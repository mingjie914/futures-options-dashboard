"""Explicit, dry-run-first runtime retention; never traverses source datasets."""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path


def prune_runtime(runtime_root: Path, *, apply: bool = False, staging_limit: int = 1, min_age_hours: float = 24) -> dict:
    root = runtime_root.resolve(strict=True)
    if staging_limit < 0 or min_age_hours < 0:
        raise ValueError("retention limits must be nonnegative")
    manifest_path = root / "manifests/current.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    current = (root / manifest["database_path"]).resolve(strict=True)
    snapshots = root / "snapshots"
    staging = root / "staging"
    if not manifest.get("complete") or not current.is_file() or current.parent != snapshots or snapshots.is_symlink() or staging.is_symlink():
        raise ValueError("current snapshot must be complete and inside the runtime snapshots directory")
    snapshot_files = sorted(
        (path for path in snapshots.iterdir() if re.fullmatch(r"seat_dashboard_b\d{8}\.duckdb", path.name)),
        reverse=True,
    )
    previous = next((path for path in snapshot_files if path.name < current.name), None)
    keep = {current, previous}
    # A newer final file may belong to a publication in progress; leave it alone.
    candidates = [path for path in snapshot_files if path not in keep and path.name < current.name]
    staging_files = sorted(
        (path for path in staging.glob("seat_dashboard_b*.duckdb.tmp") if re.fullmatch(r"seat_dashboard_b\d{8}\.duckdb\.tmp", path.name)),
        key=lambda path: path.stat().st_mtime_ns, reverse=True,
    )
    candidates.extend(staging_files[staging_limit:])
    removable, protected = [], []
    cutoff = time.time() - min_age_hours * 3600
    for path in candidates:
        if path.is_symlink() or path.resolve().parent not in (snapshots, staging) or not path.is_file():
            raise ValueError("refusing retention target outside the runtime")
        (removable if path.stat().st_mtime <= cutoff else protected).append(path)
    result = {
        "applied": apply,
        "keep": sorted(str(path.relative_to(root)) for path in keep if path),
        "delete": [str(path.relative_to(root)) for path in removable],
        "protected_recent": [str(path.relative_to(root)) for path in protected],
        "reclaim_bytes": sum(path.stat().st_size for path in removable),
        "staging_limit": staging_limit,
    }
    if apply:
        # The CLI is for a maintenance window with builders stopped.
        for path in removable:
            if manifest_path.read_bytes() != manifest_bytes:
                raise RuntimeError("publication changed during cleanup; stop and re-plan")
            if path.is_symlink() or path.resolve().parent not in (snapshots, staging):
                raise ValueError("retention target changed during cleanup")
            path.unlink()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Delete listed files; stop snapshot jobs first")
    parser.add_argument("--staging-limit", type=int, default=1)
    parser.add_argument("--min-age-hours", type=float, default=24)
    args = parser.parse_args(argv)
    print(json.dumps(prune_runtime(args.runtime_root, apply=args.apply, staging_limit=args.staging_limit, min_age_hours=args.min_age_hours), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
