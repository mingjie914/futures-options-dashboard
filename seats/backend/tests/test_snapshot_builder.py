from __future__ import annotations

import json
import shutil
import hashlib
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import duckdb
import pytest

from seat_dashboard.adapters.source.parquet_source import ParquetSource
from seat_dashboard.adapters.store.duckdb.publisher import DuckDBPublisher
from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader
from seat_dashboard.jobs.build_snapshot import BuildRequest, SnapshotBuilder
from seat_dashboard.jobs.verify_snapshot import verify_current, main as verify_main
from seat_dashboard.core.errors import SnapshotValidationError


def _write_dataset(root: Path, name: str, rows: list[dict]) -> None:
    path = root / name / "year_month=2026-07"
    path.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path / "part-000.parquet")


def _fixture_source(tmp_path: Path) -> tuple[Path, Path]:
    seat_root = tmp_path / "futuresseatdata"
    market_root = tmp_path / "futureshistoryprices1d"
    rows_product = [
        {"trade_date": date(2026, 7, 27), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "total_long": 1000, "total_short": 800, "net_position": 200, "long_change": 100, "short_change": 50, "seat_count": 2},
        {"trade_date": date(2026, 7, 28), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "total_long": 1200, "total_short": 700, "net_position": 500, "long_change": 200, "short_change": -100, "seat_count": 2},
    ]
    rows_seat = [
        {"trade_date": date(2026, 7, 27), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "seat_name": "席位A", "long_position": 700, "long_change": 50, "short_position": 100, "short_change": 0, "net_position": 600, "contract_count": 2},
        {"trade_date": date(2026, 7, 27), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "seat_name": "席位B", "long_position": 300, "long_change": 50, "short_position": 700, "short_change": 50, "net_position": -400, "contract_count": 2},
        {"trade_date": date(2026, 7, 28), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "seat_name": "席位A", "long_position": 900, "long_change": 200, "short_position": 100, "short_change": 0, "net_position": 800, "contract_count": 2},
        {"trade_date": date(2026, 7, 28), "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "seat_name": "席位B", "long_position": 300, "long_change": 0, "short_position": 600, "short_change": -100, "net_position": -300, "contract_count": 2},
    ]
    rows_main = [
        {"trade_date": row["trade_date"], "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "contract_code": "RB2601.SHFE", "symbol": "RB2601", "seat_name": row["seat_name"], "long_position": row["long_position"], "long_change": row["long_change"], "short_position": row["short_position"], "short_change": row["short_change"], "net_position": row["net_position"], "close": 3200.0 if row["trade_date"] == date(2026, 7, 28) else 3150.0, "open_interest": 10000}
        for row in rows_seat
    ]
    rows_raw = []
    for row in rows_seat:
        for contract, net_factor in (("RB2601.SHFE", 1), ("RB2605.SHFE", -1)):
            long_position = max(row["long_position"] // 2, 0)
            short_position = max(row["short_position"] // 2, 0)
            if net_factor < 0:
                long_position, short_position = short_position, long_position
            rows_raw.append({"trade_date": row["trade_date"], "exchange": "SHFE", "root": "RB", "product_code": "RB.SHFE", "contract_code": contract, "symbol": contract.split(".")[0], "seat_name": row["seat_name"], "is_aggregated": 0, "record_grain": "合约级", "long_position": long_position, "long_change": 0, "short_position": short_position, "short_change": 0, "net_position": long_position - short_position})
    market_rows = [
        {"exchange": "SHFE", "symbol": "RB2601", "trade_datetime": datetime(2026, 7, 27), "open": 3150.0, "high": 3160.0, "low": 3140.0, "close": 3150.0, "volume": 100, "amount": 1.0, "position": 9000, "type": 14, "sequence": 1, "trade_date": date(2026, 7, 27), "settle_price": 3150.0, "pre_settle_price": 3140.0},
        {"exchange": "SHFE", "symbol": "RB2601", "trade_datetime": datetime(2026, 7, 28), "open": 3200.0, "high": 3210.0, "low": 3190.0, "close": 3200.0, "volume": 100, "amount": 1.0, "position": 10000, "type": 14, "sequence": 1, "trade_date": date(2026, 7, 28), "settle_price": 3200.0, "pre_settle_price": 3150.0},
    ]
    _write_dataset(seat_root, "derive_product_daily", rows_product)
    _write_dataset(seat_root, "derive_product_seat", rows_seat)
    _write_dataset(seat_root, "derive_main_contract_seat", rows_main)
    _write_dataset(seat_root, "raw_seat_position", rows_raw)
    _write_dataset(market_root, "market", market_rows)
    return seat_root, market_root


def test_builder_dry_run_does_not_publish(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    publisher = DuckDBPublisher(tmp_path / "runtime")
    metadata = SnapshotBuilder(ParquetSource(seat_root, market_root), publisher).build(
        BuildRequest(trade_date=date(2026, 7, 28), dry_run=True)
    )
    assert metadata["dry_run"] is True
    assert metadata["validation"]["complete"] is True
    assert not (tmp_path / "runtime" / "manifests" / "current.json").exists()


def test_builder_publishes_atomic_snapshot_and_reader_can_query(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    runtime = tmp_path / "runtime"
    publisher = DuckDBPublisher(runtime)
    metadata = SnapshotBuilder(ParquetSource(seat_root, market_root), publisher).build(
        BuildRequest(trade_date=date(2026, 7, 28))
    )
    assert metadata["batch_id"] == 1
    assert (runtime / "manifests" / "current.json").is_file()
    reader = DuckDBReader(runtime)
    assert reader.snapshot_status()["complete"] is True
    assert verify_current(runtime)["valid"] is True
    result = reader.view("combination", product_code="RB.SHFE")
    assert result["metrics"][0]["f2_status"] == "ready"
    assert result["metrics"][0]["net_position"] == 500
    term_result = reader.view("term-structure", product_code="RB.SHFE")
    assert term_result["contracts"][0]["contract_month"] == 2601
    assert term_result["contracts"][0]["close_price"] == 3200.0
    assert term_result["contracts"][0]["price_date"] == date(2026, 7, 28)
    assert term_result["contracts"][1]["close_price"] is None
    assert term_result["contracts"][1]["price_date"] is None

    # A deployed runtime must survive relocation without its builder or source data.
    manifest = json.loads((runtime / "manifests/current.json").read_text(encoding="utf-8"))
    assert manifest["database_path"] == "snapshots/seat_dashboard_b00000001.duckdb"
    deployed = tmp_path / "cloud-runtime"
    shutil.move(str(runtime), deployed)
    shutil.rmtree(seat_root)
    shutil.rmtree(market_root)
    assert verify_current(deployed)["valid"] is True
    assert DuckDBReader(deployed).view("combination", product_code="RB.SHFE") == result
    exported = tmp_path / "exported"
    assert verify_main(["--runtime-root", str(deployed), "--export-root", str(exported)]) == 0
    portable = json.loads((exported / "manifests/current.json").read_text(encoding="utf-8"))
    assert "source_manifest" not in portable
    assert not Path(portable["database_path"]).is_absolute()
    portable["database_sha256"] = "0" * 64
    (exported / "manifests/current.json").write_text(json.dumps(portable), encoding="utf-8")
    assert verify_current(exported)["valid"] is False


def test_market_interest_rate_excludes_synthetic_contracts_and_requires_prior_date(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    path = market_root / "market/year_month=2026-07/part-000.parquet"
    quotes = pq.ParquetFile(path).read().to_pylist()
    quotes += [{**row, "symbol": "RB2605", "position": 1000 if row["trade_date"].day == 27 else 2000} for row in quotes.copy()]
    quotes += [{**quotes[0], "symbol": f"RB{suffix}", "position": 99999999} for suffix in ("8888", "9998", "9999")]
    pq.write_table(pa.Table.from_pylist(quotes), path)
    runtime = tmp_path / "runtime"
    SnapshotBuilder(ParquetSource(seat_root, market_root), DuckDBPublisher(runtime)).build(BuildRequest())
    reader = DuckDBReader(runtime)
    row = reader.view("market-signals", product_code="RB.SHFE")["treemap"][0]
    assert row["open_interest"] == 12000
    assert row["previous_open_interest"] == 10000
    assert abs(row["open_interest_change_rate"] - 0.2) < 1e-12
    assert row["previous_interest_date"] == date(2026, 7, 27)
    assert row["interest_contract_count"] == row["previous_interest_contract_count"] == 2
    assert row["interest_contracts_changed"] is False
    earliest = reader.view("market-signals", product_code="RB.SHFE", trade_date=date(2026, 7, 27))["treemap"][0]
    assert earliest["open_interest_change_rate"] is None


def test_builder_fills_dates_newer_than_derived_tables_from_raw(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    raw_increment = [
        {
            "trade_date": date(2026, 8, 3), "exchange": "SHFE", "root": "RB",
            "product_code": "RB.SHFE", "contract_code": "RB2601.SHFE", "symbol": "RB2601",
            "seat_name": "席位A", "is_aggregated": 0, "record_grain": "合约级",
            "long_position": 700, "long_change": 100, "short_position": 100, "short_change": 0,
            "net_position": 600,
        },
        {
            "trade_date": date(2026, 8, 3), "exchange": "SHFE", "root": "RB",
            "product_code": "RB.SHFE", "contract_code": "RB2605.SHFE", "symbol": "RB2605",
            "seat_name": "席位A", "is_aggregated": 0, "record_grain": "合约级",
            "long_position": 200, "long_change": 0, "short_position": 0, "short_change": 0,
            "net_position": 200,
        },
        {
            "trade_date": date(2026, 8, 3), "exchange": "SHFE", "root": "RB",
            "product_code": "RB.SHFE", "contract_code": "RB2601.SHFE", "symbol": "RB2601",
            "seat_name": "席位B", "is_aggregated": 0, "record_grain": "合约级",
            "long_position": 100, "long_change": 0, "short_position": 600, "short_change": 100,
            "net_position": -500,
        },
    ]
    increment_path = seat_root / "raw_seat_position" / "year_month=2026-08"
    increment_path.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(raw_increment), increment_path / "part-000.parquet")

    runtime = tmp_path / "runtime-with-raw-increment"
    metadata = SnapshotBuilder(
        ParquetSource(seat_root, market_root), DuckDBPublisher(runtime)
    ).build(BuildRequest())

    assert metadata["trade_date"] == "2026-08-03"
    result = DuckDBReader(runtime).view("combination", product_code="RB.SHFE")
    assert result["selected_date"] == date(2026, 8, 3)
    assert result["metrics"][0]["total_long"] == 1000
    assert result["metrics"][0]["total_short"] == 700
    seat_a = next(row for row in result["net_position_top"] if row["seat_name"] == "席位A")
    assert seat_a["long_position"] == 900
    term_result = DuckDBReader(runtime).view("term-structure", product_code="RB.SHFE")
    assert term_result["selected_date"] == date(2026, 8, 3)
    assert len(term_result["contracts"]) == 2
    # Historical/main-contract prices must not fill a missing same-day quote.
    assert all(row["close_price"] is None and row["price_date"] is None for row in term_result["contracts"])
    validation_result = DuckDBReader(runtime).view("validation", product_code="RB.SHFE")
    assert len(validation_result["rules"]) == 4


def test_governed_static_tag_config_is_published_and_returned(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    tag_config = tmp_path / "seat-tags.csv"
    tag_config.write_text(
        "seat_name,tag_code,product_code,valid_from,valid_to,source,confidence,evidence,note\n"
        "席位A,capital_seat,RB.SHFE,2026-01-01,2026-12-31,人工研究台账,0.90,经审批的测试标签,仅测试\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime-with-tags"
    metadata = SnapshotBuilder(
        ParquetSource(seat_root, market_root),
        DuckDBPublisher(runtime),
        tag_config,
    ).build(BuildRequest(trade_date=date(2026, 7, 28)))
    assert metadata["row_counts"]["seat_dash_seat_tag_assignment"] == 1
    result = DuckDBReader(runtime).view("combination", product_code="RB.SHFE")
    seat_a = next(row for row in result["net_position_top"] if row["seat_name"] == "席位A")
    assert seat_a["tags"][0]["name"] == "资金席位"
    assert seat_a["tags"][0]["source"] == "人工研究台账"


def test_combination_config_validates_members_and_publishes_counts(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    combination_config = tmp_path / "combinations.json"
    combination_config.write_text(
        '[{"id":"test_watch","name":"测试观察组合","scope":"system",'
        '"isSystem":true,"active":true,"members":["席位A"]}]',
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime-with-combination"
    SnapshotBuilder(
        ParquetSource(seat_root, market_root),
        DuckDBPublisher(runtime),
        None,
        combination_config,
    ).build(BuildRequest(trade_date=date(2026, 7, 28)))
    options = DuckDBReader(runtime).options()
    configured = next(
        row for row in options["combinations"] if row["combination_id"] == "test_watch"
    )
    assert configured["member_count"] == 1


def test_reclassify_frozen_snapshot_preserves_facts_and_recalculates_divergence(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    path = seat_root / "derive_product_seat/year_month=2026-07/part-000.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[-1]["long_change"] = -200  # B is short and adds short; A is long and adds long.
    pq.write_table(pa.Table.from_pylist(rows), path)
    source = ParquetSource(seat_root, market_root)
    runtime = tmp_path / "original"
    SnapshotBuilder(source, DuckDBPublisher(runtime)).build(BuildRequest())
    manifest_path = runtime / "manifests/current.json"
    original_manifest = manifest_path.read_bytes()
    original_db = runtime / json.loads(original_manifest)["database_path"]
    original_hash = hashlib.sha256(original_db.read_bytes()).hexdigest()
    shutil.rmtree(seat_root)
    shutil.rmtree(market_root)  # Reclassification must not access the original data sources.
    config = tmp_path / "groups.json"
    groups = [
        {"id": "wolong_fengchu", "name": "家人席位", "members": ["席位B"]},
        {"id": "industrial_major", "name": "产业大户", "members": ["席位A", "席位B"]},
        {"id": "capital_strike", "name": "投机交易", "members": ["席位A"]},
        {"id": "brokerage_proprietary", "name": "券商自营", "members": ["席位A"]},
    ]
    config.write_text(json.dumps(groups), encoding="utf-8")
    target = tmp_path / "preview"
    builder = SnapshotBuilder(source, DuckDBPublisher(target), combination_config=config)
    metadata = builder.reclassify(runtime)
    assert metadata["base_batch_id"] == 1 and metadata["batch_id"] == 2
    assert verify_current(target)["valid"]
    result = DuckDBReader(target).view("combination", product_code="RB.SHFE")
    assert [row["name"] for row in result["combinations"]] == ["产业大户", "券商自营", "投机交易", "家人席位"]
    assert result["divergence_pair"] == ["投机交易", "家人席位"]
    card = next(card for card in result["divergence_cards"] if card["count"])
    assert card["label"] == "投机加多 · 家人加空" and card["count"] == 1
    assert card["products"][0]["product_code"] == "RB.SHFE"
    signals = {row["combination_id"]: row for row in result["combination_signals"]}
    assert "industrial_major" not in signals  # Opposite members do not produce a group signal.
    assert signals["capital_strike"]["net_position"] == signals["brokerage_proprietary"]["net_position"] == 800
    assert signals["wolong_fengchu"]["net_change"] == -100
    new_db = target / DuckDBPublisher(target).current_metadata()["database_path"]
    with duckdb.connect(str(original_db), read_only=True) as old, duckdb.connect(str(new_db), read_only=True) as new:
        changed = {"seat_dash_combination", "seat_dash_combination_member", "seat_dash_combination_signal_daily", "seat_dash_etl_run", "seat_dash_publish_state"}
        for (table,) in old.execute("SHOW TABLES").fetchall():
            if table not in changed:
                query = f'SELECT * FROM "{table}" ORDER BY ALL'
                assert old.execute(query).fetchall() == new.execute(query).fetchall(), table
    # Valid groups can have zero same-direction signals; do not keep stale signals or reject publication.
    config.write_text(json.dumps([groups[1]]), encoding="utf-8")
    builder.reclassify(target)
    empty = DuckDBReader(target).view("combination")
    assert empty["combination_signals"] == [] and empty["divergence_cards"] == []
    assert manifest_path.read_bytes() == original_manifest
    assert hashlib.sha256(original_db.read_bytes()).hexdigest() == original_hash
    last_good = (target / "manifests/current.json").read_bytes()
    config.write_text('[{"id":"invalid","name":"未知","members":["不存在"]}]', encoding="utf-8")
    with pytest.raises(SnapshotValidationError, match="unknown member"):
        builder.reclassify(target)
    assert (target / "manifests/current.json").read_bytes() == last_good
    assert not list((target / "staging").iterdir())
    broken = {**json.loads(original_manifest), "database_sha256": "0" * 64}
    manifest_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(SnapshotValidationError, match="checksum mismatch"):
        builder.reclassify(runtime)
    assert not list((target / "staging").iterdir())


def test_group_positions_include_mixed_balanced_and_missing_members(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    seat_path = seat_root / "derive_product_seat/year_month=2026-07/part-000.parquet"
    rows = pq.read_table(seat_path).to_pylist()
    rows = [row for row in rows if not (row["seat_name"] == "席位B" and row["trade_date"].day == 27)]
    rows[-1].update(short_position=1100, net_position=-800)
    pq.write_table(pa.Table.from_pylist(rows), seat_path)
    config = tmp_path / "groups.json"
    config.write_text(json.dumps([
        {"id": "industrial_major", "name": "混合组", "members": ["席位A", "席位B"]},
        {"id": "capital_strike", "name": "投机交易", "members": ["席位A"]},
        {"id": "wolong_fengchu", "name": "家人席位", "members": ["席位B"]},
    ]), encoding="utf-8")
    runtime = tmp_path / "runtime"
    SnapshotBuilder(ParquetSource(seat_root, market_root), DuckDBPublisher(runtime), combination_config=config).build(BuildRequest())
    reader = DuckDBReader(runtime)
    result = reader.view("combination", product_code="RB.SHFE")
    positions = {row["combination_id"]: row for row in result["combination_positions"]}
    mixed = positions["industrial_major"]
    assert mixed["net_position"] == 0 and mixed["net_change"] == 300
    assert (mixed["long_count"], mixed["short_count"], mixed["flat_count"], mixed["missing_count"]) == (1, 1, 0, 0)
    assert all(row["combination_id"] != "industrial_major" for row in result["combination_signals"])
    earlier = reader.view("combination", trade_date=date(2026, 7, 27))
    partial = {row["combination_id"]: row for row in earlier["combination_positions"]}
    assert partial["industrial_major"]["missing_count"] == 1
    assert partial["industrial_major"]["net_position"] == 600
    assert partial["wolong_fengchu"]["missing_count"] == 1
    assert partial["wolong_fengchu"]["net_position"] is None
    assert partial["wolong_fengchu"]["net_change"] is None


@pytest.mark.parametrize("historical_only", [False, True])
def test_group_rename_connects_history_without_rewriting_facts(tmp_path: Path, historical_only: bool):
    seat_root, market_root = _fixture_source(tmp_path)
    path = seat_root / "derive_product_seat/year_month=2026-07/part-000.parquet"
    rows = pq.ParquetFile(path).read().to_pylist()
    for row in rows:
        if row["seat_name"] == "席位A":
            row["seat_name"] = "乾坤期货" if historical_only or row["trade_date"].day == 27 else "高盛期货"
    pq.write_table(pa.Table.from_pylist(rows), path)
    source = ParquetSource(seat_root, market_root)
    config = tmp_path / "groups.json"
    group = {"id": "foreign_seats", "name": "外资席位", "members": ["高盛期货"]}
    config.write_text(json.dumps([group]), encoding="utf-8")
    original = tmp_path / "original"
    SnapshotBuilder(source, DuckDBPublisher(original)).build(BuildRequest())
    # Also cover the normal build path, including historical sources with no new name yet.
    fresh = tmp_path / "fresh"
    SnapshotBuilder(source, DuckDBPublisher(fresh), combination_config=config).build(BuildRequest())
    shutil.rmtree(seat_root)
    shutil.rmtree(market_root)
    target = tmp_path / "reclassified"
    builder = SnapshotBuilder(source, DuckDBPublisher(target), combination_config=config)
    builder.reclassify(original)
    for runtime in (fresh, target):
        reader = DuckDBReader(runtime)
        for day, net in ((27, 600), (28, 800)):
            result = reader.view("combination", trade_date=date(2026, 7, day))
            group_row = next(r for r in result["combinations"] if r["combination_id"] == "foreign_seats")
            assert group_row["members"] == ["高盛期货"] and group_row["member_count"] == 1
            position = next(r for r in result["combination_positions"] if r["combination_id"] == "foreign_seats")
            assert position["net_position"] == net and position["missing_count"] == 0
            assert position["long_count"] == 1 and position["short_count"] == 0
        signal = next(r for r in result["combination_signals"] if r["combination_id"] == "foreign_seats")
        assert signal["net_position"] == 800 and signal["member_count"] == 1
    old_db = original / DuckDBPublisher(original).current_metadata()["database_path"]
    new_db = target / DuckDBPublisher(target).current_metadata()["database_path"]
    with duckdb.connect(str(old_db), read_only=True) as old, duckdb.connect(str(new_db), read_only=True) as new:
        for table in ("seat_dash_product_seat_daily", "seat_dash_product_daily", "seat_dash_main_contract_seat_daily"):
            query = f"SELECT * FROM {table} ORDER BY ALL"
            assert old.execute(query).fetchall() == new.execute(query).fetchall()
    good_manifest = (target / "manifests/current.json").read_bytes()
    group["members"] = ["乾坤期货", "高盛期货"]
    config.write_text(json.dumps([group]), encoding="utf-8")
    with pytest.raises(SnapshotValidationError, match="duplicate identity"):
        builder.reclassify(target)
    assert (target / "manifests/current.json").read_bytes() == good_manifest


def test_overlapping_rename_records_are_not_silently_added(tmp_path: Path):
    seat_root, market_root = _fixture_source(tmp_path)
    path = seat_root / "derive_product_seat/year_month=2026-07/part-000.parquet"
    rows = pq.ParquetFile(path).read().to_pylist()
    for row in rows:
        row["seat_name"] = "乾坤期货" if row["seat_name"] == "席位A" else "高盛期货"
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(SnapshotValidationError, match="overlapping renamed seats"):
        SnapshotBuilder(ParquetSource(seat_root, market_root), DuckDBPublisher(tmp_path / "runtime")).build(BuildRequest())
    assert not (tmp_path / "runtime/manifests/current.json").exists()


def _p2_source(tmp_path):
    seat_root, market_root = _fixture_source(tmp_path)
    for name in ('derive_product_daily', 'derive_product_seat', 'derive_main_contract_seat'):
        shutil.rmtree(seat_root / name)
    path = next(market_root.rglob('*.parquet'))
    rows = pq.ParquetFile(path).read().to_pylist()
    rows += [{**r, 'symbol': 'RB2605', 'position': 1000} for r in rows]
    pq.write_table(pa.Table.from_pylist(rows), path)
    return ParquetSource(seat_root, market_root, p2_only=True)


def test_p2_only_builds_from_contract_rows_and_rebuilds_same_day_revision(tmp_path):
    source = _p2_source(tmp_path)
    runtime = tmp_path / 'runtime'
    raw_path = next(source.dataset_path('raw_seat').rglob('*.parquet'))
    raw_rows = pq.ParquetFile(raw_path).read().to_pylist()
    raw_rows.append({**raw_rows[-1], 'seat_name':'零值席位','long_position':0,'short_position':0,'long_change':0,'short_change':0,'net_position':0})
    pq.write_table(pa.Table.from_pylist(raw_rows),raw_path)
    path = next(source.market_1d_root.rglob('*.parquet'))
    market = pq.ParquetFile(path).read().to_pylist()
    current = next(r for r in market if r['symbol']=='RB2601' and r['trade_date']==date(2026,7,28))
    market += [{**current,'symbol':'RB0000','position':999999}, {**current,'sequence':2,'close':3210}]
    pq.write_table(pa.Table.from_pylist(market),path)
    builder = SnapshotBuilder(source, DuckDBPublisher(runtime))
    original = builder.build(BuildRequest(full=True))
    reader = DuckDBReader(runtime)
    assert reader.view('term-structure',product_code='RB.SHFE')['contracts'][0]['close_price'] == 3210
    first = reader.view('combination', product_code='RB.SHFE')['metrics'][0]
    assert first['total_long'] == 950 and first['total_short'] == 950
    assert first['net_position'] == 0 and first['seat_count'] == 3
    with duckdb.connect(str(runtime/DuckDBPublisher(runtime).current_metadata()['database_path']),read_only=True) as c:
        assert c.execute("SELECT long_position,short_position,contract_count FROM seat_dash_product_seat_daily WHERE seat_name='零值席位'").fetchone()==(0,0,1)
    path = next(source.dataset_path('raw_seat').rglob('*.parquet'))
    rows = pq.ParquetFile(path).read().to_pylist()
    row = next(r for r in rows if r['trade_date'] == date(2026,7,28) and r['symbol'] == 'RB2601' and r['seat_name'] == '席位A')
    row['long_position'] += 10
    row['long_change'] = 10
    # Aggregated product rows must never be counted again.
    rows.append({**row, 'is_aggregated': 1, 'long_position': 999999})
    replacement = path.with_suffix('.new')
    pq.write_table(pa.Table.from_pylist(rows), replacement)
    replacement.replace(path)
    result = builder.build(BuildRequest(full=True))
    assert result['batch_id'] == 2
    assert result['source_manifest_sha256'] != original['source_manifest_sha256']
    revised = reader.view('combination', product_code='RB.SHFE')['metrics'][0]
    assert revised['total_long'] == 960 and revised['net_position'] == 10
    assert result['source_manifest']['input_mode'] == 'p2-only-v1'
    assert set(result['source_manifest']['datasets']) == {'raw_seat', 'market_1d'}
    assert not source.dataset_path('product_daily').exists()


@pytest.mark.parametrize('failure', ['conflicting_raw', 'market_conflict'])
def test_p2_only_rejects_ambiguous_inputs_without_publishing(tmp_path, failure):
    source = _p2_source(tmp_path)
    if failure == 'conflicting_raw':
        path = next(source.dataset_path('raw_seat').rglob('*.parquet'))
        rows = pq.ParquetFile(path).read().to_pylist()
        rows.append({**rows[0], 'long_position': 12345})
    else:
        path = next(source.market_1d_root.rglob('*.parquet'))
        rows = pq.ParquetFile(path).read().to_pylist()
        for row in rows:
            if row['trade_date'] == date(2026,7,28) and row['symbol'] == 'RB2605':
                row['position'] = 10000 if failure == 'tie' else 20000
                if failure == 'missing_main': row['symbol'] = 'RB2609'
                if failure == 'unknown_oi': row['position'] = None
        if failure == 'market_conflict': rows.append({**rows[0], 'close': 9999})
    pq.write_table(pa.Table.from_pylist(rows), path)
    runtime = tmp_path / 'runtime'
    with pytest.raises(SnapshotValidationError, match='P2-only'):
        SnapshotBuilder(source, DuckDBPublisher(runtime)).build(BuildRequest(full=True))
    assert not (runtime / 'manifests/current.json').exists()


def test_p2_only_czce_codes_match_three_and_four_digit_contracts(tmp_path):
    source = _p2_source(tmp_path)
    for dataset in ('raw_seat', 'market_1d'):
        path = next(source.dataset_path(dataset).rglob('*.parquet'))
        rows = pq.ParquetFile(path).read().to_pylist()
        for r in rows:
            r['exchange'] = 'CZCE'
            r['symbol'] = r['symbol'].replace('RB26','CF6' if dataset == 'raw_seat' else 'CF26')
            if dataset == 'raw_seat':
                r.update(root='CF',product_code='CF.CZCE',contract_code=r['symbol']+'.CZCE')
        pq.write_table(pa.Table.from_pylist(rows), path)
    runtime = tmp_path / 'runtime'
    SnapshotBuilder(source, DuckDBPublisher(runtime)).build(BuildRequest(full=True))
    result = DuckDBReader(runtime).view('term-structure',product_code='CF.CZCE')
    assert {r['contract_code'] for r in result['contracts']} == {'CF2601.CZCE','CF2605.CZCE'}
    assert all(r['close_price'] is not None for r in result['contracts'])


@pytest.mark.parametrize('failure', ['tie', 'missing_main', 'unknown_oi', 'missing_market'])
def test_p2_pending_main_preserves_product_and_does_not_guess(tmp_path, failure):
    source = _p2_source(tmp_path)
    path = next(source.market_1d_root.rglob('*.parquet'))
    rows = pq.ParquetFile(path).read().to_pylist()
    for row in rows:
        if row['trade_date']==date(2026,7,28) and row['symbol']=='RB2605':
            row['position'] = 10000 if failure=='tie' else None if failure=='unknown_oi' else 20000
            if failure=='missing_main': row['symbol']='RB2609'
    if failure=='missing_market':rows=[r for r in rows if r['trade_date']!=date(2026,7,28)]
    pq.write_table(pa.Table.from_pylist(rows),path)
    runtime=tmp_path/'runtime'
    metadata=SnapshotBuilder(source,DuckDBPublisher(runtime)).build(BuildRequest(full=True))
    assert metadata['validation']['complete']
    assert metadata['validation']['main_seat_pending']==1
    assert metadata['backfill_status']=='unknown'
    data= DuckDBReader(runtime).view('combination',product_code='RB.SHFE')
    assert data['metrics'][0]['total_long']==950
    coverage=data['main_contract_coverage'][0]
    if failure=='missing_main':
        assert coverage['symbol']=='RB2609' and coverage['close']==3200
        assert coverage['price_status']=='ready' and coverage['seat_status']=='missing_seat'
    else:
        assert coverage['symbol'] is None and coverage['close'] is None
        assert coverage['price_status']==coverage['seat_status']=='main_unresolved'


@pytest.mark.parametrize('missing_factor', [False, True])
def test_p2_validation_keeps_missing_days_and_uses_known_main_prices(tmp_path, missing_factor):
    from datetime import timedelta
    dates=[];day=date(2026,1,1)
    while len(dates)<80:
        if day.weekday()<5:dates.append(day)
        day+=timedelta(days=1)
    source=ParquetSource(tmp_path,tmp_path,p2_only=True)
    builder=SnapshotBuilder(source,DuckDBPublisher(tmp_path/'runtime'))
    with duckdb.connect() as c:
        c.execute('CREATE TABLE seat_dash_product_daily(trade_date DATE,product_code VARCHAR,net_cr5 DOUBLE,f2_value DOUBLE)')
        c.execute('CREATE TABLE seat_dash_main_contract_coverage(trade_date DATE,product_code VARCHAR,close DOUBLE)')
        c.executemany('INSERT INTO seat_dash_product_daily VALUES (?,?,?,?)',[(d,'RB.SHFE',.8,.1) for i,d in enumerate(dates) if not missing_factor or i!=20])
        # Day 10 has no price; day 15 has price but no seat publication. Both retain their date.
        c.executemany('INSERT INTO seat_dash_main_contract_coverage VALUES (?,?,?)',[(d,'RB.SHFE',None if i==10 else 100+i) for i,d in enumerate(dates)])
        builder._build_validation(c,BuildRequest(full=True),1)
        for horizon in (5,10):
            values=[(100+i+horizon)/(100+i)-1 for i in range(55-horizon) if i!=10 and i+horizon!=10 and (not missing_factor or i!=20)]
            actual=c.execute("SELECT sample_size,mean_return,library_cutoff FROM seat_dash_rule_result_daily WHERE rule_code='net_cr5_concentration' AND horizon_days=?",[horizon]).fetchone()
            assert actual[2]==dates[54]
            assert actual[0]==len(values)
            assert actual[1]==pytest.approx(sum(values)/len(values))
