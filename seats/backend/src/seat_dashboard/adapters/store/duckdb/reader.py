from __future__ import annotations

import json
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import duckdb

from seat_dashboard.core.errors import InvalidDashboardQuery, SnapshotUnavailable
from seat_dashboard.ports.dashboard_reader import DashboardReadPort


def _as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _one(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    return next(iter(rows), None)


class DuckDBReader(DashboardReadPort):
    """Read-only page queries against the published DuckDB snapshot."""

    QUERY_VERSION = "seat-queries-v8-contract-universe"
    COMBINATION_ORDER = (
        "ORDER BY CASE c.combination_id WHEN 'industrial_major' THEN 1 "
        "WHEN 'brokerage_proprietary' THEN 2 WHEN 'capital_strike' THEN 3 "
        "WHEN 'foreign_seats' THEN 4 WHEN 'wolong_fengchu' THEN 5 ELSE 6 END, c.name"
    )
    ALLOWED_VIEWS = {"combination", "market-signals", "term-structure", "validation"}

    def __init__(self, runtime_root: Path, snapshot_path: Path | None = None, backend_label: str = "local", threads: int = 1, memory_mb: int = 256):
        self.runtime_root = Path(runtime_root).resolve()
        self.snapshot_path = Path(snapshot_path).resolve() if snapshot_path else None
        self.manifest_path = self.runtime_root / "manifests" / "current.json"
        self.backend_label = backend_label
        if threads < 1 or memory_mb < 64:
            raise ValueError("DuckDB requires positive threads and at least 64 MB memory")
        self.connection_config = {"threads": threads, "memory_limit": f"{memory_mb}MB"}
        self._pinned: ContextVar[dict[str, Any] | None] = ContextVar("seat_snapshot", default=None)

    @contextmanager
    def read_session(self):
        manifest = self._manifest()
        stat = Path(manifest["database_path"]).stat()
        version = hashlib.sha256(json.dumps([self.QUERY_VERSION, manifest, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns], sort_keys=True, default=str).encode()).hexdigest()
        token = self._pinned.set({**manifest, "data_version": version})
        try:
            yield version
        finally:
            self._pinned.reset(token)

    def _manifest(self) -> dict[str, Any]:
        if self._pinned.get() is not None:
            return self._pinned.get()
        if self.snapshot_path:
            if not self.snapshot_path.is_file():
                raise SnapshotUnavailable(f"Snapshot does not exist: {self.snapshot_path}")
            return {
                "batch_id": 0,
                "trade_date": None,
                "database_path": str(self.snapshot_path),
                "complete": True,
                "data_backend": self.backend_label,
            }
        if not self.manifest_path.is_file():
            raise SnapshotUnavailable(f"No published snapshot manifest: {self.manifest_path}")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotUnavailable("Current snapshot manifest cannot be read") from exc
        database_path = Path(manifest.get("database_path", ""))
        if not database_path.is_absolute():
            database_path = (self.runtime_root / database_path).resolve()
        manifest["database_path"] = str(database_path)
        if not manifest.get("complete") or not database_path.is_file():
            raise SnapshotUnavailable("Current snapshot is not complete or its database file is missing")
        return manifest

    def _connect(self) -> tuple[duckdb.DuckDBPyConnection, dict[str, Any]]:
        manifest = self._manifest()
        connection = duckdb.connect(manifest["database_path"], read_only=True, config=self.connection_config)
        return connection, manifest

    def snapshot_status(self) -> dict[str, Any]:
        manifest = self._manifest()
        trade_date = manifest.get("trade_date")
        if isinstance(trade_date, str):
            try:
                trade_date = date.fromisoformat(trade_date)
            except ValueError:
                pass
        return {
            "batch_id": manifest.get("batch_id"),
            "data_version": manifest.get("data_version"),
            "metric_version": manifest.get("metric_version"),
            "combination_version": manifest.get("combination_version"),
            "rule_version": manifest.get("rule_version"),
            "trade_date": trade_date,
            "updated_at": manifest.get("published_at"),
            "data_backend": manifest.get("data_backend", self.backend_label),
            "complete": bool(manifest.get("complete")),
            "source_manifest_sha256": manifest.get("source_manifest_sha256"),
            "warnings": manifest.get("warnings", []),
            "is_demo": bool(manifest.get("is_demo", False)),
            "backfill_status": manifest.get("backfill_status", "unspecified"),
        }

    @staticmethod
    def _date_filter(value: date | None, column: str = "trade_date") -> tuple[str, list[Any]]:
        if value is None:
            return "", []
        return f" AND {column} = ?", [value]

    @staticmethod
    def _product_filter(value: str | None, column: str = "product_code") -> tuple[str, list[Any]]:
        if not value:
            return "", []
        normalized = value.strip().upper()
        if normalized in {"ALL", "*"}:
            return "", []
        if not normalized or len(normalized) > 64:
            raise InvalidDashboardQuery("product_code is invalid")
        return f" AND {column} = ?", [normalized]

    @staticmethod
    def _safe_limit(limit: int, maximum: int = 100_000) -> int:
        if limit < 1 or limit > maximum:
            raise InvalidDashboardQuery(f"limit must be between 1 and {maximum}")
        return int(limit)

    def options(self) -> dict[str, Any]:
        connection, manifest = self._connect()
        try:
            products = _as_dicts(
                connection.execute(
                    "SELECT product_code, root, exchange, display_name FROM seat_dash_product_dim ORDER BY product_code"
                )
            )
            combinations = _as_dicts(
                connection.execute(
                    "SELECT c.combination_id, c.name, c.scope, count(m.seat_id) AS member_count "
                    "FROM seat_dash_combination c LEFT JOIN seat_dash_combination_member m "
                    "ON m.combination_id = c.combination_id WHERE c.active "
                    "GROUP BY c.combination_id, c.name, c.scope, c.is_system "
                    + self.COMBINATION_ORDER
                )
            )
            tags = _as_dicts(
                connection.execute(
                    "SELECT tag_code, label, category, color_token, description "
                    "FROM seat_dash_tag_definition ORDER BY category, label"
                )
            )
            latest = _one(
                _as_dicts(connection.execute("SELECT max(trade_date) AS trade_date FROM seat_dash_product_daily"))
            )
            return {
                "trade_date": latest["trade_date"] if latest else manifest.get("trade_date"),
                "products": products,
                "combinations": combinations,
                "tags": tags,
            }
        finally:
            connection.close()

    def _resolve_date(self, connection: duckdb.DuckDBPyConnection, value: date | None) -> date:
        if value:
            return value
        row = _one(_as_dicts(connection.execute("SELECT max(trade_date) AS trade_date FROM seat_dash_product_daily")))
        if not row or row["trade_date"] is None:
            raise SnapshotUnavailable("Snapshot has no product daily rows")
        return row["trade_date"]

    def _metrics(
        self, connection: duckdb.DuckDBPyConnection, selected_date: date, product_code: str | None
    ) -> list[dict[str, Any]]:
        date_sql, date_params = self._date_filter(selected_date)
        product_sql, product_params = self._product_filter(product_code)
        return _as_dicts(
            connection.execute(
                "SELECT * FROM seat_dash_product_daily WHERE 1=1" + date_sql + product_sql + " ORDER BY product_code",
                [*date_params, *product_params],
            )
        )

    @staticmethod
    def _static_tags(
        connection: duckdb.DuckDBPyConnection,
        selected_date: date,
        product_code: str | None,
        seat_ids: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        unique_ids = sorted({seat_id for seat_id in seat_ids if seat_id})
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        product_clause = " AND a.product_code IS NULL"
        params: list[Any] = [*unique_ids, selected_date, selected_date]
        if product_code:
            product_clause = " AND (a.product_code IS NULL OR a.product_code = ?)"
            params.append(product_code.strip().upper())
        rows = _as_dicts(
            connection.execute(
                "SELECT a.seat_id, d.label AS name, d.color_token AS tone, a.source, "
                "a.confidence, a.valid_from, a.valid_to, a.evidence_json, a.note "
                "FROM seat_dash_seat_tag_assignment a "
                "INNER JOIN seat_dash_tag_definition d ON d.tag_code = a.tag_code "
                f"WHERE a.seat_id IN ({placeholders}) "
                "AND (a.valid_from IS NULL OR a.valid_from <= ?) "
                "AND (a.valid_to IS NULL OR a.valid_to >= ?)"
                + product_clause
                + " ORDER BY a.seat_id, a.product_code NULLS FIRST, d.label",
                params,
            )
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            seat_id = row.pop("seat_id")
            result.setdefault(seat_id, []).append(row)
        return result

    @staticmethod
    def _attach_static_tags(
        rows: Iterable[dict[str, Any]],
        tags_by_seat: dict[str, list[dict[str, Any]]],
    ) -> None:
        for row in rows:
            row["tags"] = tags_by_seat.get(row.get("seat_id", ""), [])

    def view(
        self,
        view_name: str,
        trade_date: date | None = None,
        product_code: str | None = None,
        scope: str = "all",
        limit: int = 20,
        min_leg_position: int = 300,
        detail: str = "all",
    ) -> dict[str, Any]:
        if view_name not in self.ALLOWED_VIEWS:
            raise InvalidDashboardQuery(f"unknown view: {view_name}")
        if detail not in {"combination": {"all", "groups", "positions"}, "term-structure": {"all", "contracts"}}.get(view_name, {"all"}):
            raise InvalidDashboardQuery("unknown view detail")
        if detail == "groups":
            product_code = None
        if detail == "positions" and (not product_code or product_code.upper() in {"ALL", "*"}):
            raise InvalidDashboardQuery("positions requires a product")
        limit = self._safe_limit(limit, 1_000)
        if min_leg_position < 0 or min_leg_position > 10_000_000:
            raise InvalidDashboardQuery("min_leg_position is invalid")
        connection, manifest = self._connect()
        try:
            selected_date = self._resolve_date(connection, trade_date)
            metrics = self._metrics(connection, selected_date, product_code)
            if view_name == "combination":
                data = self._combination(connection, selected_date, product_code, metrics, limit, detail)
            elif view_name == "market-signals":
                data = self._market_signals(connection, selected_date, product_code, metrics, limit)
            elif view_name == "term-structure":
                data = self._term_structure(
                    connection, selected_date, product_code, metrics, limit, min_leg_position, detail
                )
            else:
                data = self._validation(connection, selected_date, product_code, metrics, limit)
            if manifest.get("input_mode") == "p2-only-v1":
                product_sql, product_params = self._product_filter(product_code)
                data["main_contract_coverage"] = _as_dicts(connection.execute(
                    "SELECT * FROM seat_dash_main_contract_coverage WHERE trade_date=?" + product_sql + " ORDER BY product_code",
                    [selected_date, *product_params]))
            return {"selected_date": selected_date, "scope": scope, **data}
        finally:
            connection.close()

    def _seat_positions(self, connection, selected_date, normalized_product, order, limit, total=False):
        date_sql, date_params = self._date_filter(selected_date)
        product_sql, product_params = self._product_filter(normalized_product)
        if normalized_product:
            top_sql = (
                "SELECT product_code, seat_id, seat_name, long_position, long_change, "
                "short_position, short_change, net_position, net_change, abs_net_position, "
                "previous_net_position, net_position_change, entry_state "
                "FROM seat_dash_product_seat_daily WHERE 1=1"
                + date_sql
                + product_sql
            )
        else:
            top_sql = (
                "SELECT 'ALL' AS product_code, seat_id, max(seat_name) AS seat_name, "
                "sum(long_position) AS long_position, sum(long_change) AS long_change, "
                "sum(short_position) AS short_position, sum(short_change) AS short_change, "
                "sum(net_position) AS net_position, sum(net_change) AS net_change, "
                "abs(sum(net_position)) AS abs_net_position, NULL AS previous_net_position, "
                "sum(net_position_change) AS net_position_change, 'aggregate' AS entry_state "
                "FROM seat_dash_product_seat_daily WHERE 1=1"
                + date_sql
                + " GROUP BY seat_id"
            )
        params = [*date_params, *product_params]
        top_sql = f"SELECT *{', sum(abs(net_position)) OVER () AS total_abs_net' if total else ''} FROM ({top_sql}) ranked"
        return _as_dicts(connection.execute(top_sql + f" ORDER BY {order} LIMIT ?", [*params, limit]))

    def _combination(
        self,
        connection: duckdb.DuckDBPyConnection,
        selected_date: date,
        product_code: str | None,
        metrics: list[dict[str, Any]],
        limit: int,
        detail: str = "all",
    ) -> dict[str, Any]:
        normalized_product = (
            product_code.strip().upper()
            if product_code and product_code.strip().upper() not in {"ALL", "*"}
            else None
        )
        net, long_rows, short_rows, signal_rows, top_changes = [], [], [], [], []
        if detail != "groups":
            net = self._seat_positions(connection, selected_date, normalized_product, "abs_net_position DESC, seat_name", limit, total=True)
            long_rows = self._seat_positions(connection, selected_date, normalized_product, "long_position DESC, seat_name", limit)
            short_rows = self._seat_positions(connection, selected_date, normalized_product, "short_position DESC, seat_name", limit)
            total_abs_net = net[0]["total_abs_net"] if net else 0
            total_long = sum((metric.get("total_long") or 0) for metric in metrics)
            total_short = sum((metric.get("total_short") or 0) for metric in metrics)
            for row in net:
                row.pop("total_abs_net")
                row["net_share"] = abs(row["net_position"]) / total_abs_net if total_abs_net else None
            for row in long_rows:
                row["position_share"] = row["long_position"] / total_long if total_long else None
            for row in short_rows:
                row["position_share"] = row["short_position"] / total_short if total_short else None
            signal_rows = self._signals(connection, selected_date, normalized_product, max(limit, 50))
            if detail == "positions":
                top_changes = self._seat_positions(connection, selected_date, normalized_product, "abs(net_position_change) DESC, seat_name", min(limit, 10))
            all_rows = [*net, *long_rows, *short_rows, *signal_rows, *top_changes]
            self._attach_static_tags(all_rows, self._static_tags(connection, selected_date, normalized_product, (row.get("seat_id", "") for row in all_rows)))
        rankings = {"net_position_top": net, "long_top": long_rows, "short_top": short_rows, "signals": signal_rows, "top_changes": top_changes}
        if detail == "positions":
            return {**rankings, "metrics": metrics, "combinations": [], "combination_signals": [], "combination_signal_product_count": 0, "divergence_cards": [], "insight": self._insight(metrics, signal_rows)}
        combinations = _as_dicts(
            connection.execute(
                "SELECT c.combination_id, c.name, c.scope, c.is_system, "
                "count(m.seat_id) AS member_count, "
                "coalesce(string_agg(s.seat_name, '、' ORDER BY s.seat_name), '') AS members_text "
                "FROM seat_dash_combination c "
                "LEFT JOIN seat_dash_combination_member m ON m.combination_id = c.combination_id "
                "LEFT JOIN seat_dash_seat_dim s ON s.seat_id = m.seat_id "
                "WHERE c.active GROUP BY c.combination_id, c.name, c.scope, c.is_system "
                "HAVING count(m.seat_id) > 0 "
                + self.COMBINATION_ORDER
            )
        )
        for combination in combinations:
            combination["members"] = (
                combination.pop("members_text").split("、") if combination["member_count"] else []
            )
        combination_positions = _as_dicts(connection.execute(
            "WITH identities AS ("
            "SELECT p.product_code, coalesce(a.seat_id, p.seat_id) AS seat_id, "
            "sum(p.net_position) AS net_position, sum(p.net_change) AS net_change "
            "FROM seat_dash_product_seat_daily p "
            "LEFT JOIN seat_dash_seat_alias a ON a.source_seat_name = p.seat_name "
            "WHERE p.trade_date = ? GROUP BY p.product_code, coalesce(a.seat_id, p.seat_id)), "
            "observed AS ("
            "SELECT p.product_code, m.combination_id, count(*) AS observed_count, "
            "count(*) FILTER (WHERE p.net_position > 0) AS long_count, "
            "count(*) FILTER (WHERE p.net_position < 0) AS short_count, "
            "count(*) FILTER (WHERE p.net_position = 0) AS flat_count, "
            "sum(p.net_position)::BIGINT AS net_position, sum(p.net_change)::BIGINT AS net_change "
            "FROM identities p JOIN seat_dash_combination_member m USING (seat_id) "
            "GROUP BY p.product_code, m.combination_id), "
            "groups AS (SELECT c.combination_id, count(*) AS member_count "
            "FROM seat_dash_combination c JOIN seat_dash_combination_member m USING (combination_id) "
            "WHERE c.active GROUP BY c.combination_id) "
            "SELECT p.product_code, d.root, d.exchange, d.display_name, g.combination_id, "
            "o.net_position, o.net_change, coalesce(o.long_count, 0) AS long_count, "
            "coalesce(o.short_count, 0) AS short_count, coalesce(o.flat_count, 0) AS flat_count, "
            "g.member_count - coalesce(o.observed_count, 0) AS missing_count "
            "FROM seat_dash_product_daily p JOIN seat_dash_product_dim d USING (product_code) "
            "CROSS JOIN groups g LEFT JOIN observed o ON o.product_code = p.product_code "
            "AND o.combination_id = g.combination_id WHERE p.trade_date = ? "
            "ORDER BY d.exchange, d.root, g.combination_id", [selected_date, selected_date],
        ))
        combination_signals = _as_dicts(
            connection.execute(
                "SELECT s.*, p.root, p.exchange, p.display_name, c.name AS combination_name "
                "FROM seat_dash_combination_signal_daily s "
                "INNER JOIN seat_dash_product_dim p ON p.product_code = s.product_code "
                "INNER JOIN seat_dash_combination c ON c.combination_id = s.combination_id "
                "WHERE s.trade_date = ? "
                "ORDER BY p.exchange, p.root, c.name",
                [selected_date],
            )
        )
        names = {row["combination_id"]: row["name"] for row in combinations}
        pair = [names[key] for key in ("capital_strike", "wolong_fengchu") if key in names]
        left = "投机" if names.get("capital_strike") == "投机交易" else names.get("capital_strike", "")
        right = "家人" if names.get("wolong_fengchu") == "家人席位" else names.get("wolong_fengchu", "")
        divergence_pairs = [
            ("main_add_short_retail_add_long", f"{left}加空 · {right}加多", "add_short", "add_long"),
            ("main_add_long_retail_add_short", f"{left}加多 · {right}加空", "add_long", "add_short"),
            ("main_reduce_long_retail_reduce_short", f"{left}减多 · {right}减空", "reduce_long", "reduce_short"),
            ("main_reduce_short_retail_reduce_long", f"{left}减空 · {right}减多", "reduce_short", "reduce_long"),
        ] if len(pair) == 2 else []
        by_product: dict[str, dict[str, dict[str, Any]]] = {}
        for row in combination_signals:
            by_product.setdefault(row["product_code"], {})[row["combination_id"]] = row
        divergence_cards: list[dict[str, Any]] = []
        for code, label, main_action, retail_action in divergence_pairs:
            products = [
                {
                    "product_code": product,
                    "display_name": rows["capital_strike"].get("display_name", product),
                }
                for product, rows in by_product.items()
                if rows.get("capital_strike", {}).get("action_code") == main_action
                and rows.get("wolong_fengchu", {}).get("action_code") == retail_action
            ]
            divergence_cards.append({"code": code, "label": label, "count": len(products), "products": products})
        signaled_product_count = len(
            {
                row["product_code"]
                for row in combination_signals
                if (row.get("member_count") or 0) > 1
            }
        )
        validation_summary, term_count, anomaly_count = {}, 0, 0
        if detail == "all":
            validation_summary = _one(
                _as_dicts(
                    connection.execute(
                        "SELECT max(current_percentile) FILTER (WHERE rule_code = 'f2_position_ratio') "
                        "AS f2_percentile, max(current_percentile) FILTER "
                        "(WHERE rule_code = 'net_cr5_concentration') AS net_cr5_percentile, "
                        "max(library_cutoff) AS library_cutoff "
                        "FROM seat_dash_rule_result_daily WHERE trade_date = ?"
                        + (" AND product_code = ?" if normalized_product else ""),
                        [selected_date, *([normalized_product] if normalized_product else [])],
                    )
                )
            ) or {}
            term_count = connection.execute(
                "SELECT count(*) FROM seat_dash_term_signal_daily WHERE trade_date = ?"
                + (" AND product_code = ?" if normalized_product else ""),
                [selected_date, *([normalized_product] if normalized_product else [])],
            ).fetchone()[0]
            anomaly_count = connection.execute(
                "SELECT count(*) FROM seat_dash_seat_signal_daily WHERE trade_date = ?"
                + (" AND product_code = ?" if normalized_product else ""),
                [selected_date, *([normalized_product] if normalized_product else [])],
            ).fetchone()[0]
        return {
            "metrics": metrics,
            **rankings,
            "combinations": combinations,
            "combination_signals": combination_signals,
            "combination_positions": combination_positions,
            "combination_signal_product_count": signaled_product_count,
            "divergence_cards": divergence_cards,
            "divergence_pair": pair if len(pair) == 2 else [],
            "research_summary": {
                **validation_summary,
                "anomaly_count": int(anomaly_count),
                "term_opposite_count": min(int(term_count), 10),
            },
            "insight": self._insight(metrics, signal_rows),
        }

    @staticmethod
    def _signals(connection, selected_date, product_code, limit):
        return _as_dicts(connection.execute(
            "SELECT * FROM seat_dash_seat_signal_daily WHERE trade_date = ?"
            + (" AND product_code = ?" if product_code else "")
            + " ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, abs(metric_value) DESC LIMIT ?",
            [selected_date, *([product_code] if product_code else []), limit],
        ))

    def _market_signals(
        self,
        connection: duckdb.DuckDBPyConnection,
        selected_date: date,
        product_code: str | None,
        metrics: list[dict[str, Any]],
        limit: int,
    ) -> dict[str, Any]:
        normalized_product = (
            product_code.strip().upper()
            if product_code and product_code.strip().upper() not in {"ALL", "*"}
            else None
        )
        signals = self._signals(connection, selected_date, normalized_product, limit)
        treemap_sql = (
            "SELECT d.product_code, d.root, d.exchange, p.display_name, d.net_position, "
            "d.net_change, d.total_long, d.total_short, d.seat_count, d.long_cr5, "
            "d.short_cr5, d.net_cr5, d.divergence_state "
            "FROM seat_dash_product_daily d INNER JOIN seat_dash_product_dim p "
            "ON p.product_code = d.product_code WHERE d.trade_date = ?"
            + " ORDER BY abs(net_change) DESC LIMIT ?"
        )
        treemap = _as_dicts(
            connection.execute(
                treemap_sql,
                [selected_date, max(limit, 100)],
            )
        )
        interest_rows = _as_dicts(connection.execute(
            """
            WITH dates AS (
                SELECT CAST(? AS DATE) AS current_date,
                       max(trade_date) AS previous_date
                FROM seat_dash_market_daily WHERE trade_date < ?
            ), totals AS (
                SELECT trade_date, product_code, count(*) AS contracts,
                       count(*) FILTER (WHERE open_interest IS NULL OR open_interest < 0) AS invalid,
                       sum(open_interest) AS total,
                       string_agg(symbol, ',' ORDER BY symbol) AS symbols
                FROM seat_dash_market_daily, dates
                WHERE trade_date IN (dates.current_date, dates.previous_date)
                  AND NOT regexp_matches(symbol, '(8888|9998|9999)$')
                GROUP BY trade_date, product_code
            )
            SELECT c.product_code, d.previous_date AS previous_interest_date,
                   c.total AS open_interest, p.total AS previous_open_interest,
                   c.contracts AS interest_contract_count,
                   p.contracts AS previous_interest_contract_count,
                   c.symbols IS DISTINCT FROM p.symbols AS interest_contracts_changed,
                   CASE WHEN c.invalid = 0 AND p.invalid = 0 AND p.total > 0
                        THEN c.total * 1.0 / p.total - 1 END AS open_interest_change_rate
            FROM dates d INNER JOIN totals c ON c.trade_date = d.current_date
            LEFT JOIN totals p ON p.trade_date = d.previous_date AND p.product_code = c.product_code
            """, [selected_date, selected_date]
        ))
        interest = {row["product_code"]: row for row in interest_rows}
        for row in treemap:
            row.update(interest.get(row["product_code"], {}))
        severity_counts = _as_dicts(
            connection.execute(
                "SELECT signal_code, severity, count(*) AS count FROM seat_dash_seat_signal_daily "
                "WHERE trade_date = ?" + (" AND product_code = ?" if normalized_product else "") +
                " GROUP BY signal_code, severity ORDER BY severity, signal_code",
                [selected_date, *([normalized_product] if normalized_product else [])],
            )
        )
        static_tags = self._static_tags(
            connection,
            selected_date,
            normalized_product,
            (row.get("seat_id", "") for row in signals),
        )
        self._attach_static_tags(signals, static_tags)
        term_count = connection.execute(
            "SELECT count(*) FROM seat_dash_term_signal_daily WHERE trade_date = ? AND min_leg_position >= 300"
            + (" AND product_code = ?" if normalized_product else ""),
            [selected_date, *([normalized_product] if normalized_product else [])],
        ).fetchone()[0]
        return {"metrics": metrics, "anomalies": signals, "treemap": treemap, "signal_counts": severity_counts, "term_opposite_count": min(term_count, 10)}

    def _term_structure(
        self,
        connection: duckdb.DuckDBPyConnection,
        selected_date: date,
        product_code: str | None,
        metrics: list[dict[str, Any]],
        limit: int,
        min_leg_position: int,
        detail: str = "all",
    ) -> dict[str, Any]:
        normalized_product = (
            product_code.strip().upper()
            if product_code and product_code.strip().upper() not in {"ALL", "*"}
            else None
        )
        product_sql, product_params = self._product_filter(normalized_product)
        contracts = _as_dicts(
            connection.execute(
                "WITH market AS (SELECT * FROM seat_dash_market_daily WHERE trade_date = ?" + product_sql +
                " AND regexp_full_match(symbol, '[A-Z]+[0-9]{4}')"
                " AND try_cast(right(symbol, 2) AS INTEGER) BETWEEN 1 AND 12), "
                "codes AS (SELECT product_code, right(symbol, 3) AS suffix, min(symbol) AS symbol "
                "FROM market WHERE exchange = 'CZCE' GROUP BY product_code, suffix HAVING count(*) = 1), "
                "seats AS (SELECT c.* REPLACE (coalesce(k.symbol, c.symbol) AS symbol, "
                "coalesce(k.symbol || '.CZCE', c.contract_code) AS contract_code, "
                "coalesce(try_cast(right(k.symbol, 4) AS INTEGER), c.contract_month) AS contract_month) "
                "FROM (SELECT * FROM seat_dash_contract_daily WHERE trade_date = ?" + product_sql + ") c "
                "LEFT JOIN codes k ON c.product_code = k.product_code AND right(c.symbol, 3) = k.suffix "
                "AND regexp_full_match(c.symbol, '[A-Z]+[0-9]{3}')), "
                "contracts AS (SELECT coalesce(c.trade_date, m.trade_date) AS trade_date, "
                "coalesce(c.product_code, m.product_code) AS product_code, "
                "coalesce(c.contract_code, m.symbol || '.' || m.exchange) AS contract_code, "
                "coalesce(c.symbol, m.symbol) AS symbol, "
                "coalesce(c.contract_month, try_cast(regexp_extract(m.symbol, '([0-9]{4})$', 1) AS INTEGER)) AS contract_month, "
                "c.total_long, c.total_short, c.net_position, c.seat_count, coalesce(c.batch_id, m.batch_id) AS batch_id, "
                "CASE WHEN isfinite(m.close) AND m.close > 0 THEN m.close END AS close_price, "
                "CASE WHEN isfinite(m.close) AND m.close > 0 THEN m.trade_date END AS price_date "
                "FROM market m FULL OUTER JOIN seats c USING (trade_date, product_code, symbol)) "
                "SELECT *, row_number() OVER (PARTITION BY product_code ORDER BY contract_month NULLS LAST, contract_code) AS contract_rank "
                "FROM contracts ORDER BY product_code, contract_rank" + ("" if normalized_product else " LIMIT ?"),
                [selected_date, *product_params, selected_date, *product_params, *([] if normalized_product else [max(limit, 100)])],
            )
        )
        terms = _as_dicts(
            connection.execute(
                "SELECT * FROM seat_dash_term_signal_daily WHERE trade_date = ? AND min_leg_position >= ?"
                + (" AND product_code = ?" if normalized_product else "")
                + " ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
                "abs(near_net_position) + abs(far_net_position) DESC LIMIT ?",
                [selected_date, min_leg_position, *([normalized_product] if normalized_product else []), min(limit, 10)],
            )
        )
        top_changes = [] if detail == "contracts" else self._seat_positions(
            connection, selected_date, normalized_product, "abs(net_position_change) DESC, seat_name", min(limit, 10))
        static_tags = self._static_tags(
            connection,
            selected_date,
            normalized_product,
            (row.get("seat_id", "") for row in [*terms, *top_changes]),
        )
        self._attach_static_tags(terms, static_tags)
        self._attach_static_tags(top_changes, static_tags)
        return {
            "metrics": metrics,
            "contracts": contracts,
            "signals": self._signals(connection, selected_date, normalized_product, 50),
            "opposite_seats": terms,
            "top_changes": top_changes,
            "min_leg_position": min_leg_position,
        }

    def _validation(
        self,
        connection: duckdb.DuckDBPyConnection,
        selected_date: date,
        product_code: str | None,
        metrics: list[dict[str, Any]],
        limit: int,
    ) -> dict[str, Any]:
        normalized_product = (
            product_code.strip().upper()
            if product_code and product_code.strip().upper() not in {"ALL", "*"}
            else None
        )
        product_sql, product_params = self._product_filter(normalized_product)
        rows = _as_dicts(
            connection.execute(
                "SELECT * FROM seat_dash_rule_result_daily WHERE trade_date = ?" + product_sql +
                " ORDER BY product_code, "
                "CASE rule_code WHEN 'net_cr5_concentration' THEN 1 ELSE 2 END, "
                "CASE horizon_days WHEN 5 THEN 1 ELSE 2 END LIMIT ?",
                [selected_date, *product_params, limit],
            )
        )
        return {
            "metrics": metrics,
            "rules": rows,
            "library_cutoff": max(
                (row["library_cutoff"] for row in rows if row.get("library_cutoff")),
                default=None,
            ),
            "rule_version": rows[0]["rule_version"] if rows else None,
        }

    @staticmethod
    def _insight(metrics: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
        if not metrics:
            return {"code": "NO_DATA", "text": "当前筛选范围没有完整席位数据。", "confidence": None}
        metric = metrics[0]
        direction = "净多" if (metric.get("net_position") or 0) > 0 else "净空"
        f2 = metric.get("f2_value")
        f2_text = f"F2 {f2 * 100:.1f}%" if f2 is not None else "F2 暂不可用"
        text = (
            f"{metric.get('product_code')} 当前席位结构为{direction}，"
            f"净持仓 {metric.get('net_position'):,} 手，{f2_text}。"
        )
        if signals:
            text += f" 检测到 {len(signals)} 条需要复核的席位信号。"
        return {"code": "DETERMINISTIC_SUMMARY", "text": text, "confidence": None}

    def history(
        self,
        entity: str,
        entity_id: str,
        product_code: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = self._safe_limit(limit, 10_000)
        if entity not in {"seat", "product"}:
            raise InvalidDashboardQuery("entity must be seat or product")
        connection, _ = self._connect()
        try:
            clauses = ["1=1"]
            params: list[Any] = []
            if start_date:
                clauses.append("trade_date >= ?")
                params.append(start_date)
            if end_date:
                clauses.append("trade_date <= ?")
                params.append(end_date)
            if entity == "seat":
                clauses.extend(["seat_id = ?"])
                params.append(entity_id)
                if product_code:
                    clauses.append("product_code = ?")
                    params.append(product_code.strip().upper())
                    sql = (
                        "SELECT trade_date, product_code, seat_id, seat_name, long_position, long_change, "
                        "short_position, short_change, net_position, net_change, abs_net_position "
                        "FROM seat_dash_product_seat_daily WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY trade_date DESC LIMIT ?"
                    )
                else:
                    sql = (
                        "SELECT trade_date, 'ALL' AS product_code, seat_id, max(seat_name) AS seat_name, "
                        "sum(long_position) AS long_position, sum(long_change) AS long_change, "
                        "sum(short_position) AS short_position, sum(short_change) AS short_change, "
                        "sum(net_position) AS net_position, sum(net_change) AS net_change, "
                        "abs(sum(net_position)) AS abs_net_position "
                        "FROM seat_dash_product_seat_daily WHERE "
                        + " AND ".join(clauses)
                        + " GROUP BY trade_date, seat_id ORDER BY trade_date DESC LIMIT ?"
                    )
            else:
                clauses.append("product_code = ?")
                params.append(entity_id.strip().upper())
                sql = (
                    "SELECT trade_date, product_code, total_long, total_short, net_position, net_change, "
                    "long_short_ratio, long_cr5, short_cr5, divergence_state, f2_status "
                    "FROM seat_dash_product_daily WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY trade_date DESC LIMIT ?"
                )
            return _as_dicts(connection.execute(sql, [*params, limit]))
        finally:
            connection.close()
