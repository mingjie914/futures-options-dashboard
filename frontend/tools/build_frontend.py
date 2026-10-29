"""Prepare the frozen futures module before a Vite build.

The futures source lives in this repository. Reuse the generated copy when its
source hash is unchanged; regenerate it on first checkout or source changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath


FRONTEND_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = FRONTEND_ROOT / "src" / "futures"
MANIFEST_PATH = VENDOR_ROOT / "manifest.json"
FUTURES_SOURCE_ROOT = FRONTEND_ROOT.parent / "futures" / "frontend" / "src"
MANIFEST_VERSION = 1
TEXT_SUFFIXES = {".css", ".html", ".js", ".jsx", ".json", ".mjs", ".ts", ".tsx"}
RAW_API_PATH = re.compile(r"/api/(?!futures/v1/)")


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def aggregate(entries: list[dict[str, object]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(entry["bytes"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def source_snapshot() -> tuple[list[dict[str, object]], dict[PurePosixPath, bytes], str]:
    if not FUTURES_SOURCE_ROOT.is_dir():
        raise BuildError(f"期货源目录不存在：{FUTURES_SOURCE_ROOT}")

    entries: list[dict[str, object]] = []
    data_by_path: dict[PurePosixPath, bytes] = {}
    for path in sorted(FUTURES_SOURCE_ROOT.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"期货源包含不允许的符号链接：{path}")
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(FUTURES_SOURCE_ROOT).as_posix())
        data = path.read_bytes()
        data_by_path[relative] = data
        entries.append({"path": relative.as_posix(), "bytes": len(data), "sha256": sha256(data)})
    if not entries:
        raise BuildError(f"期货源目录为空：{FUTURES_SOURCE_ROOT}")
    return entries, data_by_path, aggregate(entries)


def transform(relative: PurePosixPath, data: bytes) -> bytes:
    if relative.suffix.lower() not in TEXT_SUFFIXES:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BuildError(f"文本文件不是UTF-8，无法稳定生成副本：{relative}") from error

    text = text.replace("/api/", "/api/futures/v1/")
    if relative.suffix.lower() == ".css":
        text = "@scope (.futures-module) {\n" + text.rstrip() + "\n}\n"
    if RAW_API_PATH.search(text):
        raise BuildError(f"副本仍包含未命名空间化的 /api/ 路径：{relative}")
    return text.encode("utf-8")


def generated_entries(
    source_entries: list[dict[str, object]], data_by_path: dict[PurePosixPath, bytes]
) -> tuple[list[dict[str, object]], dict[PurePosixPath, bytes]]:
    output_by_path: dict[PurePosixPath, bytes] = {}
    entries: list[dict[str, object]] = []
    for source_entry in source_entries:
        relative = PurePosixPath(str(source_entry["path"]))
        output = transform(relative, data_by_path[relative])
        output_by_path[relative] = output
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": len(output),
                "sha256": sha256(output),
                "source_sha256": source_entry["sha256"],
            }
        )
    return entries, output_by_path


def write_stage(
    stage_root: Path,
    source_entries: list[dict[str, object]],
    source_hash: str,
    output_entries: list[dict[str, object]],
    output_by_path: dict[PurePosixPath, bytes],
) -> None:
    for relative, data in output_by_path.items():
        destination = stage_root.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "source_root": str(FUTURES_SOURCE_ROOT),
        "source_snapshot_sha256": source_hash,
        "source_files": source_entries,
        "files": output_entries,
    }
    (stage_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def safe_relative(path_value: object) -> PurePosixPath:
    if not isinstance(path_value, str):
        raise BuildError("manifest 文件路径不是字符串")
    path = PurePosixPath(path_value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BuildError(f"manifest 文件路径越界：{path_value}")
    return path


def verify_frozen() -> dict[str, object]:
    if not MANIFEST_PATH.is_file():
        raise BuildError(
            f"冻结期货副本或manifest不存在：{VENDOR_ROOT}；首次构建请显式运行 --refresh-futures"
        )
    if VENDOR_ROOT.is_symlink():
        raise BuildError(f"期货副本不能是符号链接：{VENDOR_ROOT}")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError(f"无法读取期货副本manifest：{MANIFEST_PATH}") from error
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise BuildError("期货副本manifest版本不匹配")

    expected_entries = manifest.get("files")
    if not isinstance(expected_entries, list) or not expected_entries:
        raise BuildError("期货副本manifest缺少files")
    source_entries = manifest.get("source_files")
    if not isinstance(source_entries, list) or not source_entries:
        raise BuildError("期货副本manifest缺少source_files")
    try:
        source_hash = aggregate(source_entries)
    except (KeyError, TypeError):
        raise BuildError("期货副本manifest包含无效source_files记录") from None
    if source_hash != manifest.get("source_snapshot_sha256"):
        raise BuildError("期货副本manifest的source_snapshot_sha256不匹配")
    expected_paths: set[PurePosixPath] = set()
    for entry in expected_entries:
        if not isinstance(entry, dict):
            raise BuildError("期货副本manifest包含无效文件记录")
        relative = safe_relative(entry.get("path"))
        expected_paths.add(relative)
        candidate = VENDOR_ROOT.joinpath(*relative.parts)
        if candidate.is_symlink() or not candidate.is_file():
            raise BuildError(f"期货副本文件缺失或是符号链接：{relative}")
        data = candidate.read_bytes()
        if len(data) != entry.get("bytes") or sha256(data) != entry.get("sha256"):
            raise BuildError(f"期货副本hash不匹配：{relative}")
        if candidate.suffix.lower() in TEXT_SUFFIXES:
            text = data.decode("utf-8")
            if RAW_API_PATH.search(text):
                raise BuildError(f"期货副本存在未命名空间化的 /api/ 路径：{relative}")
            if candidate.suffix.lower() == ".css" and not text.startswith("@scope (.futures-module)"):
                raise BuildError(f"期货样式未使用.futures-module作用域：{relative}")

    actual_paths = {
        PurePosixPath(path.relative_to(VENDOR_ROOT).as_posix())
        for path in VENDOR_ROOT.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "manifest.json"
    }
    if actual_paths != expected_paths:
        missing = ", ".join(sorted(path.as_posix() for path in expected_paths - actual_paths))
        extra = ", ".join(sorted(path.as_posix() for path in actual_paths - expected_paths))
        raise BuildError(f"期货副本文件集合变化；缺少[{missing}]，多出[{extra}]")
    return manifest


def refresh_futures() -> None:
    source_entries, source_data, source_hash = source_snapshot()
    output_entries, output_data = generated_entries(source_entries, source_data)
    temporary_root = Path(tempfile.mkdtemp(prefix=".futures-refresh-", dir=FRONTEND_ROOT))
    stage_root = temporary_root / "futures"
    backup_root = FRONTEND_ROOT / f".futures-backup-{uuid.uuid4().hex}"
    failed_root = temporary_root / "failed-futures"
    replaced = False
    succeeded = False
    try:
        stage_root.mkdir()
        write_stage(stage_root, source_entries, source_hash, output_entries, output_data)

        after_entries, _, after_hash = source_snapshot()
        if after_hash != source_hash or after_entries != source_entries:
            raise BuildError("期货源在复制前后发生变化；未更新冻结副本，请重试")

        if VENDOR_ROOT.exists() or VENDOR_ROOT.is_symlink():
            VENDOR_ROOT.rename(backup_root)
        stage_root.rename(VENDOR_ROOT)
        replaced = True
        verify_frozen()
        succeeded = True
    except Exception:
        if replaced and VENDOR_ROOT.exists():
            VENDOR_ROOT.rename(failed_root)
        if backup_root.exists() and not VENDOR_ROOT.exists():
            backup_root.rename(VENDOR_ROOT)
        raise
    finally:
        if succeeded and backup_root.exists():
            shutil.rmtree(backup_root)
        if failed_root.exists():
            shutil.rmtree(failed_root)
        shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-futures",
        action="store_true",
        help="从期货项目读取一次稳定快照并更新本地冻结副本",
    )
    args = parser.parse_args()
    try:
        _, _, source_hash = source_snapshot()
        saved = json.loads(MANIFEST_PATH.read_text('utf-8')) if MANIFEST_PATH.is_file() else {}
        if args.refresh_futures or saved.get('source_snapshot_sha256') != source_hash:
            refresh_futures()
            print(f"期货副本已刷新并校验：{VENDOR_ROOT}")
        else:
            manifest = verify_frozen()
            print(
                "期货副本已冻结："
                f"{len(manifest['files'])} 个文件，"
                f"source_snapshot_sha256={manifest['source_snapshot_sha256']}"
            )
        return 0
    except BuildError as error:
        print(f"build_frontend: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
