from __future__ import annotations

from datetime import date
from typing import Any

from seat_dashboard.adapters.store.mysql.connection import MySQLConnectionFactory


class MySQLDashboardReader:
    """RDS read channel with the same page-level contract as DuckDBReader.

    V1 keeps this adapter behind a fail-closed application-factory gate. Page SQL
    must be completed and shadow-validated before the gate is removed.
    """

    def __init__(self, connection_factory: MySQLConnectionFactory):
        self.connection_factory = connection_factory

    def snapshot_status(self) -> dict[str, Any]:
        with self.connection_factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT batch_id, trade_date, published_at, data_backend, complete, metadata_json "
                    "FROM seat_dash_publish_state WHERE dataset_name = %s",
                    ("dashboard",),
                )
                row = cursor.fetchone()
        if not row:
            return {"batch_id": None, "trade_date": None, "data_backend": "mysql", "complete": False}
        return {
            "batch_id": row["batch_id"],
            "trade_date": row["trade_date"],
            "updated_at": row["published_at"],
            "data_backend": row["data_backend"],
            "complete": bool(row["complete"]),
            "is_demo": False,
        }

    def options(self) -> dict[str, Any]:
        with self.connection_factory.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT product_code, root, exchange, display_name FROM seat_dash_product_dim ORDER BY product_code")
                products = list(cursor.fetchall())
                cursor.execute(
                    "SELECT c.combination_id, c.name, c.scope, COUNT(m.seat_id) AS member_count "
                    "FROM seat_dash_combination c LEFT JOIN seat_dash_combination_member m "
                    "ON m.combination_id = c.combination_id WHERE c.active "
                    "GROUP BY c.combination_id, c.name, c.scope ORDER BY c.name"
                )
                combinations = list(cursor.fetchall())
                cursor.execute("SELECT tag_code, label, category, color_token, description FROM seat_dash_tag_definition ORDER BY category, label")
                tags = list(cursor.fetchall())
                cursor.execute("SELECT MAX(trade_date) AS trade_date FROM seat_dash_product_daily")
                latest = cursor.fetchone()
        return {"trade_date": latest["trade_date"] if latest else None, "products": products, "combinations": combinations, "tags": tags}

    def view(self, view_name: str, trade_date: date | None = None, product_code: str | None = None, scope: str = "all", limit: int = 20, min_leg_position: int = 300) -> dict[str, Any]:
        raise NotImplementedError(
            "RDS page SQL is enabled after shadow validation; use DuckDBReader for the local-first V1 runtime"
        )

    def history(self, entity: str, entity_id: str, product_code: str | None = None, start_date: date | None = None, end_date: date | None = None, limit: int = 500) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "RDS history SQL is enabled after shadow validation; use DuckDBReader for the local-first V1 runtime"
        )
