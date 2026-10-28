from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import ClassVar

from seat_dashboard.ports.seat_source import SeatSourcePort

logger = logging.getLogger(__name__)

class ParquetSource(SeatSourcePort):
    """Read-only Parquet source backed by the existing local conversion output."""

    DATASETS: ClassVar[dict[str, str]] = {
        "product_daily": "derive_product_daily",
        "product_seat": "derive_product_seat",
        "main_contract_seat": "derive_main_contract_seat",
        "raw_seat": "raw_seat_position",
    }

    def __init__(self, seat_root: Path, market_1d_root: Path, *, p2_only: bool = False):
        self.p2_only = p2_only
        self.seat_root = Path(seat_root).resolve()
        self.market_1d_root = Path(market_1d_root).resolve()

    def dataset_path(self, dataset: str) -> Path:
        if dataset == "market_1d":
            return self.market_1d_root
        try:
            return self.seat_root / self.DATASETS[dataset]
        except KeyError as exc:
            raise KeyError(f"Unknown Parquet dataset: {dataset}") from exc

    def relation_sql(self, dataset: str) -> str:
        if self.p2_only:
            return "_p2_" + dataset
        return self._parquet_sql(dataset)

    def _parquet_sql(self, dataset: str) -> str:
        path = self.dataset_path(dataset)
        escaped = (path / "**" / "*.parquet").as_posix().replace("'", "''")
        if not path.is_dir():
            raise FileNotFoundError(f"Dataset directory does not exist: {path}")
        return f"read_parquet('{escaped}', hive_partitioning=true)"

    @property
    def datasets(self):
        return ("raw_seat", "market_1d") if self.p2_only else (*self.DATASETS, "market_1d")

    def source_manifest(self) -> dict[str, object]:
        manifest_path = self.seat_root / "conversion_manifest.json"
        if not manifest_path.is_file():
            manifest_path = self.seat_root.parent / "conversion_manifest.json"
        result: dict[str, object] = {"source_backend": "parquet", "input_mode": "p2-only-v1" if self.p2_only else "derived", "datasets": {}}
        if manifest_path.is_file() and not self.p2_only:
            payload = manifest_path.read_bytes()
            result["conversion_manifest_sha256"] = hashlib.sha256(payload).hexdigest()
            result["conversion_manifest"] = json.loads(payload)
        for dataset in self.datasets:
            path = self.dataset_path(dataset)
            file_count = total_bytes = 0
            digest = hashlib.sha256()
            for file in sorted(path.rglob("*.parquet")):
                file_count += 1
                total_bytes += file.stat().st_size
                if self.p2_only:
                    digest.update(file.relative_to(path).as_posix().encode())
                    with file.open("rb") as stream:
                        digest.update(hashlib.file_digest(stream, "sha256").digest())
            result["datasets"][dataset] = {
                "path": str(path),
                "files": file_count,
                "bytes": total_bytes,
                **({"sha256": digest.hexdigest()} if self.p2_only else {}),
            }
        return result

    def validate(self) -> None:
        missing = [str(self.dataset_path(name)) for name in self.datasets if not self.dataset_path(name).is_dir()]
        if missing:
            raise FileNotFoundError("Missing local source datasets: " + "; ".join(missing))
        empty = [str(self.dataset_path(name)) for name in self.datasets if not any(self.dataset_path(name).rglob("*.parquet"))]
        if empty:
            raise FileNotFoundError("Local source dataset contains no Parquet files: " + "; ".join(empty))


    def prepare(self, connection, end=None, months=()) -> None:
        """Derive P3 in the build connection; never create a second source on disk."""
        from seat_dashboard.core.errors import SnapshotValidationError

        def reject(sql, reason):
            rows = connection.execute(sql + " LIMIT 5").fetchall()
            if rows:
                raise SnapshotValidationError(f"P2-only {reason}: {rows}")

        connection.execute("""
            CREATE OR REPLACE TEMP MACRO _p2_ambiguous(s, e, d) AS (
                coalesce(upper(e)='CZCE' AND regexp_full_match(upper(s), '[A-Z]+[0-9]{3}')
                    AND abs(try_cast(substr(right(s,3),1,1) AS INTEGER) - year(d) % 10)=5, FALSE)
            );
            CREATE OR REPLACE TEMP MACRO _p2_symbol(s, e, d) AS (
                CASE WHEN _p2_ambiguous(s, e, d) THEN upper(s)
                WHEN upper(e) = 'CZCE' AND regexp_full_match(upper(s), '[A-Z]+[0-9]{3}')
                THEN regexp_extract(upper(s), '^([A-Z]+)', 1) ||
                     lpad(CAST((year(d) + ((CAST(substr(regexp_extract(s, '([0-9]{3})$', 1), 1, 1) AS INTEGER)
                         - year(d) % 10 + 15) % 10) - 5) % 100 AS VARCHAR), 2, '0') || right(s, 2)
                ELSE upper(s) END
            )
        """)
        cutoff = f"AND CAST(trade_date AS DATE) <= DATE '{end.isoformat()}'" if end else ""
        month_sql = ",".join(f"'{month}'" for month in months)
        changed = f"strftime(CAST(trade_date AS DATE), '%Y-%m') IN ({month_sql})" if months else "TRUE"
        if months:
            cutoff += f" AND year_month IN ({month_sql}) AND {changed}"
            logger.info("P2 rebuilding months: %s", ",".join(months))
        for dataset in self.datasets:
            # Preserve unresolved zero compatibility rows as supplied; never guess a decade.
            scope = "AND is_aggregated=0 AND (long_position=0 AND long_change=0 AND short_position=0 AND short_change=0) IS NOT TRUE" if dataset == "raw_seat" else ""
            reject(f"""SELECT trade_date, symbol FROM {self._parquet_sql(dataset)}
                WHERE _p2_ambiguous(symbol, exchange, CAST(trade_date AS DATE)) {scope} {cutoff}""",
                "ambiguous CZCE decade")
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE _p2_raw_seat AS
            WITH source AS (
                SELECT *, _p2_symbol(symbol, exchange, CAST(trade_date AS DATE)) AS full_symbol
                FROM {self._parquet_sql('raw_seat')}
                WHERE is_aggregated = 0 {cutoff}
            )
            SELECT DISTINCT CAST(trade_date AS DATE) AS trade_date, upper(exchange) AS exchange,
                upper(root) AS root, upper(root) || '.' || upper(exchange) AS product_code,
                full_symbol || '.' || upper(exchange) AS contract_code, full_symbol AS symbol,
                trim(seat_name) AS seat_name, 0 AS is_aggregated,
                long_position, long_change, short_position, short_change,
                coalesce(long_position, 0) - coalesce(short_position, 0) AS net_position
            FROM source WHERE seat_name IS NOT NULL AND trim(seat_name) <> ''
        """)
        reject("""SELECT trade_date, product_code, symbol FROM _p2_raw_seat
            WHERE (NOT regexp_full_match(symbol, root || '[0-9]{4}')
                   AND NOT (_p2_ambiguous(symbol, exchange, trade_date)
                            AND regexp_full_match(symbol, root || '[0-9]{3}')))
               OR try_cast(right(symbol, 2) AS INTEGER) NOT BETWEEN 1 AND 12
               OR symbol IS NULL
               OR product_code IS NULL""", "invalid or ambiguous contract code")
        reject("""SELECT trade_date, contract_code, seat_name FROM _p2_raw_seat
            GROUP BY ALL HAVING count(*) > 1""", "conflicting raw rows; export one authoritative revision")
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE _p2_market_1d AS
            SELECT DISTINCT * REPLACE (CAST(trade_date AS DATE) AS trade_date,
                upper(exchange) AS exchange, _p2_symbol(symbol, exchange, CAST(trade_date AS DATE)) AS symbol)
            FROM {self._parquet_sql('market_1d')} WHERE TRUE {cutoff}
            QUALIFY dense_rank() OVER (PARTITION BY CAST(trade_date AS DATE), upper(exchange),
                _p2_symbol(symbol, exchange, CAST(trade_date AS DATE)) ORDER BY sequence DESC NULLS LAST) = 1
        """)
        reject("""SELECT trade_date, exchange, symbol FROM _p2_market_1d
            GROUP BY ALL HAVING count(*) > 1""", "conflicting latest market rows")
        if months:
            connection.execute(f"""INSERT INTO _p2_market_1d BY NAME
                SELECT trade_date, exchange, symbol, close, open_interest AS position, volume, sequence
                FROM seat_dash_market_daily WHERE NOT ({changed})""")
        product_dates = "SELECT trade_date, product_code FROM _p2_product_daily"
        old_seats = ""
        if months:
            product_dates += f" UNION ALL SELECT trade_date, product_code FROM seat_dash_product_daily WHERE NOT ({changed})"
            old_seats = f"""OR EXISTS (SELECT 1 FROM seat_dash_main_contract_coverage c
                WHERE c.trade_date=d.trade_date AND c.product_code=d.product_code AND c.symbol=m.symbol
                    AND c.seat_status='ready' AND NOT ({changed.replace('trade_date', 'c.trade_date')}))"""
        for statement in f"""
            CREATE OR REPLACE TEMP TABLE _p2_product_seat AS
            -- Raw contract keys are already unique and non-null, so no second DISTINCT hash is needed.
            SELECT trade_date, exchange, root, product_code, seat_name,
                sum(coalesce(long_position, 0)) AS long_position, sum(coalesce(long_change, 0)) AS long_change,
                sum(coalesce(short_position, 0)) AS short_position, sum(coalesce(short_change, 0)) AS short_change,
                sum(net_position) AS net_position, count(*) AS contract_count
            FROM _p2_raw_seat GROUP BY ALL;
            CREATE OR REPLACE TEMP TABLE _p2_product_daily AS
            SELECT trade_date, exchange, root, product_code,
                sum(long_position) AS total_long, sum(short_position) AS total_short,
                sum(long_change) AS long_change, sum(short_change) AS short_change,
                sum(net_position) AS net_position, count(*) AS seat_count
            FROM _p2_product_seat GROUP BY ALL;
            CREATE OR REPLACE TEMP TABLE _p2_market_contracts AS
            WITH products AS (
                SELECT product_code, min(trade_date) AS first_day, max(trade_date) AS last_day
                FROM ({product_dates}) GROUP BY product_code
            ), contracts AS (
                SELECT *, regexp_extract(symbol, '^([A-Z]+)', 1) || '.' || exchange AS product_code
                FROM _p2_market_1d
                WHERE regexp_full_match(symbol, '[A-Z]+[0-9]{{4}}')
                  AND try_cast(right(symbol,2) AS INTEGER) BETWEEN 1 AND 12
            )
            SELECT m.* FROM contracts m JOIN products d USING (product_code)
            WHERE m.trade_date BETWEEN first_day AND last_day;
            CREATE OR REPLACE TEMP TABLE _p2_main AS
            WITH ranked AS (
                SELECT *, max(position) OVER w AS max_oi,
                    count(*) FILTER (WHERE position IS NULL OR position < 0) OVER w AS unknown_oi
                FROM _p2_market_contracts
                WINDOW w AS (PARTITION BY trade_date, product_code)
            )
            SELECT * EXCLUDE(max_oi,unknown_oi) FROM ranked
            WHERE position = max_oi AND position > 0 AND unknown_oi=0
            QUALIFY count(*) OVER (PARTITION BY trade_date, product_code)=1;
            CREATE OR REPLACE TEMP TABLE _p2_main_price AS
            WITH calendar AS (
                {product_dates}
                UNION SELECT trade_date, product_code FROM _p2_market_contracts
            )
            SELECT d.trade_date, d.product_code, m.symbol, m.position AS open_interest,
                CASE WHEN m.close > 0 THEN m.close END AS close,
                CASE WHEN m.symbol IS NULL THEN 'main_unresolved'
                     WHEN m.close IS NULL OR m.close <= 0 THEN 'missing_close' ELSE 'ready' END AS price_status,
                CASE WHEN m.symbol IS NULL THEN 'main_unresolved'
                     WHEN EXISTS (SELECT 1 FROM (SELECT DISTINCT trade_date, product_code, symbol FROM _p2_raw_seat) r WHERE r.trade_date=d.trade_date
                         AND r.product_code=d.product_code AND r.symbol=m.symbol) {old_seats}
                     THEN 'ready' ELSE 'missing_seat' END AS seat_status
            FROM calendar d LEFT JOIN _p2_main m USING (trade_date,product_code);
            CREATE OR REPLACE TEMP TABLE _p2_main_contract_seat AS
            SELECT r.*, m.close, m.position AS open_interest
            FROM _p2_raw_seat r JOIN _p2_main m USING (trade_date, product_code, symbol)
        """.split(";"):
            stage = statement.split(" AS", 1)[0].split()[-1]
            started = perf_counter()
            logger.info("P2 stage %s started", stage)
            connection.execute(statement)
            logger.info("P2 stage %s completed in %.3fs", stage, perf_counter() - started)
        connection.execute("DROP TABLE _p2_market_contracts; DROP TABLE _p2_main")
