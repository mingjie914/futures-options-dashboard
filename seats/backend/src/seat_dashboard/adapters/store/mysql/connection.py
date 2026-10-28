from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from seat_dashboard.core.config import MySQLConfig


class MySQLConnectionFactory:
    """Lazy RDS connector. Importing the adapter never opens a production connection."""

    def __init__(self, config: MySQLConfig):
        self.config = config

    @contextmanager
    def connect(self) -> Iterator[Any]:
        self.config.validate_for_connection()
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("pymysql is required for the MySQL adapter") from exc
        connection = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=self.config.connect_timeout,
            read_timeout=self.config.read_timeout,
            write_timeout=self.config.write_timeout,
            cursorclass=pymysql.cursors.DictCursor,
            ssl={"ca": str(self.config.ssl_ca), "check_hostname": True},
        )
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
