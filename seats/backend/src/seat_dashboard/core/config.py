from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path
    runtime_root: Path
    seat_data_root: Path
    market_1d_root: Path
    seat_tag_config: Path
    combination_config: Path
    product_config: Path
    read_store: str = "duckdb"
    source_backend: str = "parquet"
    publish_stores: tuple[str, ...] = ("duckdb",)
    snapshot_path: Path | None = None
    demo_mode: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    duckdb_threads: int = 1
    duckdb_memory_mb: int = 256
    query_concurrency: int = 1
    cache_bytes: int = 128 * 1024 * 1024
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[4]).resolve()
        load_dotenv(root / ".env", override=False)
        def path_setting(name: str, default: str) -> Path:
            return (root / (os.getenv(name, "").strip() or default)).resolve()

        runtime = path_setting("DASHBOARD_RUNTIME_ROOT", "var")
        snapshot = os.getenv("DASHBOARD_SNAPSHOT_PATH", "").strip()
        publish = tuple(
            value.strip().lower()
            for value in os.getenv("DASHBOARD_PUBLISH_STORES", "duckdb").split(",")
            if value.strip()
        )
        if not publish:
            publish = ("duckdb",)
        cors_origins = tuple(
            value.strip().rstrip("/")
            for value in os.getenv(
                "DASHBOARD_CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if value.strip()
        )
        return cls(
            project_root=root,
            runtime_root=runtime,
            seat_data_root=path_setting("SEAT_LOCAL_ROOT", "data/futuresseatdata"),
            market_1d_root=path_setting("MARKET_1D_LOCAL_ROOT", "data/futureshistoryprices1d"),
            seat_tag_config=path_setting("SEAT_TAG_CONFIG", "config/seat-tags.csv"),
            combination_config=path_setting("SEAT_COMBINATION_CONFIG", "config/combinations.json"),
            product_config=path_setting("SEAT_PRODUCT_CONFIG", "config/products.csv"),
            read_store=os.getenv("DASHBOARD_READ_STORE", "duckdb").strip().lower(),
            source_backend=os.getenv("SEAT_SOURCE_BACKEND", "parquet").strip().lower(),
            publish_stores=publish,
            snapshot_path=(root / snapshot).resolve() if snapshot else None,
            demo_mode=_env_bool("DASHBOARD_DEMO_MODE", False),
            api_host=os.getenv("DASHBOARD_API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("DASHBOARD_API_PORT", "8000")),
            duckdb_threads=int(os.getenv("DASHBOARD_DUCKDB_THREADS", "1")),
            duckdb_memory_mb=int(os.getenv("DASHBOARD_DUCKDB_MEMORY_MB", "256")),
            query_concurrency=int(os.getenv("DASHBOARD_QUERY_CONCURRENCY", "1")),
            cache_bytes=int(os.getenv("DASHBOARD_CACHE_BYTES", str(128 * 1024 * 1024))),
            cors_origins=cors_origins,
        )

    @property
    def snapshots_root(self) -> Path:
        return self.runtime_root / "snapshots"

    @property
    def staging_root(self) -> Path:
        return self.runtime_root / "staging"

    @property
    def current_manifest_path(self) -> Path:
        return self.runtime_root / "manifests" / "current.json"

    def ensure_runtime_dirs(self) -> None:
        for path in (self.runtime_root, self.snapshots_root, self.staging_root, self.current_manifest_path.parent):
            path.mkdir(parents=True, exist_ok=True)
