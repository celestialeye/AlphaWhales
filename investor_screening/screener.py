from __future__ import annotations

import os
import hashlib
import json
import statistics
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from config import FUND_MANAGERS
from .database import DEFAULT_DATABASE_PATH

DEFAULT_SNAPSHOT_POINTER = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "investor_screening"
    / "screening_snapshot.json"
)
DEFAULT_SNAPSHOT_PATH = DEFAULT_SNAPSHOT_POINTER
DEFAULT_PERFORMANCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "investor_screening"
    / "performance.duckdb"
)

BEST_BET_CUBE_WEIGHT_FLOOR_PCT = 1.0
MAX_BEST_BET_SNAPSHOTS = 20
REQUIRED_MANAGER_SNAPSHOTS = 12
BEST_BET_DURATION_SNAPSHOTS = {
    6: 3,
    12: 5,
    18: 7,
    24: 9,
}

DEFAULT_FILTERS = {
    "minimum_size_billions": 10.0,
    "minimum_stock_count": 1,
    "minimum_direct_stock_pct": 80.0,
    "minimum_top10_pct": 40.0,
    "minimum_concentration_quarters": 6,
    "minimum_best_bet_weight_pct": 3.0,
    "best_bet_duration_months": 12,
    "minimum_best_bet_count": 1,
    "performance_window": "3Y",
    "benchmark_hurdle": "none",
    "minimum_excess_cagr_pct": 0.0,
    "minimum_beat_consistency_pct": None,
    "maximum_drawdown_pct": None,
    "require_performance": False,
}

FUND_LIKE_PATTERN = (
    r"ETF|EXCHANGE[- ]TRADED|ISHARES|VANGUARD|SPDR|DIMENSIONAL ETF|"
    r"SCHWAB STRATEGIC|PROSHARES|WISDOMTREE|VANECK|GLOBAL X|ARK ETF|"
    r"INVESCO QQQ|INVESCO EXCH|FIRST TR EXCHANGE|FIRST TRUST EXCHANGE|"
    r"J P MORGAN EXCHANGE|JPMORGAN ETF|AMERICAN CENTY ETF|"
    r"FIDELITY COVINGTON|BLACKROCK ETF|ETF SER|PGIM ETF|"
    r"HARTFORD.*EXCHANGE|CAPITAL GRP.*ETF|GOLDMAN SACHS ETF"
)


SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_metadata (
    report_period DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    methodology_version VARCHAR NOT NULL,
    turnover_method VARCHAR NOT NULL,
    fund_classification_method VARCHAR NOT NULL,
    source_fingerprint VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_metrics (
    cik VARCHAR PRIMARY KEY,
    manager_name VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    filing_quarters INTEGER NOT NULL,
    median_reported_value_4q DOUBLE NOT NULL,
    latest_nonoption_value DOUBLE NOT NULL,
    latest_direct_stock_value DOUBLE NOT NULL,
    latest_stock_count INTEGER NOT NULL,
    direct_stock_pct DOUBLE NOT NULL,
    top10_pct DOUBLE NOT NULL,
    maximum_position_pct DOUBLE NOT NULL,
    concentration_pass_quarters INTEGER NOT NULL,
    annualized_turnover_pct DOUBLE NOT NULL,
    durable_position_count INTEGER NOT NULL,
    roster_name VARCHAR,
    is_current_roster BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS durable_positions (
    cik VARCHAR NOT NULL,
    security_key VARCHAR NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    issuer VARCHAR,
    title_of_class VARCHAR,
    latest_weight_pct DOUBLE NOT NULL,
    latest_rank INTEGER NOT NULL,
    observed_quarters INTEGER NOT NULL,
    conviction_quarters INTEGER NOT NULL,
    PRIMARY KEY (cik, security_key)
);

CREATE TABLE IF NOT EXISTS manager_position_quarters (
    cik VARCHAR NOT NULL,
    security_key VARCHAR NOT NULL,
    quarter_index INTEGER NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR,
    ticker VARCHAR,
    issuer VARCHAR,
    title_of_class VARCHAR,
    reported_value DOUBLE NOT NULL,
    weight_nonoption_pct DOUBLE NOT NULL,
    direct_sleeve_weight_pct DOUBLE NOT NULL,
    position_rank INTEGER NOT NULL,
    PRIMARY KEY (cik, security_key, quarter_index)
);

CREATE INDEX IF NOT EXISTS idx_manager_position_quarters_streak
    ON manager_position_quarters (
        cik,
        quarter_index,
        weight_nonoption_pct,
        security_key
    );

CREATE TABLE IF NOT EXISTS manager_quarter_concentration (
    cik VARCHAR NOT NULL,
    quarter_index INTEGER NOT NULL,
    top10_pct DOUBLE NOT NULL,
    PRIMARY KEY (cik, quarter_index)
);

CREATE TABLE IF NOT EXISTS performance_run_metadata (
    run_id VARCHAR PRIMARY KEY,
    methodology_version VARCHAR NOT NULL,
    label VARCHAR NOT NULL,
    disclaimer VARCHAR NOT NULL,
    screening_source_fingerprint VARCHAR NOT NULL,
    requested_as_of DATE NOT NULL,
    latest_end_date DATE,
    window_years INTEGER NOT NULL,
    cost_bps DOUBLE NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS manager_performance (
    cik VARCHAR NOT NULL,
    "window" VARCHAR NOT NULL,
    cost_bps DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    start_date DATE,
    end_date DATE,
    years DOUBLE,
    estimated_cagr DOUBLE,
    spy_cagr DOUBLE,
    qqq_cagr DOUBLE,
    spy_excess_cagr DOUBLE,
    qqq_excess_cagr DOUBLE,
    max_drawdown DOUBLE,
    monthly_sharpe_rf0 DOUBLE,
    spy_information_ratio DOUBLE,
    qqq_information_ratio DOUBLE,
    spy_quarterly_beat_rate DOUBLE,
    qqq_quarterly_beat_rate DOUBLE,
    mapping_coverage DOUBLE,
    priced_coverage DOUBLE,
    interval_count INTEGER NOT NULL,
    unavailable_reason VARCHAR,
    label VARCHAR NOT NULL,
    disclaimer VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (cik, "window", cost_bps)
);

CREATE TABLE IF NOT EXISTS manager_performance_monthly (
    cik VARCHAR NOT NULL,
    month_end DATE NOT NULL,
    cost_bps DOUBLE NOT NULL,
    estimated_return DOUBLE NOT NULL,
    spy_return DOUBLE NOT NULL,
    qqq_return DOUBLE NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (cik, month_end, cost_bps)
);
"""


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _alias_values() -> str:
    rows = []
    for fund in FUND_MANAGERS:
        roster_name = fund["manager"]
        rows.append((fund["cik"], fund["cik"], roster_name))
        rows.extend(
            (historical_cik, fund["cik"], roster_name)
            for historical_cik in fund.get("historical_ciks", [])
        )
    return ", ".join(
        f"('{_sql_string(source)}','{_sql_string(canonical)}',"
        f"'{_sql_string(name)}')"
        for source, canonical, name in rows
    )


def compute_source_fingerprint(
    connection: duckdb.DuckDBPyConnection,
) -> str:
    datasets = connection.execute(
        """
        SELECT dataset_id, source_sha256, submission_count, holdings_count
        FROM datasets
        WHERE status = 'IMPORTED'
        ORDER BY dataset_id
        """
    ).fetchall()
    payload = {
        "datasets": datasets,
        "aliases": _alias_values(),
        "methodology": "screening-v1",
        "fund_pattern": FUND_LIKE_PATTERN,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def resolve_snapshot_path(
        snapshot_path: str | Path = DEFAULT_SNAPSHOT_POINTER,
) -> Path:
    path = Path(snapshot_path).resolve()
    if path.suffix.lower() != ".json":
        return path
    payload = json.loads(path.read_text(encoding="utf-8"))
    generation = Path(payload["generation"])
    if not generation.is_absolute():
        generation = path.parent / generation
    return generation.resolve()


def _compatible_performance_rows(
    performance_path: str | Path,
    source_fingerprint: str,
    run_id: str | None = None,
) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Read one completed, source-compatible result without invoking a provider."""
    path = Path(performance_path).resolve()
    if not path.is_file():
        return [], [], []
    performance = duckdb.connect(str(path), read_only=True)
    try:
        tables = {
            row[0]
            for row in performance.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }
        required = {
            "performance_runs",
            "manager_performance",
            "monthly_returns",
        }
        if not required.issubset(tables):
            return [], [], []
        columns_by_table: dict[str, set[str]] = {}
        for table_name in required:
            columns_by_table[table_name] = {
                row[0]
                for row in performance.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'main' AND table_name = ?
                    """,
                    [table_name],
                ).fetchall()
            }
        if not {
            "run_id",
            "status",
            "methodology_version",
            "label",
            "disclaimer",
            "screening_source_fingerprint",
            "requested_as_of",
            "latest_end_date",
            "window_years",
            "minimum_size_billions",
            "cost_bps",
            "completed_at",
        }.issubset(columns_by_table["performance_runs"]):
            return [], [], []
        if not {
            "run_id",
            "cik",
            "window",
            "cost_bps",
            "status",
            "start_date",
            "end_date",
            "years",
            "estimated_cagr",
            "spy_cagr",
            "qqq_cagr",
            "spy_excess_cagr",
            "qqq_excess_cagr",
            "max_drawdown",
            "monthly_sharpe_rf0",
            "spy_information_ratio",
            "qqq_information_ratio",
            "spy_quarterly_beat_rate",
            "qqq_quarterly_beat_rate",
            "mapping_coverage",
            "priced_coverage",
            "interval_count",
            "unavailable_reason",
            "label",
            "disclaimer",
        }.issubset(columns_by_table["manager_performance"]):
            return [], [], []
        if not {
            "run_id",
            "cik",
            "month_end",
            "cost_bps",
            "estimated_return",
            "spy_return",
            "qqq_return",
        }.issubset(columns_by_table["monthly_returns"]):
            return [], [], []
        run_filter = ""
        params: list[object] = [source_fingerprint]
        if run_id:
            run_filter = "AND run_id = ?"
            params.append(run_id)
        else:
            run_filter = """
              AND methodology_version = '13f-disclosure-lag-v1'
              AND window_years >= 5
              AND coalesce(minimum_size_billions, 10) <= 10
            """
        eligible_runs_sql = f"""
            SELECT *
            FROM performance_runs
            WHERE status = 'COMPLETE'
              AND screening_source_fingerprint = ?
              AND cost_bps = 0
              {run_filter}
        """
        selected_runs_sql = f"""
            WITH eligible_runs AS (
                {eligible_runs_sql}
            ),
            candidate_manager_runs AS (
                SELECT DISTINCT
                    mp.cik,
                    r.run_id,
                    r.latest_end_date,
                    r.requested_as_of,
                    r.completed_at
                FROM manager_performance mp
                JOIN eligible_runs r USING (run_id)
            )
            SELECT cik, run_id
            FROM candidate_manager_runs
            QUALIFY row_number() OVER (
                PARTITION BY cik
                ORDER BY
                    latest_end_date DESC,
                    requested_as_of DESC,
                    completed_at DESC
            ) = 1
        """
        selected_count = performance.execute(
            f"SELECT count(*) FROM ({selected_runs_sql})",
            params,
        ).fetchone()[0]
        if not selected_count:
            return [], [], []
        runs = performance.execute(
            f"""
            WITH selected_manager_runs AS (
                {selected_runs_sql}
            )
            SELECT DISTINCT
                r.run_id, r.methodology_version, r.label, r.disclaimer,
                r.screening_source_fingerprint, r.requested_as_of,
                r.latest_end_date, r.window_years, r.cost_bps, r.completed_at
            FROM performance_runs r
            JOIN (
                SELECT DISTINCT run_id
                FROM selected_manager_runs
            ) selected USING (run_id)
            ORDER BY r.latest_end_date DESC, r.completed_at DESC
            """,
            params,
        ).fetchall()
        summaries = performance.execute(
            f"""
            WITH selected_manager_runs AS (
                {selected_runs_sql}
            )
            SELECT
                mp.cik, mp."window", mp.cost_bps, mp.status, mp.start_date,
                mp.end_date, mp.years, mp.estimated_cagr, mp.spy_cagr,
                mp.qqq_cagr, mp.spy_excess_cagr, mp.qqq_excess_cagr,
                mp.max_drawdown, mp.monthly_sharpe_rf0,
                mp.spy_information_ratio, mp.qqq_information_ratio,
                mp.spy_quarterly_beat_rate, mp.qqq_quarterly_beat_rate,
                mp.mapping_coverage, mp.priced_coverage, mp.interval_count,
                mp.unavailable_reason, mp.label, mp.disclaimer, mp.run_id
            FROM manager_performance mp
            JOIN selected_manager_runs selected USING (cik, run_id)
            ORDER BY mp.cik, mp."window"
            """,
            params,
        ).fetchall()
        monthly = performance.execute(
            f"""
            WITH selected_manager_runs AS (
                {selected_runs_sql}
            )
            SELECT
                mr.cik, mr.month_end, mr.cost_bps, mr.estimated_return,
                mr.spy_return, mr.qqq_return, mr.run_id
            FROM monthly_returns mr
            JOIN selected_manager_runs selected USING (cik, run_id)
            ORDER BY mr.cik, mr.month_end
            """,
            params,
        ).fetchall()
        return runs, summaries, monthly
    finally:
        performance.close()


def build_screening_snapshot(
    source_path: str | Path = DEFAULT_DATABASE_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_POINTER,
    performance_path: str | Path = DEFAULT_PERFORMANCE_PATH,
    performance_run_id: str | None = None,
) -> dict:
    source = duckdb.connect(str(Path(source_path).resolve()), read_only=True)
    pointer_file = Path(snapshot_path).resolve()
    pointer_file.parent.mkdir(parents=True, exist_ok=True)
    generation_name = f"screening_snapshot.{uuid.uuid4().hex}.duckdb"
    snapshot_file = pointer_file.parent / generation_name
    staging_file = snapshot_file.with_suffix(".building.duckdb")
    snapshot = duckdb.connect(str(staging_file))
    try:
        source.execute("PRAGMA disable_progress_bar")
        snapshot.execute("PRAGMA disable_progress_bar")
        source.execute("SET preserve_insertion_order=false")
        source.execute("SET memory_limit='6GB'")
        source.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE manager_aliases(
                source_cik, canonical_cik, roster_name
            ) AS
            SELECT * FROM (
                VALUES {_alias_values()}
            )
            """
        )
        report_period = source.execute(
            """
            WITH period_counts AS (
                SELECT
                    period_of_report,
                    count(DISTINCT cik) AS manager_count
                FROM submissions
                WHERE submission_type IN ('13F-HR', '13F-HR/A')
                GROUP BY period_of_report
            )
            SELECT period_of_report
            FROM period_counts
            WHERE manager_count >= 0.8 * (
                SELECT max(manager_count) FROM period_counts
            )
            ORDER BY period_of_report DESC
            LIMIT 1
            """
        ).fetchone()[0]
        source_fingerprint = compute_source_fingerprint(source)
        quarter_values = []
        current_period = report_period
        for _ in range(MAX_BEST_BET_SNAPSHOTS):
            quarter_values.append(current_period)
            month = current_period.month - 3
            year = current_period.year
            if month <= 0:
                month += 12
                year -= 1
            current_period = date(
                year,
                month,
                31 if month in (3, 12) else 30,
            )
        quarter_sql = ", ".join(
            f"(DATE '{period.isoformat()}', {index})"
            for index, period in enumerate(quarter_values, start=1)
        )
        source.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE screening_quarters AS
            SELECT * FROM (
                VALUES {quarter_sql}
            ) AS q(period_of_report, quarter_index)
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_accessions AS
            SELECT
                coalesce(a.canonical_cik, ea.cik) AS cik,
                coalesce(a.roster_name, ea.filing_manager_name) AS manager_name,
                ea.accession_number,
                ea.period_of_report,
                q.quarter_index
            FROM v_effective_accessions ea
            JOIN screening_quarters q USING (period_of_report)
            LEFT JOIN manager_aliases a ON a.source_cik = ea.cik
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_manager_quarters AS
            SELECT
                sa.cik,
                arg_max(sa.manager_name, sa.accession_number) AS manager_name,
                sa.quarter_index,
                sum(h.value_usd)::DOUBLE AS total_reported_value
            FROM screening_accessions sa
            JOIN holdings h USING (accession_number)
            GROUP BY sa.cik, sa.quarter_index
            """
        )
        source.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE screening_history AS
            SELECT
                cik,
                max(manager_name) FILTER (WHERE quarter_index = 1) AS manager_name,
                count(DISTINCT quarter_index)
                    FILTER (
                        WHERE quarter_index <= {REQUIRED_MANAGER_SNAPSHOTS}
                    )::INTEGER AS filing_quarters,
                median(total_reported_value)
                    FILTER (WHERE quarter_index <= 4) AS median_reported_value_4q
            FROM screening_manager_quarters
            GROUP BY cik
            HAVING filing_quarters = {REQUIRED_MANAGER_SNAPSHOTS}
            """
        )
        source.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE screening_raw_positions AS
            SELECT
                sa.cik,
                sa.quarter_index,
                coalesce(
                    nullif(trim(h.cusip), ''),
                    concat(
                        'NO-CUSIP|',
                        coalesce(h.name_of_issuer, ''),
                        '|',
                        coalesce(h.title_of_class, '')
                    )
                ) AS security_key,
                max(h.cusip) AS cusip,
                max(h.ticker) AS ticker,
                max(h.name_of_issuer) AS issuer,
                max(h.title_of_class) AS title_of_class,
                sum(h.value_usd)::DOUBLE AS value_usd,
                sum(h.shares_or_principal)::DOUBLE AS shares,
                bool_or(
                    regexp_matches(
                        upper(
                            coalesce(h.name_of_issuer, '')
                            || ' '
                            || coalesce(h.title_of_class, '')
                        ),
                        '{FUND_LIKE_PATTERN}'
                    )
                ) AS is_fund_like
            FROM screening_accessions sa
            JOIN screening_history sh USING (cik)
            JOIN holdings h USING (accession_number)
            WHERE nullif(trim(h.put_call), '') IS NULL
            GROUP BY sa.cik, sa.quarter_index, security_key
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_sleeves AS
            SELECT
                cik,
                quarter_index,
                sum(value_usd) AS nonoption_value,
                sum(value_usd) FILTER (WHERE NOT is_fund_like) AS direct_stock_value,
                count(DISTINCT security_key)
                    FILTER (WHERE NOT is_fund_like)::INTEGER AS direct_stock_count
            FROM screening_raw_positions
            GROUP BY cik, quarter_index
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_positions AS
            SELECT
                p.*,
                p.value_usd / nullif(s.direct_stock_value, 0) AS weight,
                row_number() OVER (
                    PARTITION BY p.cik, p.quarter_index
                    ORDER BY p.value_usd DESC, p.security_key
                ) AS position_rank
            FROM screening_raw_positions p
            JOIN screening_sleeves s USING (cik, quarter_index)
            WHERE NOT p.is_fund_like
            """
        )
        source.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE screening_position_quarters AS
            SELECT
                p.cik,
                p.security_key,
                p.quarter_index,
                q.period_of_report AS report_period,
                p.cusip,
                p.ticker,
                p.issuer,
                p.title_of_class,
                p.value_usd AS reported_value,
                p.value_usd / nullif(s.nonoption_value, 0) * 100
                    AS weight_nonoption_pct,
                p.weight * 100 AS direct_sleeve_weight_pct,
                p.position_rank::INTEGER AS position_rank
            FROM screening_positions p
            JOIN screening_sleeves s USING (cik, quarter_index)
            JOIN screening_quarters q USING (quarter_index)
            WHERE p.quarter_index <= {MAX_BEST_BET_SNAPSHOTS}
              AND p.value_usd > 0
              AND s.nonoption_value > 0
              AND s.direct_stock_value > 0
              AND (
                  p.value_usd / nullif(s.nonoption_value, 0) * 100
                      >= {BEST_BET_CUBE_WEIGHT_FLOOR_PCT}
                  OR p.position_rank <= 10
              )
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_concentration AS
            SELECT
                cik,
                quarter_index,
                sum(weight) FILTER (WHERE position_rank <= 10) AS top10_weight,
                max(weight) AS maximum_weight
            FROM screening_positions
            GROUP BY cik, quarter_index
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_turnover AS
            WITH paired AS (
                SELECT
                    coalesce(curr.cik, prev.cik) AS cik,
                    coalesce(
                        curr.quarter_index,
                        prev.quarter_index - 1
                    ) AS quarter_index,
                    coalesce(curr.security_key, prev.security_key) AS security_key,
                    coalesce(curr.weight, 0) AS current_weight,
                    coalesce(prev.weight, 0) AS previous_weight,
                    curr.shares AS current_shares,
                    prev.shares AS previous_shares
                FROM screening_positions curr
                FULL OUTER JOIN screening_positions prev
                  ON curr.cik = prev.cik
                 AND curr.security_key = prev.security_key
                 AND prev.quarter_index = curr.quarter_index + 1
                WHERE coalesce(
                    curr.quarter_index,
                    prev.quarter_index - 1
                ) BETWEEN 1 AND 8
            ),
            quarterly AS (
                SELECT
                    cik,
                    quarter_index,
                    0.5 * sum(
                        CASE
                            WHEN current_shares IS NULL OR previous_shares IS NULL
                            THEN CASE
                                WHEN greatest(current_weight, previous_weight) >= 0.005
                                THEN abs(current_weight - previous_weight)
                                ELSE 0
                            END
                            WHEN abs(current_weight - previous_weight) >= 0.0025
                             AND abs(current_shares - previous_shares)
                                 / nullif(abs(previous_shares), 0) >= 0.10
                            THEN abs(current_weight - previous_weight)
                            ELSE 0
                        END
                    )
                        AS quarterly_turnover
                FROM paired
                GROUP BY cik, quarter_index
            )
            SELECT
                cik,
                avg(quarterly_turnover) * 400 AS annualized_turnover_pct
            FROM quarterly
            GROUP BY cik
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_durable_positions AS
            SELECT
                p.cik,
                p.security_key,
                max(p.cusip) AS cusip,
                max(p.ticker) AS ticker,
                max(p.issuer) AS issuer,
                max(p.title_of_class) AS title_of_class,
                max(p.weight) FILTER (WHERE p.quarter_index = 1) * 100
                    AS latest_weight_pct,
                max(p.position_rank) FILTER (WHERE p.quarter_index = 1)::INTEGER
                    AS latest_rank,
                count(DISTINCT p.quarter_index)
                    FILTER (WHERE p.quarter_index <= 5)::INTEGER
                    AS observed_quarters,
                count(DISTINCT p.quarter_index)
                    FILTER (
                        WHERE p.quarter_index <= 4
                          AND p.weight >= 0.03
                          AND p.position_rank <= 10
                    )::INTEGER AS conviction_quarters
            FROM screening_positions p
            GROUP BY p.cik, p.security_key
            HAVING max(p.weight) FILTER (WHERE p.quarter_index = 1) >= 0.03
               AND max(p.position_rank) FILTER (WHERE p.quarter_index = 1) <= 10
               AND observed_quarters = 5
               AND conviction_quarters >= 3
            """
        )
        source.execute(
            """
            CREATE OR REPLACE TEMP TABLE screening_metrics AS
            SELECT
                sh.cik,
                sh.manager_name,
                sh.filing_quarters,
                sh.median_reported_value_4q,
                coalesce(
                    max(s.nonoption_value)
                        FILTER (WHERE s.quarter_index = 1),
                    0
                ) AS latest_nonoption_value,
                coalesce(
                    max(s.direct_stock_value)
                        FILTER (WHERE s.quarter_index = 1),
                    0
                ) AS latest_direct_stock_value,
                coalesce(
                    max(s.direct_stock_count)
                        FILTER (WHERE s.quarter_index = 1),
                    0
                )::INTEGER AS latest_stock_count,
                coalesce(
                    max(
                        s.direct_stock_value / nullif(s.nonoption_value, 0)
                    ) FILTER (WHERE s.quarter_index = 1) * 100,
                    0
                ) AS direct_stock_pct,
                coalesce(
                    max(c.top10_weight)
                        FILTER (WHERE c.quarter_index = 1) * 100,
                    0
                ) AS top10_pct,
                coalesce(
                    max(c.maximum_weight)
                        FILTER (WHERE c.quarter_index = 1) * 100,
                    0
                ) AS maximum_position_pct,
                count(DISTINCT c.quarter_index)
                    FILTER (
                        WHERE c.quarter_index <= 8
                          AND c.top10_weight >= 0.40
                    )::INTEGER AS concentration_pass_quarters,
                coalesce(t.annualized_turnover_pct, 0) AS annualized_turnover_pct,
                count(DISTINCT dp.security_key)::INTEGER AS durable_position_count
            FROM screening_history sh
            JOIN screening_sleeves s USING (cik)
            JOIN screening_concentration c USING (cik, quarter_index)
            JOIN screening_turnover t USING (cik)
            LEFT JOIN screening_durable_positions dp USING (cik)
            GROUP BY
                sh.cik,
                sh.manager_name,
                sh.filing_quarters,
                sh.median_reported_value_4q,
                t.annualized_turnover_pct
            """
        )

        manager_rows_raw = source.execute(
            """
            SELECT
                m.*,
                a.roster_name,
                a.roster_name IS NOT NULL AS is_current_roster
            FROM screening_metrics m
            LEFT JOIN (
                SELECT DISTINCT canonical_cik, roster_name
                FROM manager_aliases
            ) a ON a.canonical_cik = m.cik
            """
        ).fetchall()
        manager_rows = [
            (row[0], row[1], report_period, *row[2:])
            for row in manager_rows_raw
        ]
        position_rows = source.execute(
            "SELECT * FROM screening_durable_positions"
        ).fetchall()
        position_quarter_rows = source.execute(
            "SELECT * FROM screening_position_quarters"
        ).fetchall()
        concentration_rows = source.execute(
            """
            SELECT cik, quarter_index, coalesce(top10_weight * 100, 0)
            FROM screening_concentration
            WHERE quarter_index <= 8
            """
        ).fetchall()
        (
            performance_metadata_rows,
            performance_summary_rows,
            performance_monthly_rows,
        ) = _compatible_performance_rows(
            performance_path,
            source_fingerprint,
            performance_run_id,
        )

        snapshot.execute(SNAPSHOT_SCHEMA)
        snapshot.execute("BEGIN TRANSACTION")
        snapshot.execute("DELETE FROM snapshot_metadata")
        snapshot.execute("DELETE FROM manager_metrics")
        snapshot.execute("DELETE FROM durable_positions")
        snapshot.execute("DELETE FROM manager_position_quarters")
        snapshot.execute("DELETE FROM manager_quarter_concentration")
        snapshot.execute("DELETE FROM performance_run_metadata")
        snapshot.execute("DELETE FROM manager_performance")
        snapshot.execute("DELETE FROM manager_performance_monthly")
        snapshot.execute(
            """
            INSERT INTO snapshot_metadata
            VALUES (?, ?, 'screening-v1',
                    'share-confirmed-material-weight-proxy',
                    'issuer-name-and-title-heuristic', ?)
            """,
            [report_period, datetime.now(timezone.utc), source_fingerprint],
        )
        manager_insert = (
            "INSERT INTO manager_metrics VALUES ("
            + ",".join("?" for _ in range(16))
            + ")"
        )
        for start in range(0, len(manager_rows), 500):
            snapshot.executemany(
                manager_insert,
                manager_rows[start:start + 500],
            )
        for start in range(0, len(position_rows), 500):
            snapshot.executemany(
                "INSERT INTO durable_positions VALUES (?,?,?,?,?,?,?,?,?,?)",
                position_rows[start:start + 500],
            )
        for start in range(0, len(position_quarter_rows), 1000):
            snapshot.executemany(
                "INSERT INTO manager_position_quarters VALUES ("
                + ",".join("?" for _ in range(12))
                + ")",
                position_quarter_rows[start:start + 1000],
            )
        for start in range(0, len(concentration_rows), 500):
            snapshot.executemany(
                "INSERT INTO manager_quarter_concentration VALUES (?,?,?)",
                concentration_rows[start:start + 500],
            )
        if performance_metadata_rows:
            snapshot.executemany(
                "INSERT INTO performance_run_metadata VALUES (?,?,?,?,?,?,?,?,?,?)",
                performance_metadata_rows,
            )
        for start in range(0, len(performance_summary_rows), 500):
            snapshot.executemany(
                "INSERT INTO manager_performance VALUES ("
                + ",".join("?" for _ in range(25))
                + ")",
                performance_summary_rows[start:start + 500],
            )
        for start in range(0, len(performance_monthly_rows), 1000):
            snapshot.executemany(
                "INSERT INTO manager_performance_monthly VALUES (?,?,?,?,?,?,?)",
                performance_monthly_rows[start:start + 1000],
            )
        snapshot.execute("COMMIT")

        default_count = snapshot.execute(
            """
            WITH persistent_best_bets AS (
                SELECT cik, count(*) AS best_bet_count
                FROM (
                    SELECT cik, security_key
                    FROM manager_position_quarters
                    WHERE quarter_index <= 5
                      AND weight_nonoption_pct >= 3
                    GROUP BY cik, security_key
                    HAVING count(*) = 5
                ) positions
                GROUP BY cik
            )
            SELECT count(*)
            FROM manager_metrics m
            JOIN persistent_best_bets b USING (cik)
            WHERE m.median_reported_value_4q >= 10000000000
              AND m.latest_stock_count >= 1
              AND m.direct_stock_pct >= 80
              AND m.top10_pct >= 40
              AND m.concentration_pass_quarters >= 6
              AND b.best_bet_count >= 1
            """
        ).fetchone()[0]
        roster_count = snapshot.execute(
            """
            WITH persistent_best_bets AS (
                SELECT cik, count(*) AS best_bet_count
                FROM (
                    SELECT cik, security_key
                    FROM manager_position_quarters
                    WHERE quarter_index <= 5
                      AND weight_nonoption_pct >= 3
                    GROUP BY cik, security_key
                    HAVING count(*) = 5
                ) positions
                GROUP BY cik
            )
            SELECT count(*)
            FROM manager_metrics m
            JOIN persistent_best_bets b USING (cik)
            WHERE m.median_reported_value_4q >= 10000000000
              AND m.latest_stock_count >= 1
              AND m.direct_stock_pct >= 80
              AND m.top10_pct >= 40
              AND m.concentration_pass_quarters >= 6
              AND b.best_bet_count >= 1
              AND m.is_current_roster
            """
        ).fetchone()[0]
        result = {
            "report_period": report_period,
            "manager_count": len(manager_rows),
            "durable_position_count": len(position_rows),
            "position_quarter_count": len(position_quarter_rows),
            "default_count": default_count,
            "default_roster_count": roster_count,
            "performance_summary_count": len(performance_summary_rows),
            "performance_monthly_count": len(performance_monthly_rows),
            "snapshot_path": str(snapshot_file),
            "pointer_path": str(pointer_file),
        }
    finally:
        source.close()
        snapshot.close()
    os.replace(staging_file, snapshot_file)
    pointer_temporary = pointer_file.with_suffix(".json.partial")
    pointer_temporary.write_text(
        json.dumps(
            {
                "generation": generation_name,
                "report_period": str(report_period),
                "source_fingerprint": source_fingerprint,
                "published_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(pointer_temporary, pointer_file)
    generations = sorted(
        pointer_file.parent.glob("screening_snapshot.*.duckdb"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_generation in generations[3:]:
        try:
            old_generation.unlink()
        except PermissionError:
            pass
    return result


class ScreeningService:
    def __init__(self, snapshot_path: str | Path = DEFAULT_SNAPSHOT_POINTER):
        self.snapshot_path = Path(snapshot_path)

    def get_screening_results(
        self,
        *,
        minimum_size_billions: float = 10.0,
        minimum_stock_count: int = 1,
        minimum_direct_stock_pct: float = 80.0,
        minimum_top10_pct: float = 40.0,
        minimum_concentration_quarters: int = 6,
        minimum_best_bet_weight_pct: float = 3.0,
        best_bet_duration_months: int = 12,
        minimum_best_bet_count: int = 1,
        benchmark_hurdle: str = "none",
        minimum_excess_cagr: float = 0.0,
        minimum_beat_consistency: float | None = None,
        maximum_drawdown: float | None = None,
        maximum_turnover_pct: float | None = None,
        require_durable_position: bool = False,
        roster_only: bool = False,
        search: str | None = None,
        performance_window: str = "3Y",
        minimum_spy_excess_cagr: float | None = None,
        minimum_qqq_excess_cagr: float | None = None,
        require_performance: bool = False,
    ) -> dict:
        if not self.snapshot_path.exists():
            return {
                "data": [],
                "summary": {},
                "defaults": DEFAULT_FILTERS,
                "error": "Screening snapshot has not been generated",
            }

        resolved_snapshot = resolve_snapshot_path(self.snapshot_path)
        connection = duckdb.connect(str(resolved_snapshot), read_only=True)
        try:
            normalized_window = performance_window.strip().upper()
            if normalized_window not in {"3Y", "5Y", "FULL"}:
                raise ValueError("performance_window must be 3Y, 5Y, or FULL")
            if best_bet_duration_months not in BEST_BET_DURATION_SNAPSHOTS:
                raise ValueError(
                    "best_bet_duration_months must be 6, 12, 18, or 24"
                )
            if minimum_best_bet_weight_pct < BEST_BET_CUBE_WEIGHT_FLOOR_PCT:
                raise ValueError(
                    "minimum_best_bet_weight_pct cannot be below "
                    f"{BEST_BET_CUBE_WEIGHT_FLOOR_PCT:g}"
                )
            normalized_hurdle = benchmark_hurdle.strip().lower()
            if normalized_hurdle not in {"none", "spy", "qqq", "both"}:
                raise ValueError(
                    "benchmark_hurdle must be none, spy, qqq, or both"
                )
            available_tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                    """
                ).fetchall()
            }
            has_performance = "manager_performance" in available_tables
            has_position_cube = "manager_position_quarters" in available_tables
            if not has_position_cube:
                return {
                    "data": [],
                    "summary": {},
                    "metadata": {
                        "position_cube_available": False,
                    },
                    "defaults": DEFAULT_FILTERS,
                    "error": (
                        "Screening snapshot predates dynamic best-bet facts; "
                        "run refresh-screening"
                    ),
                }
            conditions = [
                "m.median_reported_value_4q >= ?",
                "m.latest_stock_count >= ?",
                "m.direct_stock_pct >= ?",
                "m.top10_pct >= ?",
                "p.concentration_pass_quarters >= ?",
            ]
            params = [
                minimum_size_billions * 1_000_000_000,
                minimum_stock_count,
                minimum_direct_stock_pct,
                minimum_top10_pct,
                minimum_concentration_quarters,
            ]
            query_prefix_params: list[object] = [minimum_top10_pct]
            required_best_bet_snapshots = BEST_BET_DURATION_SNAPSHOTS[
                best_bet_duration_months
            ]
            if has_position_cube:
                best_bet_join = """
                JOIN (
                    SELECT cik, count(*)::INTEGER AS persistent_best_bet_count
                    FROM (
                        SELECT cik, security_key
                        FROM manager_position_quarters
                        WHERE quarter_index <= ?
                          AND weight_nonoption_pct >= ?
                        GROUP BY cik, security_key
                        HAVING count(*) = ?
                    ) qualified_positions
                    GROUP BY cik
                ) bb USING (cik)
                """
                best_bet_projection = (
                    "bb.persistent_best_bet_count "
                    "AS persistent_best_bet_count"
                )
                query_prefix_params.extend(
                    [
                        required_best_bet_snapshots,
                        minimum_best_bet_weight_pct,
                        required_best_bet_snapshots,
                    ]
                )
                conditions.append("bb.persistent_best_bet_count >= ?")
                params.append(minimum_best_bet_count)
            else:
                best_bet_join = ""
                best_bet_projection = (
                    "NULL::INTEGER AS persistent_best_bet_count"
                )
            if maximum_turnover_pct is not None:
                conditions.append("m.annualized_turnover_pct <= ?")
                params.append(maximum_turnover_pct)
            if require_durable_position:
                conditions.append("m.durable_position_count > 0")
            if roster_only:
                conditions.append("m.is_current_roster")
            if search:
                conditions.append(
                    "(m.manager_name ILIKE ? OR m.cik ILIKE ? OR m.roster_name ILIKE ?)"
                )
                value = f"%{search.strip()}%"
                params.extend([value, value, value])
            structural_conditions = list(conditions)
            structural_params = list(params)
            if has_performance:
                if require_performance:
                    conditions.append("perf.status = 'AVAILABLE'")
                excess_operator = (
                    ">=" if minimum_excess_cagr > 0 else ">"
                )
                if normalized_hurdle in {"spy", "both"}:
                    conditions.extend(
                        [
                            "perf.status = 'AVAILABLE'",
                            f"perf.spy_excess_cagr {excess_operator} ?",
                        ]
                    )
                    params.append(minimum_excess_cagr)
                    if minimum_beat_consistency is not None:
                        conditions.append(
                            "perf.spy_quarterly_beat_rate >= ?"
                        )
                        params.append(minimum_beat_consistency)
                if normalized_hurdle in {"qqq", "both"}:
                    conditions.extend(
                        [
                            "perf.status = 'AVAILABLE'",
                            f"perf.qqq_excess_cagr {excess_operator} ?",
                        ]
                    )
                    params.append(minimum_excess_cagr)
                    if minimum_beat_consistency is not None:
                        conditions.append(
                            "perf.qqq_quarterly_beat_rate >= ?"
                        )
                        params.append(minimum_beat_consistency)
                if maximum_drawdown is not None:
                    conditions.extend(
                        [
                            "perf.status = 'AVAILABLE'",
                            "perf.max_drawdown >= ?",
                        ]
                    )
                    params.append(-abs(maximum_drawdown))
                if minimum_spy_excess_cagr is not None:
                    legacy_spy_operator = (
                        ">=" if minimum_spy_excess_cagr > 0 else ">"
                    )
                    conditions.extend(
                        [
                            "perf.status = 'AVAILABLE'",
                            f"perf.spy_excess_cagr {legacy_spy_operator} ?",
                        ]
                    )
                    params.append(minimum_spy_excess_cagr)
                if minimum_qqq_excess_cagr is not None:
                    legacy_qqq_operator = (
                        ">=" if minimum_qqq_excess_cagr > 0 else ">"
                    )
                    conditions.extend(
                        [
                            "perf.status = 'AVAILABLE'",
                            f"perf.qqq_excess_cagr {legacy_qqq_operator} ?",
                        ]
                    )
                    params.append(minimum_qqq_excess_cagr)
                performance_join = """
                LEFT JOIN manager_performance perf
                  ON perf.cik = m.cik
                 AND perf."window" = ?
                 AND perf.cost_bps = 0
                """
                performance_projection = """
                    perf.status AS performance_status,
                    perf."window" AS performance_window,
                    perf.start_date AS performance_start_date,
                    perf.end_date AS performance_end_date,
                    perf.years AS performance_years,
                    perf.estimated_cagr,
                    perf.spy_cagr,
                    perf.qqq_cagr,
                    perf.spy_excess_cagr,
                    perf.qqq_excess_cagr,
                    perf.max_drawdown,
                    perf.monthly_sharpe_rf0,
                    perf.spy_information_ratio,
                    perf.qqq_information_ratio,
                    perf.spy_quarterly_beat_rate,
                    perf.qqq_quarterly_beat_rate,
                    perf.mapping_coverage AS performance_mapping_coverage,
                    perf.priced_coverage AS performance_priced_coverage,
                    perf.interval_count AS performance_interval_count,
                    perf.unavailable_reason AS performance_unavailable_reason,
                    perf.label AS performance_label,
                    perf.disclaimer AS performance_disclaimer
                """
                query_prefix_params.append(normalized_window)
            else:
                if (
                    require_performance
                    or normalized_hurdle != "none"
                    or maximum_drawdown is not None
                    or minimum_spy_excess_cagr is not None
                    or minimum_qqq_excess_cagr is not None
                ):
                    conditions.append("false")
                performance_join = ""
                performance_projection = """
                    NULL::VARCHAR AS performance_status,
                    NULL::VARCHAR AS performance_window,
                    NULL::DATE AS performance_start_date,
                    NULL::DATE AS performance_end_date,
                    NULL::DOUBLE AS performance_years,
                    NULL::DOUBLE AS estimated_cagr,
                    NULL::DOUBLE AS spy_cagr,
                    NULL::DOUBLE AS qqq_cagr,
                    NULL::DOUBLE AS spy_excess_cagr,
                    NULL::DOUBLE AS qqq_excess_cagr,
                    NULL::DOUBLE AS max_drawdown,
                    NULL::DOUBLE AS monthly_sharpe_rf0,
                    NULL::DOUBLE AS spy_information_ratio,
                    NULL::DOUBLE AS qqq_information_ratio,
                    NULL::DOUBLE AS spy_quarterly_beat_rate,
                    NULL::DOUBLE AS qqq_quarterly_beat_rate,
                    NULL::DOUBLE AS performance_mapping_coverage,
                    NULL::DOUBLE AS performance_priced_coverage,
                    NULL::INTEGER AS performance_interval_count,
                    'No compatible performance result'::VARCHAR
                        AS performance_unavailable_reason,
                    NULL::VARCHAR AS performance_label,
                    NULL::VARCHAR AS performance_disclaimer
                """

            where_clause = " AND ".join(conditions)
            structural_where_clause = " AND ".join(structural_conditions)
            if has_performance:
                structural_counts = connection.execute(
                    f"""
                    SELECT
                        count(*)::INTEGER AS structural_candidate_count,
                        count(*) FILTER (
                            WHERE perf.status = 'AVAILABLE'
                        )::INTEGER AS structural_performance_available_count
                    FROM manager_metrics m
                    JOIN (
                        SELECT
                            cik,
                            count(*) FILTER (WHERE top10_pct >= ?)
                                AS concentration_pass_quarters
                        FROM manager_quarter_concentration
                        GROUP BY cik
                    ) p USING (cik)
                    {best_bet_join}
                    {performance_join}
                    WHERE {structural_where_clause}
                    """,
                    [*query_prefix_params, *structural_params],
                ).fetchone()
                performance_fact_manager_count = connection.execute(
                    """
                    SELECT count(DISTINCT cik)
                    FROM manager_performance
                    WHERE "window" = ? AND cost_bps = 0
                    """,
                    [normalized_window],
                ).fetchone()[0]
            else:
                structural_counts = connection.execute(
                    f"""
                    SELECT count(*)::INTEGER
                    FROM manager_metrics m
                    JOIN (
                        SELECT
                            cik,
                            count(*) FILTER (WHERE top10_pct >= ?)
                                AS concentration_pass_quarters
                        FROM manager_quarter_concentration
                        GROUP BY cik
                    ) p USING (cik)
                    {best_bet_join}
                    WHERE {structural_where_clause}
                    """,
                    [*query_prefix_params, *structural_params],
                ).fetchone()
                structural_counts = (structural_counts[0], 0)
                performance_fact_manager_count = 0
            rows = connection.execute(
                f"""
                SELECT
                    m.* EXCLUDE (concentration_pass_quarters),
                    p.concentration_pass_quarters,
                    {best_bet_projection},
                    {performance_projection}
                FROM manager_metrics m
                JOIN (
                    SELECT
                        cik,
                        count(*) FILTER (WHERE top10_pct >= ?)
                            AS concentration_pass_quarters
                    FROM manager_quarter_concentration
                    GROUP BY cik
                ) p USING (cik)
                {best_bet_join}
                {performance_join}
                WHERE {where_clause}
                ORDER BY m.median_reported_value_4q DESC, m.manager_name
                """,
                [*query_prefix_params, *params],
            )
            columns = [item[0] for item in rows.description]
            data = [dict(zip(columns, row)) for row in rows.fetchall()]

            ciks = [item["cik"] for item in data]
            positions_by_cik = {}
            if ciks and has_position_cube:
                placeholders = ",".join("?" for _ in ciks)
                position_rows = connection.execute(
                    f"""
                    SELECT
                        cik,
                        security_key,
                        max(cusip) FILTER (WHERE quarter_index = 1) AS cusip,
                        max(ticker) FILTER (WHERE quarter_index = 1) AS ticker,
                        max(issuer) FILTER (WHERE quarter_index = 1) AS issuer,
                        max(title_of_class)
                            FILTER (WHERE quarter_index = 1) AS title_of_class,
                        max(weight_nonoption_pct)
                            FILTER (WHERE quarter_index = 1)
                            AS latest_weight_pct,
                        max(direct_sleeve_weight_pct)
                            FILTER (WHERE quarter_index = 1)
                            AS latest_direct_sleeve_pct,
                        max(position_rank)
                            FILTER (WHERE quarter_index = 1) AS latest_rank,
                        min(weight_nonoption_pct) AS minimum_weight_pct,
                        count(*)::INTEGER AS observed_snapshots
                    FROM manager_position_quarters
                    WHERE cik IN ({placeholders})
                      AND quarter_index <= ?
                      AND weight_nonoption_pct >= ?
                    GROUP BY cik, security_key
                    HAVING count(*) = ?
                    ORDER BY cik, latest_weight_pct DESC
                    """,
                    [
                        *ciks,
                        required_best_bet_snapshots,
                        minimum_best_bet_weight_pct,
                        required_best_bet_snapshots,
                    ],
                )
                position_columns = [item[0] for item in position_rows.description]
                for row in position_rows.fetchall():
                    item = dict(zip(position_columns, row))
                    positions_by_cik.setdefault(item["cik"], []).append(item)
            for item in data:
                best_bets = positions_by_cik.get(item["cik"], [])
                item["persistent_best_bets"] = best_bets
                item["durable_positions"] = best_bets

            metadata = connection.execute(
                "SELECT * FROM snapshot_metadata LIMIT 1"
            )
            metadata_columns = [item[0] for item in metadata.description]
            metadata_row = metadata.fetchone()
            meta = (
                dict(zip(metadata_columns, metadata_row))
                if metadata_row
                else {}
            )
            meta["position_cube_available"] = has_position_cube
            summary = {
                "candidate_count": len(data),
                "structural_candidate_count": structural_counts[0],
                "structural_performance_available_count": structural_counts[1],
                "performance_fact_manager_count": performance_fact_manager_count,
                "roster_count": sum(1 for item in data if item["is_current_roster"]),
                "median_size_billions": statistics.median(
                    item["median_reported_value_4q"] / 1_000_000_000
                    for item in data
                ) if data else 0,
                "median_turnover_pct": statistics.median(
                    item["annualized_turnover_pct"] for item in data
                ) if data else 0,
                "performance_available_count": sum(
                    1 for item in data
                    if item["performance_status"] == "AVAILABLE"
                ),
                "beat_spy_count": sum(
                    1 for item in data
                    if item["performance_status"] == "AVAILABLE"
                    and item["spy_excess_cagr"] is not None
                    and item["spy_excess_cagr"] > 0
                ),
                "beat_qqq_count": sum(
                    1 for item in data
                    if item["performance_status"] == "AVAILABLE"
                    and item["qqq_excess_cagr"] is not None
                    and item["qqq_excess_cagr"] > 0
                ),
                "median_estimated_cagr": statistics.median(
                    item["estimated_cagr"] for item in data
                    if item["performance_status"] == "AVAILABLE"
                    and item["estimated_cagr"] is not None
                ) if any(
                    item["performance_status"] == "AVAILABLE"
                    and item["estimated_cagr"] is not None
                    for item in data
                ) else None,
            }
            return {
                "data": data,
                "summary": summary,
                "metadata": meta,
                "defaults": DEFAULT_FILTERS,
            }
        finally:
            connection.close()
