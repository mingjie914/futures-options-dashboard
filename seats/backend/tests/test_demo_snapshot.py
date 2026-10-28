from __future__ import annotations

from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader


def test_demo_snapshot_is_explicitly_labelled_and_readable(demo_reader: DuckDBReader):
    status = demo_reader.snapshot_status()
    assert status["complete"] is True
    assert status["data_backend"] == "demo"
    assert status["is_demo"] is True
    assert status["trade_date"].isoformat() == "2026-07-28"

    result = demo_reader.view("combination", product_code="RB.SHFE")
    assert result["selected_date"].isoformat() == "2026-07-28"
    assert result["metrics"][0]["f2_status"] == "ready"
    assert result["net_position_top"]


def test_demo_history(demo_reader: DuckDBReader):
    history = demo_reader.history("product", "RB.SHFE")
    assert history[0]["product_code"] == "RB.SHFE"
