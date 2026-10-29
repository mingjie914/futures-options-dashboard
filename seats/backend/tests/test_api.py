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


def test_page_details_preserve_rankings_and_skip_unneeded_sections(demo_reader, monkeypatch):
    client = TestClient(create_app(reader=demo_reader))
    base = "/api/v1/views/"
    def get(view, **params):
        response = client.get(base + view, params=params)
        assert response.status_code == 200, response.text
        return response.json()["data"]
    legacy = get("combination", product_code="RB.SHFE", limit=1)
    positions = get("combination", product_code="RB.SHFE", limit=1, detail="positions")
    for key in ("metrics", "net_position_top", "long_top", "short_top", "signals"):
        assert positions[key] == legacy[key]
    assert positions["net_position_top"][0]["net_share"] == pytest.approx(900 / 1300)
    assert positions["top_changes"][0]["product_code"] == "RB.SHFE"
    assert not positions["combinations"] and not positions["divergence_cards"]
    term = get("term-structure", product_code="RB.SHFE", limit=1)
    assert positions["top_changes"] == term["top_changes"]
    lean_term = get("term-structure", product_code="RB.SHFE", limit=1, detail="contracts")
    assert not lean_term["top_changes"]
    for key in ("contracts", "opposite_seats", "signals"):
        assert lean_term[key] == term[key]
    all_groups = get("combination")
    monkeypatch.setattr(demo_reader, "_seat_positions", lambda *a, **kw: pytest.fail("group page queried member rankings"))
    groups = get("combination", detail="groups", product_code="RB.SHFE")
    for key in ("metrics", "combinations", "combination_positions", "divergence_cards"):
        assert groups[key] == all_groups[key]
    assert not groups["net_position_top"] and not groups["long_top"] and not groups["short_top"]
    assert client.get(base + "combination", params={"detail": "positions"}).status_code == 400
    assert client.get(base + "combination", params={"detail": "invalid"}).status_code == 422


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


def test_term_contracts_include_market_only_without_filling_missing_seats(demo_reader, demo_runtime):
    import duckdb

    with duckdb.connect(str(next((demo_runtime / "snapshots").glob("*.duckdb")))) as connection:
        symbols = [f"RB27{month:02d}" for month in range(1, 13)] + ["RB2801", "RB9999"]
        connection.executemany(
            "INSERT INTO seat_dash_market_daily VALUES ('2026-07-28', 'RB.SHFE', 'SHFE', ?, 3300, 50, 10, 1, 1)",
            [(symbol,) for symbol in symbols],
        )
        connection.execute("UPDATE seat_dash_contract_daily SET total_short=total_long, net_position=0 WHERE symbol='RB2601'")
        connection.execute("UPDATE seat_dash_market_daily SET close=0 WHERE symbol='RB2801'")
        connection.execute("INSERT INTO seat_dash_market_daily VALUES ('2026-07-27', 'RB.SHFE', 'SHFE', 'RB2802', 3300, 50, 10, 1, 1)")
    client = TestClient(create_app(reader=demo_reader))
    response = client.get("/api/v1/views/term-structure", params={"product_code": "RB.SHFE", "trade_date": "2026-07-28", "detail": "contracts", "limit": 1})
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["contracts"]
    assert len(rows) == 15  # 13 market-only contracts and both disclosed seat contracts.
    assert [row["contract_rank"] for row in rows] == list(range(1, 16))
    assert [row["contract_month"] for row in rows] == sorted(row["contract_month"] for row in rows)
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["RB2601"]["net_position"] == 0
    assert by_symbol["RB2605"]["net_position"] == -400
    assert by_symbol["RB2605"]["close_price"] is None
    assert by_symbol["RB2701"]["close_price"] == 3300
    assert by_symbol["RB2701"]["price_date"] == "2026-07-28"
    assert all(by_symbol["RB2701"][key] is None for key in ("net_position", "total_long", "total_short", "seat_count"))
    assert by_symbol["RB2801"]["close_price"] is None and by_symbol["RB2801"]["price_date"] is None
    assert not {"CU2601", "RB2802", "RB9999"} & by_symbol.keys()


def test_term_legacy_czce_code_uses_only_unique_same_day_market_match(demo_reader, demo_runtime):
    import duckdb

    with duckdb.connect(str(next((demo_runtime / "snapshots").glob("*.duckdb")))) as connection:
        connection.executemany(
            "INSERT INTO seat_dash_contract_daily VALUES ('2026-07-28', 'SH.CZCE', ?, ?, ?, 1, 100, 70, 30, 1, 1)",
            [(s + ".CZCE", s, int(s[2:])) for s in ("SH611", "SH701")],
        )
        connection.executemany(
            "INSERT INTO seat_dash_market_daily VALUES ('2026-07-28', 'SH.CZCE', 'CZCE', ?, 1500, 100, 10, 1, 1)",
            [(s,) for s in ("SH2611", "SH2701", "SH3701")],
        )
    response = TestClient(create_app(reader=demo_reader)).get("/api/v1/views/term-structure", params={"product_code": "SH.CZCE"})
    assert response.status_code == 200, response.text
    rows = {row["symbol"]: row for row in response.json()["data"]["contracts"]}
    assert set(rows) == {"SH2611", "SH701", "SH2701", "SH3701"}
    assert rows["SH2611"]["net_position"] == 30 and rows["SH2611"]["close_price"] == 1500
    assert rows["SH2611"]["contract_code"] == "SH2611.CZCE"
    assert rows["SH701"]["close_price"] is None
    assert rows["SH2701"]["net_position"] is None and rows["SH3701"]["net_position"] is None
