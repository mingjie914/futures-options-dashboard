from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader
from seat_dashboard.main import create_app


def test_no_snapshot_is_not_presented_as_real_data(tmp_path):
    client = TestClient(create_app(reader=DuckDBReader(tmp_path)))
    assert client.get("/health/live").status_code == 200
    response = client.get("/api/v1/meta/status")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
