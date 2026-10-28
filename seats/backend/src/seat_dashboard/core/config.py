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
class MySQLConfig:
    host: str = ""
    port: int = 3306
    database: str = ""
    user: str = ""
    password: str = ""
    ssl_ca: Path | None = None
    connect_timeout: int = 10
    read_timeout: int = 60
    write_timeout: int = 60

    @classmethod
    def from_env(cls) -> "MySQLConfig":
        ssl_ca = os.getenv("RDS_SSL_CA", "").strip()
        return cls(
            host=os.getenv("RDS_HOST", "").strip(),
            port=int(os.getenv("RDS_PORT", "3306")),
            database=os.getenv("RDS_DATABASE", "").strip(),
            user=os.getenv("RDS_READER_USER", "").strip(),
            password=os.getenv("RDS_READER_PASSWORD", ""),
            ssl_ca=Path(ssl_ca) if ssl_ca else None,
            connect_timeout=int(os.getenv("RDS_CONNECT_TIMEOUT", "10")),
            read_timeout=int(os.getenv("RDS_READ_TIMEOUT", "60")),
            write_timeout=int(os.getenv("RDS_WRITE_TIMEOUT", "60")),
        )

    @classmethod
    def writer_from_env(cls) -> "MySQLConfig":
        """Build a separate writer credential config without connecting to RDS."""
        reader = cls.from_env()
        return cls(
            host=reader.host,
            port=reader.port,
            database=reader.database,
            user=os.getenv("RDS_WRITER_USER", "").strip(),
            password=os.getenv("RDS_WRITER_PASSWORD", ""),
            ssl_ca=reader.ssl_ca,
            connect_timeout=reader.connect_timeout,
            read_timeout=reader.read_timeout,
            write_timeout=reader.write_timeout,
        )

    def validate_for_connection(self) -> None:
        missing = [
            name
            for name, value in (
                ("RDS_HOST", self.host),
                ("RDS_DATABASE", self.database),
                ("RDS_READER_USER", self.user),
                ("RDS_READER_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"MySQL connection is not configured: {', '.join(missing)}")
        if self.ssl_ca is None or not self.ssl_ca.is_file():
            raise RuntimeError("RDS_SSL_CA must point to an existing CA certificate")


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
    mysql: MySQLConfig = MySQLConfig()

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
            mysql=MySQLConfig.from_env(),
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
