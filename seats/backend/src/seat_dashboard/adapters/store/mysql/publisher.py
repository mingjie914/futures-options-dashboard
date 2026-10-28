from __future__ import annotations

from pathlib import Path
from typing import Any

from seat_dashboard.adapters.store.mysql.connection import MySQLConnectionFactory


class MySQLMartPublisher:
    """Reserved RDS publishing channel; requires an explicit caller and CA-verified config."""

    def __init__(self, connection_factory: MySQLConnectionFactory):
        self.connection_factory = connection_factory

    def publish(self, snapshot_path: Path, metadata: dict[str, Any]) -> None:
        raise NotImplementedError(
            "RDS shadow publishing is intentionally gated until MySQL migrations and parity checks are approved"
        )
