from __future__ import annotations

import pytest
import shutil
from pathlib import Path

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from seat_dashboard.api import __name__ as _api_package  # noqa: F401
from seat_dashboard.core.config import Settings
from seat_dashboard.main import create_app


def test_fixed_fastapi_entry_and_page_endpoints(demo_reader):
    client = TestClient(create_app(reader=demo_reader))
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    status = client.get("/api/v1/meta/status")
    assert status.status_code == 200
    assert status.json()["meta"]["data_backend"] == "demo"
    assert status.json()["meta"]["batch_id"] is not None

    response = client.get("/api/v1/views/market-signals", params={"product_code": "RB.SHFE"})
    assert response.status_code == 200
    assert response.json()["meta"]["trade_date"] == "2026-07-28"

    term = client.get("/api/v1/views/term-structure", params={"product_code": "RB.SHFE"})
    assert term.status_code == 200
    assert term.json()["data"]["top_changes"][0]["seat_name"] == "示例席位B"

    assert client.get("/api/v1/exports/seat-rankings.csv").status_code == 404

    seat_history = client.get("/api/v1/seats/seat-a/history", params={"product_code": "RB.SHFE"})
    assert seat_history.status_code == 200
    assert seat_history.json()["data"]["entity"] == "seat"
    assert seat_history.json()["data"]["rows"][0]["seat_name"] == "示例席位A"

    all_market_history = client.get("/api/v1/seats/seat-a/history")
    assert all_market_history.status_code == 200
    assert len(all_market_history.json()["data"]["rows"]) == 1
    assert all_market_history.json()["data"]["rows"][0]["product_code"] == "ALL"
    assert all_market_history.json()["data"]["rows"][0]["net_position"] == 1200


def test_all_product_alias_and_local_cors(demo_reader):
    client = TestClient(create_app(reader=demo_reader))
    response = client.get(
        "/api/v1/views/combination",
        params={"product_code": "all"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["metrics"]) == 2
    assert len({row["seat_id"] for row in response.json()["data"]["net_position_top"]}) == 2
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unsupported_read_store_is_fail_closed(tmp_path):
    settings = Settings(
        project_root=tmp_path,
        runtime_root=tmp_path / "var",
        seat_data_root=tmp_path / "seats",
        market_1d_root=tmp_path / "market",
        seat_tag_config=tmp_path / "seat-tags.csv",
        combination_config=tmp_path / "combinations.json",
        product_config=tmp_path / "products.csv",
        read_store="mysql",
    )

    with pytest.raises(RuntimeError, match="Unsupported DASHBOARD_READ_STORE: mysql"):
        create_app(settings=settings)


def test_relocated_runtime_serves_without_source_scan(demo_runtime, tmp_path, monkeypatch):
    deployed = tmp_path / "cloud/runtime"
    shutil.move(str(demo_runtime / "snapshots"), deployed / "snapshots")
    shutil.move(str(demo_runtime / "manifests"), deployed / "manifests")
    project = tmp_path / "application"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DASHBOARD_RUNTIME_ROOT", "../cloud/runtime")
    monkeypatch.setenv("SEAT_LOCAL_ROOT", "data/not-mounted")
    monkeypatch.setenv("MARKET_1D_LOCAL_ROOT", "")
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_PATH", "")
    monkeypatch.setattr(Path, "rglob", lambda *args, **kwargs: pytest.fail("API must not scan source directories"))
    settings = Settings.from_env(project)
    assert settings.runtime_root == deployed
    assert settings.seat_data_root == project / "data/not-mounted"
    assert settings.market_1d_root == project / "data/futureshistoryprices1d"
    client = TestClient(create_app(settings=settings))
    assert client.get("/health/ready").status_code == 200
    for view in ("combination", "market-signals", "term-structure", "validation"):
        response = client.get(f"/api/v1/views/{view}")
        assert response.status_code == 200
        assert response.json()["meta"]["trade_date"] == "2026-07-28"
        assert "database_path" not in response.json()["meta"]
