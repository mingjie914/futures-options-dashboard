from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import ClassVar

from seat_dashboard.ports.seat_source import SeatSourcePort


class ParquetSource(SeatSourcePort):
    """Read-only Parquet source backed by the existing local conversion output."""

    DATASETS: ClassVar[dict[str, str]] = {
        "product_daily": "derive_product_daily",
        "product_seat": "derive_product_seat",
        "main_contract_seat": "derive_main_contract_seat",
        "raw_seat": "raw_seat_position",
    }

    def __init__(self, seat_root: Path, market_1d_root: Path):
        self.seat_root = Path(seat_root).resolve()
        self.market_1d_root = Path(market_1d_root).resolve()

    def dataset_path(self, dataset: str) -> Path:
        if dataset == "market_1d":
            return self.market_1d_root
        try:
            return self.seat_root / self.DATASETS[dataset]
        except KeyError as exc:
            raise KeyError(f"Unknown Parquet dataset: {dataset}") from exc

    def relation_sql(self, dataset: str) -> str:
        path = self.dataset_path(dataset)
        escaped = (path / "**" / "*.parquet").as_posix().replace("'", "''")
        if not path.is_dir():
            raise FileNotFoundError(f"Dataset directory does not exist: {path}")
        return f"read_parquet('{escaped}', hive_partitioning=true)"

    def source_manifest(self) -> dict[str, object]:
        manifest_path = self.seat_root / "conversion_manifest.json"
        if not manifest_path.is_file():
            manifest_path = self.seat_root.parent / "conversion_manifest.json"
        result: dict[str, object] = {"source_backend": "parquet", "datasets": {}}
        if manifest_path.is_file():
            payload = manifest_path.read_bytes()
            result["conversion_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
            result["conversion_manifest"] = json.loads(payload)
        for dataset in (*self.DATASETS, "market_1d"):
            path = self.dataset_path(dataset)
            file_count = total_bytes = 0
            for file in path.rglob("*.parquet"):
                file_count += 1
                total_bytes += file.stat().st_size
            result["datasets"][dataset] = {
                "path": str(path),
                "files": file_count,
                "bytes": total_bytes,
            }
        return result

    def validate(self) -> None:
        missing = [str(self.dataset_path(name)) for name in (*self.DATASETS, "market_1d") if not self.dataset_path(name).is_dir()]
        if missing:
            raise FileNotFoundError("Missing local source datasets: " + "; ".join(missing))
        empty = [str(self.dataset_path(name)) for name in (*self.DATASETS, "market_1d") if not any(self.dataset_path(name).rglob("*.parquet"))]
        if empty:
            raise FileNotFoundError("Local source dataset contains no Parquet files: " + "; ".join(empty))
