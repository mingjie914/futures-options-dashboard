from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from seat_dashboard.adapters.source.parquet_source import ParquetSource
from seat_dashboard.adapters.store.duckdb.publisher import DuckDBPublisher
from seat_dashboard.core.config import Settings
from seat_dashboard.core.errors import SnapshotValidationError
from seat_dashboard.core.logging import configure_logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildRequest:
    full: bool = False
    trade_date: date | None = None
    dry_run: bool = False

    @property
    def mode(self) -> str:
        if self.trade_date:
            return "date"
        return "full"



class SnapshotBuilder:
    """Build all dashboard marts into a new DuckDB file before publication."""

    RULE_VERSION = "signals-v2-reference"
    METRIC_VERSION = "metrics-v2-reference"
    TERM_MIN_LEG_POSITION = 300
    ANOMALY_RATIO_THRESHOLD = 2.0
    VALIDATION_WINDOW = 504
    VALIDATION_EMBARGO_DAYS = 25
    VALIDATION_LIBRARY_WINDOW = 1260
    # Official rename: https://www.goldmansachs.cn/futures/ (2026-03-04).
    SEAT_RENAMES = {"乾坤期货": "高盛期货"}

    def __init__(
        self,
        source: ParquetSource,
        publisher: DuckDBPublisher,
        seat_tag_config: Path | None = None,
        combination_config: Path | None = None,
        product_config: Path | None = None,
    ):
        self.source = source
        self.publisher = publisher
        self.seat_tag_config = Path(seat_tag_config).resolve() if seat_tag_config else None
        self.combination_config = Path(combination_config).resolve() if combination_config else None
        self.product_config = Path(product_config).resolve() if product_config else None

    @property
    def combination_version(self) -> str:
        config = self.combination_config.read_bytes() if self.combination_config and self.combination_config.is_file() else b"[]"
        identity = json.dumps(self.SEAT_RENAMES, sort_keys=True).encode()
        return self.RULE_VERSION + "-groups-" + hashlib.sha256(config + identity).hexdigest()[:12]

    def reclassify(self, source_runtime: Path) -> dict[str, Any]:
        """Recalculate only group definitions/signals on a copy of a published snapshot."""
        source_runtime = source_runtime.resolve()
        original = DuckDBPublisher(source_runtime).current_metadata()
        if not original or not original.get("complete"):
            raise SnapshotValidationError("Reclassification requires a complete published snapshot")
        if not self.combination_config or not self.combination_config.is_file():
            raise SnapshotValidationError("Reclassification requires an explicit combination config")
        source_path = (source_runtime / original["database_path"]).resolve(strict=True)
        batch_id = max(int(original["batch_id"]) + 1, self.publisher.next_batch_id())
        temporary = self.publisher.temporary_snapshot_path(batch_id)
        if temporary.exists():
            raise FileExistsError(temporary)
        started = datetime.now(timezone.utc)
        shutil.copy2(source_path, temporary)
        if original.get("database_sha256"):
            with temporary.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != original["database_sha256"]:
                temporary.unlink()
                raise SnapshotValidationError("Source snapshot checksum mismatch")
        connection = duckdb.connect(str(temporary), config={"threads": 1, "memory_limit": "512MB"})
        try:
            connection.execute("BEGIN TRANSACTION")
            connection.execute("DELETE FROM seat_dash_combination_member")
            connection.execute("DELETE FROM seat_dash_combination")
            self._load_combinations(connection)
            self._build_combination_signals(connection, BuildRequest(full=True), batch_id)
            validation = self.validate_snapshot(connection)
            if not validation["complete"]:
                raise SnapshotValidationError(json.dumps(validation, ensure_ascii=False))
            config_hash = hashlib.sha256(self.combination_config.read_bytes()).hexdigest()
            source_manifest = {
                "base_batch_id": original["batch_id"],
                "base_source_manifest_sha256": original.get("source_manifest_sha256"),
                "base_database_sha256": original.get("database_sha256"),
                "combination_config": {"sha256": config_hash},
            }
            metadata = {key: value for key, value in original.items() if key not in ("database_path", "database_sha256", "published_at")}
            metadata.update(
                batch_id=batch_id, mode="reclassify", base_batch_id=original["batch_id"],
                combination_config_sha256=config_hash, combination_version=self.combination_version,
                source_manifest=source_manifest, source_manifest_sha256=self._sha256(source_manifest),
                started_at=started.isoformat(), row_counts=self._row_counts(connection), validation=validation,
            )
            connection.execute(
                "INSERT INTO seat_dash_etl_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                [batch_id, started, datetime.now(timezone.utc), "validated", "reclassify", metadata["source_manifest_sha256"],
                 original.get("date_min"), original.get("date_max"), json.dumps(metadata["row_counts"]), json.dumps(validation)],
            )
            connection.execute("DELETE FROM seat_dash_publish_state")
            connection.execute(
                "INSERT INTO seat_dash_publish_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["dashboard", batch_id, original["trade_date"], datetime.now(timezone.utc), original.get("data_backend", "local"), True, json.dumps(metadata, default=str)],
            )
            connection.commit()
        except Exception:
            connection.close()
            temporary.unlink(missing_ok=True)
            raise
        else:
            connection.close()
        with temporary.open("rb") as stream:
            metadata["database_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        self.publisher.publish(temporary, metadata)
        return metadata

    @staticmethod
    def _schema_path() -> Path:
        return Path(__file__).resolve().parents[1] / "adapters" / "store" / "duckdb" / "schema.sql"

    @staticmethod
    def _sha256(value: object) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _date_where(request: BuildRequest, alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        if request.trade_date:
            return f"{prefix}trade_date = DATE '{request.trade_date.isoformat()}'"
        return "TRUE"

    def build(self, request: BuildRequest) -> dict[str, Any]:
        self.source.validate()
        self.publisher.ensure_dirs()
        batch_id = self.publisher.next_batch_id()
        temporary_path = self.publisher.temporary_snapshot_path(batch_id)
        if temporary_path.exists():
            temporary_path.unlink()
        target_path = Path(":memory:") if request.dry_run else temporary_path
        source_manifest = self.source.source_manifest()
        if self.seat_tag_config and self.seat_tag_config.is_file():
            source_manifest["seat_tag_config"] = {
                "path": str(self.seat_tag_config),
                "sha256": hashlib.sha256(self.seat_tag_config.read_bytes()).hexdigest(),
            }
        if self.combination_config and self.combination_config.is_file():
            source_manifest["combination_config"] = {
                "path": str(self.combination_config),
                "sha256": hashlib.sha256(self.combination_config.read_bytes()).hexdigest(),
            }
        if self.product_config and self.product_config.is_file():
            source_manifest["product_config"] = {
                "path": str(self.product_config),
                "sha256": hashlib.sha256(self.product_config.read_bytes()).hexdigest(),
            }
        started = datetime.now(timezone.utc)
        connection = duckdb.connect(str(target_path))
        try:
            connection.execute(self._schema_path().read_text(encoding="utf-8"))
            if self.source.p2_only:
                self.source.prepare(connection, request.trade_date)
                connection.execute("CREATE OR REPLACE TABLE seat_dash_main_contract_coverage AS SELECT * FROM _p2_main_price")
            self._build_dimensions(connection, request, batch_id)
            self._build_source_marts(connection, request, batch_id)
            self._build_signals(connection, request, batch_id)
            self._build_validation(connection, request, batch_id)
            validation = self.validate_snapshot(connection)
            if not validation["complete"]:
                raise SnapshotValidationError(json.dumps(validation, ensure_ascii=False))
            row_counts = self._row_counts(connection)
            date_range = self._date_range(connection)
            metadata = {
                "batch_id": batch_id,
                "trade_date": date_range[1].isoformat() if date_range[1] else None,
                "date_min": date_range[0].isoformat() if date_range[0] else None,
                "date_max": date_range[1].isoformat() if date_range[1] else None,
                "mode": request.mode,
                "metric_version": self.METRIC_VERSION + ("-p2-calendar-v1" if self.source.p2_only else ""),
                "input_mode": "p2-only-v1" if self.source.p2_only else "derived",
                "backfill_status": "unknown" if self.source.p2_only else "unspecified",
                "rule_version": self.RULE_VERSION,
                "combination_version": self.combination_version,
                "source_manifest_sha256": self._sha256(source_manifest),
                "source_manifest": source_manifest,
                "row_counts": row_counts,
                "validation": validation,
                "warnings": validation.get("warnings", []),
                "started_at": started.isoformat(),
                "dry_run": request.dry_run,
                "is_demo": False,
            }
            connection.execute(
                "INSERT INTO seat_dash_etl_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                [
                    batch_id,
                    started,
                    datetime.now(timezone.utc),
                    "validated",
                    request.mode,
                    metadata["source_manifest_sha256"],
                    date_range[0],
                    date_range[1],
                    json.dumps(row_counts, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                ],
            )
            connection.execute(
                "INSERT INTO seat_dash_publish_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    "dashboard",
                    batch_id,
                    date_range[1],
                    datetime.now(timezone.utc),
                    "local",
                    True,
                    json.dumps(metadata, ensure_ascii=False, default=str),
                ],
            )
            connection.commit()
        except Exception:
            connection.close()
            if not request.dry_run and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            raise
        else:
            connection.close()
        if request.dry_run:
            return metadata
        final_path = self.publisher.final_snapshot_path(batch_id)
        self.publisher.publish(temporary_path, metadata)
        metadata["database_path"] = str(final_path)
        return metadata

    def _build_dimensions(self, connection: duckdb.DuckDBPyConnection, request: BuildRequest, batch_id: int) -> None:
        product = self.source.relation_sql("product_daily")
        raw = self.source.relation_sql("raw_seat")
        product_where = self._date_where(request)
        raw_where = self._date_where(request, "r")
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_product_dim AS
            SELECT DISTINCT
                upper(product_code) AS product_code,
                upper(root) AS root,
                upper(exchange) AS exchange,
                upper(product_code) AS display_name,
                TRUE AS active
            FROM {product}
            WHERE {product_where}
            UNION
            SELECT DISTINCT
                upper(r.product_code) AS product_code,
                upper(r.root) AS root,
                upper(r.exchange) AS exchange,
                upper(r.product_code) AS display_name,
                TRUE AS active
            FROM {raw} r
            WHERE r.is_aggregated = 0 AND {raw_where}
              AND CAST(r.trade_date AS DATE) > (
                  SELECT max(CAST(trade_date AS DATE)) FROM {product}
              )
            """
        )
        self._load_product_names(connection)
        product_seat = self.source.relation_sql("product_seat")
        main_seat = self.source.relation_sql("main_contract_seat")
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_seat_dim AS
            SELECT md5(seat_name) AS seat_id, seat_name, TRUE AS active
            FROM (
                SELECT DISTINCT trim(seat_name) AS seat_name FROM {product_seat}
                WHERE {self._date_where(request)} AND seat_name IS NOT NULL AND trim(seat_name) <> ''
                UNION
                SELECT DISTINCT trim(seat_name) AS seat_name FROM {main_seat}
                WHERE {self._date_where(request)} AND seat_name IS NOT NULL AND trim(seat_name) <> ''
                UNION
                SELECT DISTINCT trim(r.seat_name) AS seat_name FROM {raw} r
                WHERE r.is_aggregated = 0 AND {raw_where}
                  AND CAST(r.trade_date AS DATE) > (
                      SELECT max(CAST(trade_date AS DATE)) FROM {product_seat}
                  )
                  AND r.seat_name IS NOT NULL AND trim(r.seat_name) <> ''
            )
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_tag_definition AS
            SELECT * FROM (VALUES
                ('capital_seat', '资金席位', 'static', 'accent', '人工维护或外部来源标注的资金席位', FALSE),
                ('hedging_seat', '套保盘', 'static', 'gold', '人工维护或外部来源标注的套保席位', FALSE),
                ('weak_seat', '弱势席位', 'static', 'muted', '人工维护或外部来源标注的弱势席位', FALSE),
                ('tracked_seat', '重点跟踪', 'static', 'highlight', '用户重点跟踪的席位', FALSE),
                ('reversal', '多空反转', 'dynamic', 'warning', '净持仓方向发生反转', TRUE),
                ('position_spike', '持仓异动', 'dynamic', 'warning', '净增仓超过规则阈值', TRUE),
                ('term_opposite', '近远月反向', 'dynamic', 'gold', '近远月出现反向净持仓', TRUE),
                ('suspected_arbitrage', '疑似套利', 'research', 'gold', '人工治理且需要复核的推断性研究标签', FALSE),
                ('new_top', '新进Top', 'dynamic', 'accent', '排名进入Top区间', TRUE),
                ('exit_top', '退出Top', 'dynamic', 'muted', '排名退出Top区间', TRUE)
            ) AS t(tag_code, label, category, color_token, description, is_dynamic)
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_seat_alias AS
            SELECT seat_name AS source_seat_name, md5(seat_name) AS seat_id,
                   'normalized_source' AS source, CAST(NULL AS VARCHAR) AS note
            FROM seat_dash_seat_dim
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_seat_tag_assignment AS
            SELECT CAST(NULL AS VARCHAR) AS assignment_id,
                   CAST(NULL AS VARCHAR) AS seat_id,
                   CAST(NULL AS VARCHAR) AS tag_code,
                   CAST(NULL AS VARCHAR) AS product_code,
                   CAST(NULL AS DATE) AS valid_from,
                   CAST(NULL AS DATE) AS valid_to,
                   CAST(NULL AS VARCHAR) AS source,
                   CAST(NULL AS DOUBLE) AS confidence,
                   CAST(NULL AS VARCHAR) AS evidence_json,
                   CAST(NULL AS VARCHAR) AS note
            WHERE FALSE
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_combination AS
            SELECT 'all_market' AS combination_id, '全市场席位' AS name,
                   'all' AS scope, TRUE AS is_system, TRUE AS active
            UNION ALL
            SELECT 'custom_empty', '自定义组合（待配置）', 'custom', FALSE, TRUE
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_combination_member AS
            SELECT CAST(NULL AS VARCHAR) AS combination_id, CAST(NULL AS VARCHAR) AS seat_id
            WHERE FALSE
            """
        )
        self._load_combinations(connection)
        self._load_tag_assignments(connection)

    def _load_product_names(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Apply the governed local product dictionary without changing source facts."""
        if self.product_config is None or not self.product_config.is_file():
            return
        with self.product_config.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"product_code", "display_name", "source", "note"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                missing = sorted(required - set(reader.fieldnames or []))
                raise SnapshotValidationError(
                    f"product config is missing columns: {', '.join(missing)}"
                )
            rows = list(reader)
        seen: set[str] = set()
        for line_number, raw in enumerate(rows, start=2):
            product_code = (raw.get("product_code") or "").strip().upper()
            display_name = (raw.get("display_name") or "").strip()
            if not re.fullmatch(r"[A-Z0-9]+\.[A-Z]+", product_code) or not display_name:
                raise SnapshotValidationError(
                    f"invalid product dictionary row at line {line_number}"
                )
            if product_code in seen:
                raise SnapshotValidationError(
                    f"duplicate product dictionary row at line {line_number}: {product_code}"
                )
            seen.add(product_code)
            connection.execute(
                "UPDATE seat_dash_product_dim SET display_name = ? WHERE product_code = ?",
                [display_name, product_code],
            )

    def _load_combinations(self, connection: duckdb.DuckDBPyConnection) -> None:
        if self.combination_config is None or not self.combination_config.is_file():
            return
        try:
            payload = json.loads(self.combination_config.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotValidationError("combination config is not valid JSON") from exc
        if not isinstance(payload, list):
            raise SnapshotValidationError("combination config must be a JSON list")
        known_ids = {
            row[0]
            for row in connection.execute(
                "SELECT combination_id FROM seat_dash_combination"
            ).fetchall()
        }
        seat_ids = dict(
            connection.execute("SELECT seat_name, seat_id FROM seat_dash_seat_dim").fetchall()
        )
        for old, new in self.SEAT_RENAMES.items():
            if old not in seat_ids:
                continue
            connection.execute("ALTER TABLE seat_dash_seat_alias ALTER COLUMN note TYPE VARCHAR")
            canonical_id = seat_ids.get(new) or hashlib.md5(new.encode()).hexdigest()
            if new not in seat_ids:
                connection.execute("INSERT INTO seat_dash_seat_dim VALUES (?, ?, TRUE)", [canonical_id, new])
                connection.execute("INSERT INTO seat_dash_seat_alias VALUES (?, ?, ?, ?)",
                                   [new, canonical_id, "official_rename", f"原名{old}"])
            connection.execute(
                "UPDATE seat_dash_seat_alias SET seat_id = ?, source = 'official_rename', note = ? "
                "WHERE source_seat_name = ?",
                [canonical_id, "https://www.goldmansachs.cn/futures/", old],
            )
            seat_ids[old] = seat_ids[new] = canonical_id
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise SnapshotValidationError(f"combination item {index} must be an object")
            combination_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            scope = str(item.get("scope", "system")).strip() or "system"
            members = item.get("members", [])
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", combination_id):
                raise SnapshotValidationError(f"invalid combination id at item {index}")
            if combination_id in known_ids:
                raise SnapshotValidationError(f"duplicate combination id at item {index}: {combination_id}")
            if not name or not isinstance(members, list):
                raise SnapshotValidationError(f"combination name/members are invalid at item {index}")
            normalized_members = [str(member).strip() for member in members if str(member).strip()]
            if len(set(normalized_members)) != len(normalized_members):
                raise SnapshotValidationError(f"duplicate member in combination {combination_id}")
            unknown = [member for member in normalized_members if member not in seat_ids]
            if unknown:
                raise SnapshotValidationError(
                    f"unknown member in combination {combination_id}: {unknown[0]}"
                )
            if len({seat_ids[member] for member in normalized_members}) != len(normalized_members):
                raise SnapshotValidationError(f"duplicate identity in combination {combination_id}")
            connection.execute(
                "INSERT INTO seat_dash_combination VALUES (?, ?, ?, ?, ?)",
                [
                    combination_id,
                    name,
                    scope,
                    bool(item.get("isSystem", True)),
                    bool(item.get("active", True)),
                ],
            )
            if normalized_members:
                connection.executemany(
                    "INSERT INTO seat_dash_combination_member VALUES (?, ?)",
                    [[combination_id, seat_ids[member]] for member in normalized_members],
                )
            known_ids.add(combination_id)

    def _load_tag_assignments(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Load governed manual tags during snapshot build; never infer them from names."""
        if self.seat_tag_config is None or not self.seat_tag_config.is_file():
            return
        required = {
            "seat_name",
            "tag_code",
            "product_code",
            "valid_from",
            "valid_to",
            "source",
            "confidence",
            "evidence",
            "note",
        }
        with self.seat_tag_config.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                missing = sorted(required - set(reader.fieldnames or []))
                raise SnapshotValidationError(f"seat tag config is missing columns: {', '.join(missing)}")
            rows = list(reader)
        if not rows:
            return

        seat_ids = dict(connection.execute("SELECT seat_name, seat_id FROM seat_dash_seat_dim").fetchall())
        tag_codes = {row[0] for row in connection.execute("SELECT tag_code FROM seat_dash_tag_definition").fetchall()}
        product_codes = {row[0] for row in connection.execute("SELECT product_code FROM seat_dash_product_dim").fetchall()}
        assignment_ids: set[str] = set()
        values: list[list[Any]] = []
        for line_number, raw in enumerate(rows, start=2):
            seat_name = (raw.get("seat_name") or "").strip()
            tag_code = (raw.get("tag_code") or "").strip()
            product_code = (raw.get("product_code") or "").strip().upper() or None
            source = (raw.get("source") or "").strip()
            evidence = (raw.get("evidence") or "").strip()
            if seat_name not in seat_ids:
                raise SnapshotValidationError(f"unknown seat_name in tag config line {line_number}: {seat_name}")
            if tag_code not in tag_codes:
                raise SnapshotValidationError(f"unknown tag_code in tag config line {line_number}: {tag_code}")
            if product_code and product_code not in product_codes:
                raise SnapshotValidationError(
                    f"unknown product_code in tag config line {line_number}: {product_code}"
                )
            if not source or not evidence:
                raise SnapshotValidationError(
                    f"source and evidence are required in tag config line {line_number}"
                )
            try:
                confidence = float((raw.get("confidence") or "").strip())
            except ValueError as exc:
                raise SnapshotValidationError(
                    f"invalid confidence in tag config line {line_number}"
                ) from exc
            if confidence < 0 or confidence > 1:
                raise SnapshotValidationError(
                    f"confidence must be between 0 and 1 in tag config line {line_number}"
                )
            try:
                valid_from = date.fromisoformat((raw.get("valid_from") or "").strip())
                valid_to = date.fromisoformat((raw.get("valid_to") or "").strip())
            except ValueError as exc:
                raise SnapshotValidationError(
                    f"valid_from and valid_to must be ISO dates in tag config line {line_number}"
                ) from exc
            if valid_to < valid_from:
                raise SnapshotValidationError(f"invalid validity range in tag config line {line_number}")
            assignment_payload = {
                "seat_name": seat_name,
                "tag_code": tag_code,
                "product_code": product_code,
                "valid_from": valid_from.isoformat(),
                "valid_to": valid_to.isoformat(),
                "source": source,
            }
            assignment_id = self._sha256(assignment_payload)[:32]
            if assignment_id in assignment_ids:
                raise SnapshotValidationError(f"duplicate tag assignment in config line {line_number}")
            assignment_ids.add(assignment_id)
            values.append(
                [
                    assignment_id,
                    seat_ids[seat_name],
                    tag_code,
                    product_code,
                    valid_from,
                    valid_to,
                    source,
                    confidence,
                    json.dumps(
                        {"text": evidence, "config_line": line_number},
                        ensure_ascii=False,
                    ),
                    (raw.get("note") or "").strip() or None,
                ]
            )
        connection.executemany(
            "INSERT INTO seat_dash_seat_tag_assignment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )

    def _build_source_marts(self, connection: duckdb.DuckDBPyConnection, request: BuildRequest, batch_id: int) -> None:
        product = self.source.relation_sql("product_daily")
        product_seat = self.source.relation_sql("product_seat")
        main_seat = self.source.relation_sql("main_contract_seat")
        raw = self.source.relation_sql("raw_seat")
        market = self.source.relation_sql("market_1d")
        where = self._date_where(request)
        signal_where = (
            f"trade_date = DATE '{request.trade_date.isoformat()}'"
            if request.trade_date
            else "trade_date = (SELECT max(trade_date) FROM seat_dash_product_daily)"
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_product_seat_daily AS
            WITH normalized_all AS (
                SELECT CAST(trade_date AS DATE) AS trade_date,
                       upper(product_code) AS product_code,
                       md5(trim(seat_name)) AS seat_id,
                       trim(seat_name) AS seat_name,
                       CAST(coalesce(long_position, 0) AS BIGINT) AS long_position,
                       CAST(coalesce(long_change, 0) AS BIGINT) AS long_change,
                       CAST(coalesce(short_position, 0) AS BIGINT) AS short_position,
                       CAST(coalesce(short_change, 0) AS BIGINT) AS short_change,
                       CAST(coalesce(long_position, 0) - coalesce(short_position, 0) AS BIGINT) AS net_position,
                       CAST(coalesce(long_change, 0) - coalesce(short_change, 0) AS BIGINT) AS net_change,
                       CAST(coalesce(contract_count, 0) AS BIGINT) AS contract_count
                FROM {product_seat}
                WHERE seat_name IS NOT NULL AND trim(seat_name) <> ''
                UNION ALL
                SELECT CAST(r.trade_date AS DATE) AS trade_date,
                       upper(r.product_code) AS product_code,
                       md5(trim(r.seat_name)) AS seat_id,
                       trim(r.seat_name) AS seat_name,
                       CAST(sum(coalesce(r.long_position, 0)) AS BIGINT) AS long_position,
                       CAST(sum(coalesce(r.long_change, 0)) AS BIGINT) AS long_change,
                       CAST(sum(coalesce(r.short_position, 0)) AS BIGINT) AS short_position,
                       CAST(sum(coalesce(r.short_change, 0)) AS BIGINT) AS short_change,
                       CAST(sum(coalesce(r.long_position, 0)) - sum(coalesce(r.short_position, 0)) AS BIGINT) AS net_position,
                       CAST(sum(coalesce(r.long_change, 0)) - sum(coalesce(r.short_change, 0)) AS BIGINT) AS net_change,
                       CAST(count(DISTINCT r.contract_code) AS BIGINT) AS contract_count
                FROM {raw} r
                WHERE r.is_aggregated = 0
                  AND CAST(r.trade_date AS DATE) > (
                      SELECT max(CAST(trade_date AS DATE)) FROM {product_seat}
                  )
                  AND r.seat_name IS NOT NULL AND trim(r.seat_name) <> ''
                GROUP BY CAST(r.trade_date AS DATE), upper(r.product_code), trim(r.seat_name)
            ), product_dates AS (
                SELECT product_code, trade_date,
                       lag(trade_date) OVER (
                           PARTITION BY product_code ORDER BY trade_date
                       ) AS previous_trade_date
                FROM (SELECT DISTINCT product_code, trade_date FROM normalized_all)
            ), target AS (
                SELECT * FROM normalized_all WHERE {where}
            )
            SELECT t.trade_date, t.product_code, t.seat_id, t.seat_name,
                t.long_position, t.long_change, t.short_position, t.short_change,
                t.net_position, t.net_change,
                CAST(abs(t.net_position) AS BIGINT) AS abs_net_position,
                CAST(abs(t.net_change) AS BIGINT) AS abs_net_change,
                p.net_position AS previous_net_position,
                CAST(t.net_position - coalesce(p.net_position, 0) AS BIGINT) AS net_position_change,
                CASE
                    WHEN p.seat_id IS NULL AND t.net_position <> 0 THEN 'new'
                    WHEN p.net_position <> 0 AND t.net_position = 0 THEN 'exit'
                    ELSE 'continuing'
                END AS entry_state,
                t.contract_count,
                {batch_id} AS batch_id
            FROM target t
            INNER JOIN product_dates d
                ON d.product_code = t.product_code AND d.trade_date = t.trade_date
            LEFT JOIN normalized_all p
                ON p.product_code = t.product_code
               AND p.trade_date = d.previous_trade_date
               AND p.seat_id = t.seat_id
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_main_contract_seat_daily AS
            SELECT
                CAST(trade_date AS DATE) AS trade_date,
                upper(product_code) AS product_code,
                upper(contract_code) AS contract_code,
                upper(symbol) AS symbol,
                md5(trim(seat_name)) AS seat_id,
                trim(seat_name) AS seat_name,
                CAST(coalesce(long_position, 0) AS BIGINT) AS long_position,
                CAST(coalesce(long_change, 0) AS BIGINT) AS long_change,
                CAST(coalesce(short_position, 0) AS BIGINT) AS short_position,
                CAST(coalesce(short_change, 0) AS BIGINT) AS short_change,
                CAST(coalesce(long_position, 0) - coalesce(short_position, 0) AS BIGINT) AS net_position,
                CAST(coalesce(long_change, 0) - coalesce(short_change, 0) AS BIGINT) AS net_change,
                CAST(close AS DOUBLE) AS close,
                CAST(open_interest AS BIGINT) AS open_interest,
                {batch_id} AS batch_id
            FROM {main_seat}
            WHERE {where} AND seat_name IS NOT NULL AND trim(seat_name) <> ''
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_product_daily AS
            WITH source AS (
                SELECT CAST(trade_date AS DATE) AS trade_date,
                       upper(product_code) AS product_code,
                       upper(root) AS root,
                       upper(exchange) AS exchange,
                       CAST(coalesce(total_long, 0) AS BIGINT) AS total_long,
                       CAST(coalesce(total_short, 0) AS BIGINT) AS total_short,
                       CAST(coalesce(total_long, 0) - coalesce(total_short, 0) AS BIGINT) AS net_position,
                       CAST(coalesce(long_change, 0) AS BIGINT) AS long_change,
                       CAST(coalesce(short_change, 0) AS BIGINT) AS short_change,
                       CAST(coalesce(long_change, 0) - coalesce(short_change, 0) AS BIGINT) AS net_change,
                       CAST(coalesce(seat_count, 0) AS BIGINT) AS seat_count
                FROM {product}
                WHERE {where}
                UNION ALL
                SELECT CAST(r.trade_date AS DATE) AS trade_date,
                       upper(r.product_code) AS product_code,
                       max(upper(r.root)) AS root,
                       max(upper(r.exchange)) AS exchange,
                       CAST(sum(coalesce(r.long_position, 0)) AS BIGINT) AS total_long,
                       CAST(sum(coalesce(r.short_position, 0)) AS BIGINT) AS total_short,
                       CAST(sum(coalesce(r.long_position, 0)) - sum(coalesce(r.short_position, 0)) AS BIGINT) AS net_position,
                       CAST(sum(coalesce(r.long_change, 0)) AS BIGINT) AS long_change,
                       CAST(sum(coalesce(r.short_change, 0)) AS BIGINT) AS short_change,
                       CAST(sum(coalesce(r.long_change, 0)) - sum(coalesce(r.short_change, 0)) AS BIGINT) AS net_change,
                       CAST(count(DISTINCT r.seat_name) AS BIGINT) AS seat_count
                FROM {raw} r
                WHERE r.is_aggregated = 0 AND {self._date_where(request, "r")}
                  AND CAST(r.trade_date AS DATE) > (
                      SELECT max(CAST(trade_date AS DATE)) FROM {product}
                  )
                  AND r.seat_name IS NOT NULL AND trim(r.seat_name) <> ''
                GROUP BY CAST(r.trade_date AS DATE), upper(r.product_code)
            ), ranked AS (
                SELECT product_code, trade_date,
                       long_position, short_position, net_position,
                       row_number() OVER (
                           PARTITION BY trade_date, product_code
                           ORDER BY long_position DESC, seat_name
                       ) AS long_rank,
                       row_number() OVER (
                           PARTITION BY trade_date, product_code
                           ORDER BY short_position DESC, seat_name
                       ) AS short_rank,
                       row_number() OVER (
                           PARTITION BY trade_date, product_code
                           ORDER BY abs(net_position) DESC, seat_name
                       ) AS net_rank
                FROM seat_dash_product_seat_daily
            ), concentration AS (
                SELECT trade_date, product_code,
                       sum(CASE WHEN long_rank <= 5 THEN long_position ELSE 0 END) AS top_long,
                       sum(CASE WHEN short_rank <= 5 THEN short_position ELSE 0 END) AS top_short,
                       sum(CASE WHEN net_rank <= 5 THEN abs(net_position) ELSE 0 END) AS top_abs_net,
                       sum(abs(net_position)) AS total_abs_net
                FROM ranked
                GROUP BY trade_date, product_code
            )
            SELECT s.trade_date, s.product_code, s.root, s.exchange,
                   s.total_long, s.total_short, s.net_position,
                   s.long_change, s.short_change, s.net_change, s.seat_count,
                   CASE WHEN s.total_short = 0 THEN NULL ELSE s.total_long * 1.0 / s.total_short END AS long_short_ratio,
                   CASE WHEN s.total_long = 0 THEN NULL ELSE c.top_long * 1.0 / s.total_long END AS long_cr5,
                   CASE WHEN s.total_short = 0 THEN NULL ELSE c.top_short * 1.0 / s.total_short END AS short_cr5,
                   CASE WHEN c.total_abs_net = 0 THEN NULL ELSE c.top_abs_net * 1.0 / c.total_abs_net END AS net_cr5,
                   'combination_rule' AS divergence_state,
                   CASE WHEN s.total_long + s.total_short = 0 THEN NULL
                        ELSE s.net_position * 1.0 / (s.total_long + s.total_short) END AS f2_value,
                   CASE WHEN s.total_long + s.total_short = 0 THEN 'unavailable' ELSE 'ready' END AS f2_status,
                   {batch_id} AS batch_id
            FROM source s
            LEFT JOIN concentration c USING (trade_date, product_code)
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_market_daily AS
            SELECT trade_date, product_code, exchange, symbol, close,
                   CAST(position AS BIGINT) AS open_interest,
                   CAST(volume AS BIGINT) AS volume,
                   CAST(sequence AS INTEGER) AS sequence,
                   {batch_id} AS batch_id
            FROM (
                SELECT CAST(trade_date AS DATE) AS trade_date,
                       upper(regexp_replace(symbol, '[0-9].*$', '')) || '.' || upper(exchange) AS product_code,
                       upper(exchange) AS exchange,
                       upper(symbol) AS symbol,
                       close, position, volume, sequence,
                       row_number() OVER (
                           PARTITION BY trade_date, upper(exchange), upper(symbol)
                           ORDER BY sequence DESC NULLS LAST
                       ) AS rn
                FROM {market}
                WHERE {where}
            ) m
            WHERE rn = 1 AND product_code <> '.'
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_contract_daily AS
            WITH raw_rows AS (
                SELECT CAST(trade_date AS DATE) AS trade_date,
                       upper(product_code) AS product_code,
                       upper(contract_code) AS contract_code,
                       upper(symbol) AS symbol,
                       trim(seat_name) AS seat_name,
                       CAST(coalesce(long_position, 0) AS BIGINT) AS long_position,
                       CAST(coalesce(short_position, 0) AS BIGINT) AS short_position
                FROM {raw}
                WHERE is_aggregated = 0 AND {signal_where}
            ), aggregated AS (
                SELECT trade_date, product_code, contract_code, max(symbol) AS symbol,
                       sum(long_position) AS total_long,
                       sum(short_position) AS total_short,
                       count(DISTINCT seat_name) AS seat_count
                FROM raw_rows
                GROUP BY trade_date, product_code, contract_code
            ), ranked AS (
                SELECT *,
                       try_cast(regexp_extract(symbol, '([0-9]{{3,6}})$', 1) AS INTEGER) AS contract_month
                FROM aggregated
            )
            SELECT trade_date, product_code, contract_code, symbol, contract_month,
                   row_number() OVER (
                       PARTITION BY trade_date, product_code
                       ORDER BY contract_month ASC NULLS LAST, contract_code
                   ) AS contract_rank,
                   CAST(total_long AS BIGINT) AS total_long,
                   CAST(total_short AS BIGINT) AS total_short,
                   CAST(total_long - total_short AS BIGINT) AS net_position,
                   CAST(seat_count AS BIGINT) AS seat_count,
                   {batch_id} AS batch_id
            FROM ranked
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_contract_seat_daily AS
            WITH raw_rows AS (
                SELECT CAST(trade_date AS DATE) AS trade_date,
                       upper(product_code) AS product_code,
                       upper(contract_code) AS contract_code,
                       upper(symbol) AS symbol,
                       md5(trim(seat_name)) AS seat_id,
                       trim(seat_name) AS seat_name,
                       CAST(coalesce(long_position, 0) AS BIGINT) AS long_position,
                       CAST(coalesce(short_position, 0) AS BIGINT) AS short_position
                FROM {raw}
                WHERE is_aggregated = 0 AND {signal_where}
                  AND seat_name IS NOT NULL AND trim(seat_name) <> ''
            ), aggregated AS (
                SELECT trade_date, product_code, contract_code, max(symbol) AS symbol,
                       seat_id, max(seat_name) AS seat_name,
                       sum(long_position) AS long_position,
                       sum(short_position) AS short_position
                FROM raw_rows
                GROUP BY trade_date, product_code, contract_code, seat_id
            )
            SELECT a.trade_date, a.product_code, a.contract_code, a.symbol,
                   c.contract_rank, a.seat_id, a.seat_name,
                   CAST(a.long_position AS BIGINT) AS long_position,
                   CAST(a.short_position AS BIGINT) AS short_position,
                   CAST(a.long_position - a.short_position AS BIGINT) AS net_position,
                   {batch_id} AS batch_id
            FROM aggregated a
            INNER JOIN seat_dash_contract_daily c
                ON c.trade_date = a.trade_date
               AND c.product_code = a.product_code
               AND c.contract_code = a.contract_code
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE seat_dash_contract_dim AS
            SELECT contract_code, max(product_code) AS product_code,
                   split_part(contract_code, '.', 2) AS exchange,
                   max(symbol) AS symbol,
                   max(contract_month) AS contract_month,
                   'suffix_numeric_then_code' AS contract_order_source
            FROM seat_dash_contract_daily
            GROUP BY contract_code
            """
        )

    def _build_combination_signals(self, connection: duckdb.DuckDBPyConnection, request: BuildRequest, batch_id: int) -> None:
        for old, new in self.SEAT_RENAMES.items():
            overlap = connection.execute(
                "SELECT trade_date, product_code FROM seat_dash_product_seat_daily "
                "WHERE seat_name IN (?, ?) GROUP BY trade_date, product_code "
                "HAVING count(*) > 1 LIMIT 1", [old, new],
            ).fetchone()
            if overlap:
                raise SnapshotValidationError(f"overlapping renamed seats {old}/{new}: {overlap}")
        signal_where = (
            f"trade_date = DATE '{request.trade_date.isoformat()}'"
            if request.trade_date
            else "trade_date = (SELECT max(trade_date) FROM seat_dash_product_daily)"
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_combination_signal_daily AS
            WITH combination_size AS (
                SELECT c.combination_id, count(*) AS member_count
                FROM seat_dash_combination c
                INNER JOIN seat_dash_combination_member m USING (combination_id)
                WHERE c.active
                GROUP BY c.combination_id
            ), grouped AS (
                SELECT p.trade_date, p.product_code, m.combination_id,
                       max(s.member_count) AS member_count,
                       count(DISTINCT m.seat_id) AS observed_member_count,
                       min(sign(p.net_position)) AS min_position_sign,
                       max(sign(p.net_position)) AS max_position_sign,
                       min(sign(p.net_change)) AS min_change_sign,
                       max(sign(p.net_change)) AS max_change_sign,
                       sum(p.net_position) AS net_position,
                       sum(p.net_change) AS net_change
                FROM seat_dash_product_seat_daily p
                LEFT JOIN seat_dash_seat_alias a ON a.source_seat_name = p.seat_name
                INNER JOIN seat_dash_combination_member m ON m.seat_id = coalesce(a.seat_id, p.seat_id)
                INNER JOIN combination_size s ON s.combination_id = m.combination_id
                WHERE p.{signal_where}
                GROUP BY p.trade_date, p.product_code, m.combination_id
            )
            SELECT trade_date, product_code, combination_id,
                   CASE WHEN min_position_sign > 0 THEN 'long' ELSE 'short' END AS direction,
                   CAST(net_position AS BIGINT) AS net_position,
                   CAST(net_change AS BIGINT) AS net_change,
                   CASE
                       WHEN min_change_sign <> max_change_sign OR min_change_sign = 0 THEN NULL
                       WHEN min_position_sign > 0 AND min_change_sign > 0 THEN 'add_long'
                       WHEN min_position_sign > 0 AND min_change_sign < 0 THEN 'reduce_long'
                       WHEN min_position_sign < 0 AND min_change_sign < 0 THEN 'add_short'
                       ELSE 'reduce_short'
                   END AS action_code,
                   CASE WHEN min_change_sign = max_change_sign AND min_change_sign <> 0
                        THEN CAST(abs(net_change) AS BIGINT) ELSE NULL END AS action_change,
                   CAST(member_count AS BIGINT) AS member_count,
                   CAST(observed_member_count AS BIGINT) AS observed_member_count,
                   '{self.combination_version}' AS rule_version,
                   {batch_id} AS batch_id
            FROM grouped
            WHERE observed_member_count = member_count
              AND min_position_sign = max_position_sign
              AND min_position_sign <> 0
            """
        )
    def _build_signals(self, connection: duckdb.DuckDBPyConnection, request: BuildRequest, batch_id: int) -> None:
        self._build_combination_signals(connection, request, batch_id)
        signal_where = (
            f"trade_date = DATE '{request.trade_date.isoformat()}'"
            if request.trade_date
            else "trade_date = (SELECT max(trade_date) FROM seat_dash_product_daily)"
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_seat_signal_daily AS
            WITH baseline AS (
                SELECT trade_date, product_code,
                       avg((abs(long_change) + abs(short_change)) / 2.0) AS mean_abs_leg_change
                FROM seat_dash_product_seat_daily
                WHERE {signal_where}
                GROUP BY trade_date, product_code
            ), legs AS (
                SELECT trade_date, product_code, seat_id, seat_name, 'long' AS leg,
                       long_change AS leg_change, net_position
                FROM seat_dash_product_seat_daily
                WHERE {signal_where}
                UNION ALL
                SELECT trade_date, product_code, seat_id, seat_name, 'short' AS leg,
                       short_change AS leg_change, net_position
                FROM seat_dash_product_seat_daily
                WHERE {signal_where}
            ), spikes AS (
                SELECT l.trade_date, l.product_code, l.seat_id, l.seat_name,
                       CASE
                           WHEN leg = 'long' AND leg_change > 0 THEN 'long_surge'
                           WHEN leg = 'long' THEN 'long_drop'
                           WHEN leg_change > 0 THEN 'short_surge'
                           ELSE 'short_drop'
                       END AS signal_code,
                       leg AS direction,
                       'medium' AS severity,
                       CAST(abs(leg_change) AS DOUBLE) AS metric_value,
                       CAST(b.mean_abs_leg_change * {self.ANOMALY_RATIO_THRESHOLD} AS DOUBLE) AS threshold_value,
                       least(0.95, 0.65 + abs(leg_change) / nullif(b.mean_abs_leg_change, 0) / 100.0) AS confidence,
                       CAST(json_object(
                           'leg', leg,
                           'leg_change', leg_change,
                           'baseline_mean', round(b.mean_abs_leg_change, 6),
                           'multiple', round(abs(leg_change) / nullif(b.mean_abs_leg_change, 0), 4),
                           'net_position', net_position
                       ) AS VARCHAR) AS evidence_json
                FROM legs l
                INNER JOIN baseline b USING (trade_date, product_code)
                WHERE b.mean_abs_leg_change > 0
                  AND leg_change <> 0
                  AND abs(leg_change) >= b.mean_abs_leg_change * {self.ANOMALY_RATIO_THRESHOLD}
            ), reversals AS (
                SELECT trade_date, product_code, seat_id, seat_name,
                       'reversal' AS signal_code,
                       CASE WHEN net_position > 0 THEN 'long' ELSE 'short' END AS direction,
                       'high' AS severity,
                       CAST(net_position_change AS DOUBLE) AS metric_value,
                       CAST({self.TERM_MIN_LEG_POSITION} AS DOUBLE) AS threshold_value,
                       0.9 AS confidence,
                       CAST(json_object(
                           'previous_net_position', previous_net_position,
                           'net_position', net_position,
                           'net_position_change', net_position_change
                       ) AS VARCHAR) AS evidence_json
                FROM seat_dash_product_seat_daily
                WHERE {signal_where}
                  AND previous_net_position IS NOT NULL
                  AND previous_net_position * net_position < 0
                  AND abs(net_position) >= {self.TERM_MIN_LEG_POSITION}
            )
            SELECT *, '{self.RULE_VERSION}' AS rule_version, {batch_id} AS batch_id FROM reversals
            UNION ALL
            SELECT *, '{self.RULE_VERSION}' AS rule_version, {batch_id} AS batch_id FROM spikes
            """
        )
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_term_signal_daily AS
            WITH qualified AS (
                SELECT * FROM seat_dash_contract_seat_daily
                WHERE abs(net_position) >= {self.TERM_MIN_LEG_POSITION}
            ), opposite_pairs AS (
                SELECT n.trade_date, n.product_code, n.seat_id, n.seat_name,
                       n.contract_code AS near_contract_code,
                       f.contract_code AS far_contract_code,
                       n.net_position AS near_net_position,
                       f.net_position AS far_net_position,
                       n.contract_rank AS near_rank,
                       f.contract_rank AS far_rank,
                       row_number() OVER (
                           PARTITION BY n.trade_date, n.product_code, n.seat_id
                           ORDER BY n.contract_rank, f.contract_rank
                       ) AS pair_rank
                FROM qualified n
                INNER JOIN qualified f
                    ON f.trade_date = n.trade_date
                   AND f.product_code = n.product_code
                   AND f.seat_id = n.seat_id
                   AND f.contract_rank > n.contract_rank
                   AND f.net_position * n.net_position < 0
            ), all_legs AS (
                SELECT trade_date, product_code, seat_id,
                       count(*) FILTER (WHERE net_position <> 0) AS leg_count,
                       CAST(to_json(list(
                           struct_pack(
                               contract_code := contract_code,
                               symbol := symbol,
                               contract_rank := contract_rank,
                               net_position := net_position
                           ) ORDER BY contract_rank
                       )) AS VARCHAR) AS legs_json
                FROM seat_dash_contract_seat_daily
                GROUP BY trade_date, product_code, seat_id
            )
            SELECT p.trade_date, p.product_code,
                   p.near_contract_code, p.far_contract_code,
                   p.seat_id, p.seat_name,
                   p.near_net_position, p.far_net_position,
                   CASE WHEN p.near_net_position > 0 THEN 'near_long_far_short'
                        ELSE 'near_short_far_long' END AS direction,
                   {self.TERM_MIN_LEG_POSITION} AS min_leg_position,
                   CAST(l.leg_count AS BIGINT) AS leg_count,
                   l.legs_json,
                   'medium' AS severity,
                   0.75 AS confidence,
                   CAST(json_object(
                       'near_abs', abs(p.near_net_position),
                       'far_abs', abs(p.far_net_position),
                       'leg_count', l.leg_count
                   ) AS VARCHAR) AS evidence_json,
                   '{self.RULE_VERSION}' AS rule_version,
                   {batch_id} AS batch_id
            FROM opposite_pairs p
            INNER JOIN all_legs l USING (trade_date, product_code, seat_id)
            WHERE p.pair_rank = 1
            """
        )

    def _build_validation(self, connection: duckdb.DuckDBPyConnection, request: BuildRequest, batch_id: int) -> None:
        target = (
            f"trade_date = DATE '{request.trade_date.isoformat()}'"
            if request.trade_date
            else "trade_date = (SELECT max(trade_date) FROM seat_dash_product_daily)"
        )
        main_prices = ("SELECT product_code,trade_date,close FROM seat_dash_main_contract_coverage"
                       if self.source.p2_only else "SELECT product_code,trade_date,max(close) AS close FROM seat_dash_main_contract_seat_daily WHERE close IS NOT NULL GROUP BY product_code,trade_date")
        factors = ("SELECT c.trade_date,c.product_code,p.net_cr5,p.f2_value FROM seat_dash_main_contract_coverage c LEFT JOIN seat_dash_product_daily p USING (trade_date,product_code)"
                   if self.source.p2_only else "SELECT trade_date,product_code,net_cr5,f2_value FROM seat_dash_product_daily")
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE seat_dash_rule_result_daily AS
            WITH main_price AS ({main_prices}), forward_prices AS (
                SELECT product_code, trade_date, close,
                       lead(trade_date, 5) OVER (
                           PARTITION BY product_code ORDER BY trade_date
                       ) AS future_5_date,
                       lead(close, 5) OVER (
                           PARTITION BY product_code ORDER BY trade_date
                       ) AS future_5_close,
                       lead(trade_date, 10) OVER (
                           PARTITION BY product_code ORDER BY trade_date
                       ) AS future_10_date,
                       lead(close, 10) OVER (
                           PARTITION BY product_code ORDER BY trade_date
                       ) AS future_10_close
                FROM main_price
            ), factors AS ({factors}), factor_long AS (
                SELECT trade_date, product_code, 'net_cr5_concentration' AS rule_code,
                       net_cr5 AS factor_value
                FROM factors
                {"" if self.source.p2_only else "WHERE net_cr5 IS NOT NULL"}
                UNION ALL
                SELECT trade_date, product_code, 'f2_position_ratio', f2_value
                FROM factors
                {"" if self.source.p2_only else "WHERE f2_value IS NOT NULL"}
            ), indexed AS (
                SELECT *, row_number() OVER (
                    PARTITION BY product_code, rule_code ORDER BY trade_date
                ) AS date_index
                FROM factor_long
            ), current_rows AS (
                SELECT * FROM indexed WHERE {target} AND factor_value IS NOT NULL
            ), current_percentile AS (
                SELECT c.trade_date, c.product_code, c.rule_code, c.factor_value,
                       c.date_index,
                       100.0 * sum(CASE WHEN h.factor_value <= c.factor_value THEN 1 ELSE 0 END)
                           / nullif(count(h.factor_value), 0) AS current_percentile
                FROM current_rows c
                INNER JOIN indexed h
                    ON h.product_code = c.product_code
                   AND h.rule_code = c.rule_code
                   AND h.date_index BETWEEN greatest(1, c.date_index - {self.VALIDATION_WINDOW - 1})
                                        AND c.date_index
                GROUP BY c.trade_date, c.product_code, c.rule_code, c.factor_value, c.date_index
            ), cutoff AS (
                SELECT c.*, l.trade_date AS library_cutoff, l.date_index AS cutoff_index,
                       CASE WHEN c.current_percentile >= 70 THEN 'high'
                            WHEN c.current_percentile <= 30 THEN 'low'
                            ELSE 'middle' END AS band
                FROM current_percentile c
                LEFT JOIN indexed l
                    ON l.product_code = c.product_code
                   AND l.rule_code = c.rule_code
                   AND l.date_index = c.date_index - {self.VALIDATION_EMBARGO_DAYS}
            ), library AS (
                SELECT c.trade_date AS evaluation_date, c.product_code, c.rule_code,
                       c.current_percentile, c.band, c.library_cutoff, c.cutoff_index,
                       h.trade_date AS sample_date, h.factor_value
                FROM cutoff c
                INNER JOIN indexed h
                    ON h.product_code = c.product_code
                   AND h.rule_code = c.rule_code
                   AND h.date_index BETWEEN greatest(1, c.cutoff_index - {self.VALIDATION_LIBRARY_WINDOW - 1})
                                        AND c.cutoff_index
                WHERE c.library_cutoff IS NOT NULL
            ), thresholds AS (
                SELECT evaluation_date, product_code, rule_code, current_percentile,
                       band, library_cutoff,
                       quantile_cont(factor_value, 0.30) AS low_threshold,
                       quantile_cont(factor_value, 0.70) AS high_threshold
                FROM library
                GROUP BY evaluation_date, product_code, rule_code,
                         current_percentile, band, library_cutoff
            ), comparable AS (
                SELECT l.*, t.low_threshold, t.high_threshold,
                       fp.close, fp.future_5_date, fp.future_5_close,
                       fp.future_10_date, fp.future_10_close
                FROM library l
                INNER JOIN thresholds t USING (
                    evaluation_date, product_code, rule_code,
                    current_percentile, band, library_cutoff
                )
                LEFT JOIN forward_prices fp
                    ON fp.product_code = l.product_code AND fp.trade_date = l.sample_date
                WHERE (l.band = 'high' AND l.factor_value >= t.high_threshold)
                   OR (l.band = 'low' AND l.factor_value <= t.low_threshold)
            ), expanded AS (
                SELECT evaluation_date, product_code, rule_code, current_percentile,
                       band, library_cutoff, 5 AS horizon_days,
                       CASE WHEN close IS NULL OR close = 0 OR future_5_date > library_cutoff THEN NULL
                            ELSE future_5_close / close - 1 END AS forward_return
                FROM comparable
                UNION ALL
                SELECT evaluation_date, product_code, rule_code, current_percentile,
                       band, library_cutoff, 10 AS horizon_days,
                       CASE WHEN close IS NULL OR close = 0 OR future_10_date > library_cutoff THEN NULL
                            ELSE future_10_close / close - 1 END AS forward_return
                FROM comparable
            ), grouped AS (
                SELECT evaluation_date AS trade_date, product_code, rule_code,
                       horizon_days, current_percentile, band, library_cutoff,
                       count(forward_return) AS samples,
                       avg(forward_return) AS mean_return,
                       median(forward_return) AS median_return,
                       sum(CASE WHEN forward_return > 0 THEN 1 ELSE 0 END) AS wins
                FROM expanded
                GROUP BY evaluation_date, product_code, rule_code,
                         horizon_days, current_percentile, band, library_cutoff
            ), expected AS (
                SELECT c.trade_date, c.product_code, c.rule_code,
                       h.horizon_days, c.current_percentile, c.band, c.library_cutoff
                FROM cutoff c
                CROSS JOIN (VALUES (5), (10)) AS h(horizon_days)
            ), scored AS (
                SELECT e.*, CAST(coalesce(g.samples, 0) AS BIGINT) AS samples,
                       g.mean_return, g.median_return, g.wins,
                       CASE WHEN coalesce(g.samples, 0) = 0 THEN NULL
                            ELSE g.wins * 1.0 / g.samples END AS win_rate_value
                FROM expected e
                LEFT JOIN grouped g
                    ON g.trade_date = e.trade_date
                   AND g.product_code = e.product_code
                   AND g.rule_code = e.rule_code
                   AND g.horizon_days = e.horizon_days
            )
            SELECT trade_date, product_code, rule_code, horizon_days,
                   CASE WHEN rule_code = 'net_cr5_concentration' AND mean_return > 0 THEN 'long'
                        WHEN rule_code = 'net_cr5_concentration' AND mean_return < 0 THEN 'short'
                        ELSE 'neutral' END AS signal_direction,
                   current_percentile,
                   CAST(coalesce(samples, 0) AS BIGINT) AS sample_size,
                   mean_return, median_return,
                   win_rate_value AS win_rate,
                   CASE WHEN samples = 0 THEN NULL
                        ELSE greatest(0.0,
                            (win_rate_value + 3.8416 / (2 * samples)
                             - 1.96 * sqrt(win_rate_value * (1 - win_rate_value) / samples
                                           + 3.8416 / (4 * samples * samples)))
                            / (1 + 3.8416 / samples)) END AS confidence_low,
                   CASE WHEN samples = 0 THEN NULL
                        ELSE least(1.0,
                            (win_rate_value + 3.8416 / (2 * samples)
                             + 1.96 * sqrt(win_rate_value * (1 - win_rate_value) / samples
                                           + 3.8416 / (4 * samples * samples)))
                            / (1 + 3.8416 / samples)) END AS confidence_high,
                   band,
                   CASE WHEN rule_code = 'net_cr5_concentration' AND mean_return > 0 THEN '偏多'
                        WHEN rule_code = 'net_cr5_concentration' AND mean_return < 0 THEN '偏空'
                        ELSE '规律不可用' END AS signal_label,
                   CASE WHEN rule_code <> 'net_cr5_concentration' THEN 'unavailable'
                        WHEN samples >= 60 AND abs(win_rate_value - 0.5) >= 0.03 THEN 'medium'
                        WHEN samples >= 30 THEN 'low'
                        ELSE 'unavailable' END AS confidence_label,
                   library_cutoff,
                   CASE WHEN rule_code <> 'net_cr5_concentration' THEN 'unavailable'
                        WHEN samples >= 60 AND abs(win_rate_value - 0.5) >= 0.03 THEN 'ok'
                        WHEN samples >= 30 THEN 'sample_insufficient'
                        ELSE 'unavailable' END AS status,
                   'main_contract_close_5y_embargo25' AS return_basis,
                   '{self.RULE_VERSION}' AS rule_version,
                   {batch_id} AS batch_id
            FROM scored
            """
        )

    @staticmethod
    def _row_counts(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
        tables = [
            "seat_dash_product_daily",
            "seat_dash_product_seat_daily",
            "seat_dash_main_contract_seat_daily",
            "seat_dash_contract_daily",
            "seat_dash_contract_seat_daily",
            "seat_dash_market_daily",
            "seat_dash_combination_signal_daily",
            "seat_dash_seat_signal_daily",
            "seat_dash_term_signal_daily",
            "seat_dash_rule_result_daily",
            "seat_dash_product_dim",
            "seat_dash_seat_dim",
            "seat_dash_tag_definition",
            "seat_dash_seat_tag_assignment",
            "seat_dash_combination",
            "seat_dash_combination_member",
        ]
        return {table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}

    @staticmethod
    def _date_range(connection: duckdb.DuckDBPyConnection) -> tuple[date | None, date | None]:
        row = connection.execute("SELECT min(trade_date), max(trade_date) FROM seat_dash_product_daily").fetchone()
        return (row[0], row[1]) if row else (None, None)

    def validate_snapshot(self, connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        required = [
            "seat_dash_product_daily",
            "seat_dash_product_seat_daily",
            "seat_dash_main_contract_seat_daily",
            "seat_dash_contract_daily",
            "seat_dash_contract_seat_daily",
            "seat_dash_combination_signal_daily",
            "seat_dash_seat_signal_daily",
            "seat_dash_term_signal_daily",
            "seat_dash_rule_result_daily",
            "seat_dash_product_dim",
            "seat_dash_seat_dim",
            "seat_dash_tag_definition",
            "seat_dash_combination",
            "seat_dash_etl_run",
            "seat_dash_publish_state",
        ]
        checks["required_tables"] = {table: True for table in required}
        checks["product_daily_rows"] = int(connection.execute("SELECT count(*) FROM seat_dash_product_daily").fetchone()[0])
        checks["product_seat_rows"] = int(connection.execute("SELECT count(*) FROM seat_dash_product_seat_daily").fetchone()[0])
        checks["net_position_anomalies"] = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_seat_daily "
                "WHERE net_position <> long_position - short_position"
            ).fetchone()[0]
        )
        checks["net_change_anomalies"] = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_seat_daily "
                "WHERE net_change <> long_change - short_change"
            ).fetchone()[0]
        )
        checks["f2_ready_rows"] = int(
            connection.execute(
                "SELECT count(*) FROM seat_dash_product_daily "
                "WHERE f2_status = 'ready' AND f2_value IS NOT NULL"
            ).fetchone()[0]
        )
        checks["combination_signal_rows"] = int(
            connection.execute("SELECT count(*) FROM seat_dash_combination_signal_daily").fetchone()[0]
        )
        checks["combination_member_rows"] = int(
            connection.execute("SELECT count(*) FROM seat_dash_combination_member").fetchone()[0]
        )
        checks["market_rows"] = int(connection.execute("SELECT count(*) FROM seat_dash_market_daily").fetchone()[0])
        checks["warnings"] = []
        if "seat_dash_main_contract_coverage" in {row[0] for row in connection.execute("SHOW TABLES").fetchall()}:
            checks["main_price_pending"] = connection.execute("SELECT count(*) FROM seat_dash_main_contract_coverage WHERE price_status<>'ready'").fetchone()[0]
            checks["main_seat_pending"] = connection.execute("SELECT count(*) FROM seat_dash_main_contract_coverage WHERE seat_status<>'ready'").fetchone()[0]
            checks["warnings"].append("P2 backfill provenance unavailable; not certified")
            if checks["main_price_pending"] or checks["main_seat_pending"]:
                checks["warnings"].append(f"main layer pending: price={checks['main_price_pending']}, seat={checks['main_seat_pending']}")
        if checks["market_rows"] == 0:
            checks["warnings"].append("market_daily_empty; validation results will be unavailable")
        checks["complete"] = (
            checks["product_daily_rows"] > 0
            and checks["product_seat_rows"] > 0
            and checks["net_position_anomalies"] == 0
            and checks["net_change_anomalies"] == 0
            and checks["f2_ready_rows"] > 0
        )
        return checks


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a versioned local DuckDB dashboard snapshot")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reclassify-from", type=Path, help="Rebuild group signals from a published runtime without source access")
    mode.add_argument("--full", action="store_true", help="build every available date")
    mode.add_argument("--date", type=date.fromisoformat, help="build one trade date")
    parser.add_argument("--p2-only", action="store_true", help="derive source relations from raw seats and daily market data; ignore legacy P3")
    parser.add_argument("--dry-run", action="store_true", help="build and validate in memory without publishing")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--seat-root", type=Path)
    parser.add_argument("--market-root", type=Path)
    parser.add_argument("--seat-tag-config", type=Path)
    parser.add_argument("--combination-config", type=Path)
    parser.add_argument("--product-config", type=Path)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_logging(args.log_level)
    settings = Settings.from_env()
    if args.runtime_root:
        settings = Settings(
            **{**settings.__dict__, "runtime_root": args.runtime_root.resolve()}
        )
    if args.seat_root:
        settings = Settings(
            **{**settings.__dict__, "seat_data_root": args.seat_root.resolve()}
        )
    if args.market_root:
        settings = Settings(
            **{**settings.__dict__, "market_1d_root": args.market_root.resolve()}
        )
    if args.seat_tag_config:
        settings = Settings(
            **{**settings.__dict__, "seat_tag_config": args.seat_tag_config.resolve()}
        )
    if args.combination_config:
        settings = Settings(
            **{**settings.__dict__, "combination_config": args.combination_config.resolve()}
        )
    if args.product_config:
        settings = Settings(
            **{**settings.__dict__, "product_config": args.product_config.resolve()}
        )
    try:
        if settings.source_backend != "parquet":
            raise RuntimeError(
                f"Unsupported SEAT_SOURCE_BACKEND: {settings.source_backend}; V1 requires parquet"
            )
        unsupported_publishers = sorted(set(settings.publish_stores) - {"duckdb"})
        if unsupported_publishers:
            raise RuntimeError(
                "RDS publishing is reserved but not enabled in V1; unsupported publishers: "
                + ", ".join(unsupported_publishers)
            )
        source = ParquetSource(settings.seat_data_root, settings.market_1d_root, p2_only=args.p2_only)
        publisher = DuckDBPublisher(settings.runtime_root)
        request = BuildRequest(full=bool(args.full), trade_date=args.date, dry_run=bool(args.dry_run))
        builder = SnapshotBuilder(
            source,
            publisher,
            settings.seat_tag_config,
            settings.combination_config,
            settings.product_config,
        )
        if args.reclassify_from and args.dry_run:
            raise ValueError("reclassify publishes a separate snapshot; use an isolated --runtime-root")
        metadata = builder.reclassify(args.reclassify_from) if args.reclassify_from else builder.build(request)
    except Exception as exc:
        logger.exception("snapshot build failed")
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **metadata}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
