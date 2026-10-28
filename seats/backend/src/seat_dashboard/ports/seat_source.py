from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SeatSourcePort(Protocol):
    """Read-only access to normalized local or future RDS source datasets."""

    def dataset_path(self, dataset: str) -> Path: ...

    def relation_sql(self, dataset: str) -> str: ...

    def source_manifest(self) -> dict[str, object]: ...

    def validate(self) -> None: ...
