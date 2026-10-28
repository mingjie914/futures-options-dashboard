from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from seat_dashboard.adapters.store.duckdb.publisher import DuckDBPublisher


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "adapters" / "store" / "duckdb" / "schema.sql"


def create_demo_snapshot(runtime_root: Path, batch_id: int | None = None) -> dict[str, Any]:
    """Create an explicitly labelled demo snapshot for API tests and local wiring."""
    publisher = DuckDBPublisher(runtime_root)
    publisher.ensure_dirs()
    selected_batch = batch_id or publisher.next_batch_id()
    temporary = publisher.temporary_snapshot_path(selected_batch)
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect(str(temporary))
    try:
        connection.execute(_schema_path().read_text(encoding="utf-8"))
        connection.executemany(
            "INSERT INTO seat_dash_product_dim VALUES (?, ?, ?, ?, ?)",
            [("RB.SHFE", "RB", "SHFE", "RB.SHFE", True), ("CU.SHFE", "CU", "SHFE", "CU.SHFE", True)],
        )
        connection.executemany(
            "INSERT INTO seat_dash_seat_dim VALUES (?, ?, ?)",
            [("seat-a", "示例席位A", True), ("seat-b", "示例席位B", True)],
        )
        connection.executemany(
            """INSERT INTO seat_dash_tag_definition
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("reversal", "多空反转", "dynamic", "warning", "示例反转", True),
                ("tracked_seat", "重点跟踪", "static", "highlight", "示例标签", False),
            ],
        )
        connection.executemany(
            "INSERT INTO seat_dash_combination VALUES (?, ?, ?, ?, ?)",
            [("all_market", "全市场席位", "all", True, True), ("custom_empty", "示例组合", "custom", False, True)],
        )
        connection.executemany(
            """INSERT INTO seat_dash_product_seat_daily (
                   trade_date, product_code, seat_id, seat_name,
                   long_position, long_change, short_position, short_change,
                   net_position, net_change, abs_net_position, abs_net_change,
                   previous_net_position, net_position_change, entry_state,
                   contract_count, batch_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "seat-a", "示例席位A", 1200, 400, 300, -100, 900, 500, 900, 500, 400, 500, "existing", 2, selected_batch),
                (date(2026, 7, 28), "RB.SHFE", "seat-b", "示例席位B", 500, -800, 900, 600, -400, -1400, 400, 1400, 1000, -1400, "reversal", 2, selected_batch),
                (date(2026, 7, 28), "CU.SHFE", "seat-a", "示例席位A", 600, 50, 300, -10, 300, 60, 300, 60, 240, 60, "existing", 1, selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_product_daily (
                   trade_date, product_code, root, exchange,
                   total_long, total_short, net_position,
                   long_change, short_change, net_change, seat_count,
                   long_short_ratio, long_cr5, short_cr5, net_cr5,
                   divergence_state, f2_value, f2_status, batch_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "RB", "SHFE", 1700, 1200, 500, -400, 500, -900, 2, 1.4166667, 0.7058824, 0.75, 0.6923077, "unavailable", 0.1724138, "ready", selected_batch),
                (date(2026, 7, 28), "CU.SHFE", "CU", "SHFE", 600, 300, 300, 50, -10, 60, 1, 2.0, 1.0, 1.0, 1.0, "unavailable", 0.3333333, "ready", selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_main_contract_seat_daily
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "RB2601.SHFE", "RB2601", "seat-a", "示例席位A", 1200, 400, 300, -100, 900, 500, 3200.0, 100000, selected_batch),
                (date(2026, 7, 28), "RB.SHFE", "RB2601.SHFE", "RB2601", "seat-b", "示例席位B", 500, -800, 900, 600, -400, -1400, 3200.0, 100000, selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_contract_daily
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "RB2601.SHFE", "RB2601", 2601, 1, 1700, 1200, 500, 2, selected_batch),
                (date(2026, 7, 28), "RB.SHFE", "RB2605.SHFE", "RB2605", 2605, 2, 900, 1300, -400, 2, selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_contract_seat_daily (
                   trade_date, product_code, contract_code, symbol, contract_rank,
                   seat_id, seat_name, long_position, short_position, net_position, batch_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "RB2601.SHFE", "RB2601", 1, "seat-a", "示例席位A", 1200, 300, 900, selected_batch),
                (date(2026, 7, 28), "RB.SHFE", "RB2605.SHFE", "RB2605", 2, "seat-a", "示例席位A", 100, 600, -500, selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_market_daily
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "SHFE", "RB2601", 3200.0, 100000, 50000, 1, selected_batch),
                (date(2026, 7, 28), "CU.SHFE", "SHFE", "CU2601", 78000.0, 20000, 10000, 1, selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_seat_signal_daily
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "seat-b", "示例席位B", "reversal", "short", "high", -400.0, 300.0, 0.9, '{"demo":true}', "signals-v1", selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_term_signal_daily (
                   trade_date, product_code, near_contract_code, far_contract_code,
                   seat_id, seat_name, near_net_position, far_net_position, direction,
                   min_leg_position, leg_count, legs_json, severity, confidence,
                   evidence_json, rule_version, batch_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "RB2601.SHFE", "RB2605.SHFE", "seat-a", "示例席位A", 900, -500, "near_long_far_short", 300, 2, '[{"contract_code":"RB2601.SHFE","symbol":"RB2601","contract_rank":1,"net_position":900},{"contract_code":"RB2605.SHFE","symbol":"RB2605","contract_rank":2,"net_position":-500}]', "medium", 0.7, '{"demo":true}', "signals-v2-reference", selected_batch),
            ],
        )
        connection.executemany(
            """INSERT INTO seat_dash_rule_result_daily (
                   trade_date, product_code, rule_code, horizon_days, signal_direction,
                   current_percentile, sample_size, mean_return, median_return, win_rate,
                   confidence_low, confidence_high, band, signal_label, confidence_label,
                   library_cutoff, status, return_basis, rule_version, batch_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (date(2026, 7, 28), "RB.SHFE", "net_cr5_concentration", 5, "long", 72.0, 18, 0.021, 0.018, 0.611, 0.39, 0.80, "high", "偏多", "low", date(2026, 6, 23), "sample_insufficient", "demo_fixture", "signals-v2-reference", selected_batch),
                (date(2026, 7, 28), "RB.SHFE", "f2_position_ratio", 5, "neutral", 22.0, 0, None, None, None, None, None, "low", "规律不可用", "unavailable", date(2026, 6, 23), "unavailable", "demo_fixture", "signals-v2-reference", selected_batch),
            ],
        )
        metadata = {
            "batch_id": selected_batch,
            "trade_date": "2026-07-28",
            "date_min": "2026-07-28",
            "date_max": "2026-07-28",
            "mode": "demo",
            "metric_version": "metrics-v2-reference",
            "rule_version": "signals-v2-reference",
            "source_manifest_sha256": "demo-fixture",
            "row_counts": {"demo": 1},
            "validation": {"complete": True, "warnings": ["demo_fixture"]},
            "warnings": ["demo_fixture"],
            "data_backend": "demo",
            "is_demo": True,
        }
        connection.execute(
            "INSERT INTO seat_dash_etl_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            [selected_batch, datetime.now(timezone.utc), datetime.now(timezone.utc), "validated", "demo", "demo-fixture", date(2026, 7, 28), date(2026, 7, 28), json.dumps({"demo": 1}), json.dumps(metadata)],
        )
        connection.execute(
            "INSERT INTO seat_dash_publish_state VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["dashboard", selected_batch, date(2026, 7, 28), datetime.now(timezone.utc), "demo", True, json.dumps(metadata)],
        )
        connection.commit()
    finally:
        connection.close()
    publisher.publish(temporary, metadata)
    metadata["database_path"] = str(publisher.final_snapshot_path(selected_batch))
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an explicitly labelled demo snapshot")
    parser.add_argument("--runtime-root", type=Path, default=Path("var"))
    args = parser.parse_args(argv)
    result = create_demo_snapshot(args.runtime_root.resolve())
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
