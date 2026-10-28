from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from seat_dashboard.core.errors import SnapshotUnavailable
from seat_dashboard.ports.mart_publisher import MartPublishPort


class DuckDBPublisher(MartPublishPort):
    """Atomic publisher for immutable, versioned DuckDB snapshots."""

    def __init__(self, runtime_root: Path):
        self.runtime_root = Path(runtime_root).resolve()
        self.snapshots_root = self.runtime_root / "snapshots"
        self.staging_root = self.runtime_root / "staging"
        self.manifest_path = self.runtime_root / "manifests" / "current.json"

    def ensure_dirs(self) -> None:
        for path in (self.snapshots_root, self.staging_root, self.manifest_path.parent):
            path.mkdir(parents=True, exist_ok=True)

    def current_metadata(self) -> dict[str, Any] | None:
        if not self.manifest_path.is_file():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotUnavailable(f"Invalid current snapshot manifest: {self.manifest_path}") from exc

    def next_batch_id(self) -> int:
        current = self.current_metadata()
        if not current:
            return 1
        return int(current.get("batch_id", 0)) + 1

    def temporary_snapshot_path(self, batch_id: int) -> Path:
        self.ensure_dirs()
        return self.staging_root / f"seat_dashboard_b{batch_id:08d}.duckdb.tmp"

    def final_snapshot_path(self, batch_id: int) -> Path:
        self.ensure_dirs()
        return self.snapshots_root / f"seat_dashboard_b{batch_id:08d}.duckdb"

    def publish(self, snapshot_path: Path, metadata: dict[str, Any]) -> None:
        self.ensure_dirs()
        source = Path(snapshot_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Candidate DuckDB snapshot does not exist: {source}")
        batch_id = int(metadata["batch_id"])
        final_path = self.final_snapshot_path(batch_id)
        if source != final_path:
            os.replace(source, final_path)
        manifest = {
            **metadata,
            "batch_id": batch_id,
            "database_path": final_path.relative_to(self.runtime_root).as_posix(),
            "published_at": datetime.now(timezone.utc).isoformat(),
            "data_backend": metadata.get("data_backend", "local"),
            "complete": True,
        }
        temporary_manifest = self.manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporary_manifest, self.manifest_path)
