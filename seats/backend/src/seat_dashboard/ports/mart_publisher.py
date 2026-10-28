from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class MartPublishPort(Protocol):
    """Publish an already validated batch without exposing partial results."""

    def publish(self, snapshot_path: Path, metadata: dict[str, Any]) -> None: ...
