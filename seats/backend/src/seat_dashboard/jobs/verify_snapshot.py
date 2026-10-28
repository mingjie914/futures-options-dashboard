from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import duckdb

from seat_dashboard.adapters.store.duckdb.reader import DuckDBReader
from seat_dashboard.core.config import Settings
from seat_dashboard.core.errors import SnapshotUnavailable


REQUIRED_TABLES = (
    "seat_dash_product_daily",
    "seat_dash_product_seat_daily",
    "seat_dash_main_contract_seat_daily",
    "seat_dash_contract_daily",
    "seat_dash_contract_seat_daily",
    "seat_dash_combination_signal_daily",
    "seat_dash_seat_signal_daily",
    "seat_dash_term_signal_daily",
    "seat_dash_rule_result_daily",
    "seat_dash_etl_run",
    "seat_dash_publish_state",
)


def verify_current(runtime_root: Path) -> dict[str, Any]:
    reader = DuckDBReader(runtime_root)
    status = reader.snapshot_status()
    manifest_path = Path(runtime_root) / "manifests" / "current.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    database_path = Path(manifest["database_path"])
    if not database_path.is_absolute():
        database_path = Path(runtime_root) / database_path
    checksum_valid = True
    if manifest.get("database_sha256"):
        with database_path.open("rb") as stream:
            checksum_valid = hashlib.file_digest(stream, "sha256").hexdigest() == manifest["database_sha256"]
    connection, _ = reader._connect()
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        required = (*REQUIRED_TABLES, "seat_dash_main_contract_coverage") if manifest.get("input_mode") == "p2-only-v1" else REQUIRED_TABLES
        missing = [table for table in required if table not in tables]
        product_rows = int(connection.execute("SELECT count(*) FROM seat_dash_product_daily").fetchone()[0])
        seat_rows = int(connection.execute("SELECT count(*) FROM seat_dash_product_seat_daily").fetchone()[0])
        net_anomalies = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_seat_daily "
                "WHERE net_position <> long_position - short_position"
            ).fetchone()[0]
        )
        f2_ready_rows = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_daily "
                "WHERE f2_status = 'ready' AND f2_value IS NOT NULL"
            ).fetchone()[0]
        )
        f2_formula_anomalies = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_daily WHERE "
                "CASE WHEN total_long + total_short = 0 THEN "
                "f2_value IS NOT NULL OR f2_status <> 'unavailable' "
                "ELSE f2_value IS NULL OR f2_status <> 'ready' OR "
                "abs(f2_value - net_position * 1.0 / (total_long + total_short)) > 1e-12 END"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    result = {
        "complete": bool(status.get("complete")),
        "batch_id": status.get("batch_id"),
        "trade_date": status.get("trade_date"),
        "data_backend": status.get("data_backend"),
        "missing_tables": missing,
        "product_daily_rows": product_rows,
        "product_seat_rows": seat_rows,
        "net_position_anomalies": net_anomalies,
        "f2_ready_rows": f2_ready_rows,
        "f2_formula_anomalies": f2_formula_anomalies,
        "checksum_valid": checksum_valid,
    }
    result["valid"] = bool(
        result["complete"]
        and not missing
        and product_rows > 0
        and seat_rows > 0
        and net_anomalies == 0
        and f2_ready_rows > 0
        and f2_formula_anomalies == 0
        and checksum_valid
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the current published DuckDB snapshot")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--export-root", type=Path, help="Copy the verified current snapshot into a new, portable runtime directory")
    args = parser.parse_args(argv)
    runtime = args.runtime_root.resolve() if args.runtime_root else Settings.from_env().runtime_root
    try:
        result = verify_current(runtime)
        if result["valid"] and args.export_root:
            destination = args.export_root.resolve()
            destination.mkdir(parents=True, exist_ok=False)
            manifest = json.loads((runtime / "manifests/current.json").read_text(encoding="utf-8"))
            source = Path(manifest["database_path"])
            if not source.is_absolute():
                source = runtime / source
            target = destination / "snapshots" / source.name
            target.parent.mkdir()
            shutil.copy2(source, target)
            with source.open("rb") as stream:
                source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            with target.open("rb") as stream:
                target_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if target_hash != source_hash:
                raise OSError("Exported snapshot checksum mismatch")
            # Keep build provenance in the private original, not the runtime manifest.
            portable = {key: manifest[key] for key in (
                "batch_id", "trade_date", "date_min", "date_max", "published_at", "data_backend",
                "metric_version", "rule_version", "combination_version", "source_manifest_sha256", "warnings", "is_demo", "complete",
                "input_mode", "backfill_status",
            ) if key in manifest}
            portable.update(database_path=f"snapshots/{source.name}", database_sha256=target_hash)
            (destination / "manifests").mkdir()
            (destination / "manifests/current.json").write_text(
                json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            result = verify_current(destination)
            result["export_root"] = str(destination)
            result["database_sha256"] = target_hash
    except (OSError, SnapshotUnavailable, duckdb.Error) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, default=str))
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
