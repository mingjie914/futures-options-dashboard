from __future__ import annotations

from datetime import date
from contextlib import AbstractContextManager
from typing import Any, Protocol


class DashboardReadPort(Protocol):
    """Page-oriented snapshot read contract."""

    def snapshot_status(self) -> dict[str, Any]: ...

    def read_session(self) -> AbstractContextManager[str]: ...

    def options(self) -> dict[str, Any]: ...

    def view(
        self,
        view_name: str,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = 20,
        min_leg_position: int = 300,
    ) -> dict[str, Any]: ...

    def history(
        self,
        entity: str,
        entity_id: str,
        product_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...
