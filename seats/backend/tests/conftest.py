from __future__ import annotations

from pathlib import Path

import pytest

from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader
from seat_dashboard.demo import create_demo_snapshot


@pytest.fixture()
def demo_runtime(tmp_path: Path) -> Path:
    create_demo_snapshot(tmp_path)
    return tmp_path


@pytest.fixture()
def demo_reader(demo_runtime: Path) -> DuckDBReader:
    return DuckDBReader(demo_runtime)
