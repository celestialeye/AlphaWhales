from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from .awfi import AWFI_VERSION, HORIZON_THRESHOLDS
from .config import (
    ALLOWED_OPTIMIZATION_HORIZONS,
    BLOCK_WEIGHT_PROFILES,
)
from .research import previous_quarter


ACTION_PROTOCOL_VERSION = "awfi-action-challenger-v1"
DEFAULT_PARENT_DB = Path("data/investor_screening/predictive_sentiment.duckdb")
DEFAULT_OUTPUT_DB = Path(
    "data/investor_screening/awfi_action_experiments.duckdb"
)
FUNDAMENTAL_PROFILE_WEIGHTS = {
    "QUALITY_20": (0.80, 0.20, 0.0, 0.0),
    "INVESTMENT_20": (0.80, 0.0, 0.20, 0.0),
    "SAFETY_20": (0.80, 0.0, 0.0, 0.20),
    "FUNDAMENTAL_BALANCED_25": (0.75, 0.0, 0.0, 0.25),
}
PORTFOLIO_ACTIONS = (
    "ENTER",
    "INCREASE",
    "HOLD",
    "DECREASE",
    "EXIT",
)
ACTIVE_ACTIONS = (*PORTFOLIO_ACTIONS, "SKIP")
SELECTION_COST_BPS = 25.0
FIXED_SCREEN_THRESHOLDS = (50.0, 50.0, 25.0, 75.0)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS action_experiment_runs (
    run_id VARCHAR PRIMARY KEY,
    protocol_version VARCHAR NOT NULL,
    parent_run_id VARCHAR NOT NULL,
    parent_awfi_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    config_json JSON NOT NULL,
    source_fingerprint VARCHAR NOT NULL,
    summary_json JSON,
    last_error VARCHAR
);

CREATE TABLE IF NOT EXISTS action_selections (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    report_period DATE NOT NULL,
    status VARCHAR NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS action_candidate_trials (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    report_period DATE NOT NULL,
    stage VARCHAR NOT NULL,
    candidate_order INTEGER NOT NULL,
    selected BOOLEAN NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS action_predictions (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR,
    profile_id VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    held_before_signal BOOLEAN NOT NULL,
    action VARCHAR NOT NULL,
    security_return DOUBLE NOT NULL,
    excess_return DOUBLE NOT NULL,
    benchmark_payoff DOUBLE NOT NULL,
    cash_payoff DOUBLE NOT NULL,
    net_benchmark_payoff DOUBLE NOT NULL,
    success BOOLEAN NOT NULL,
    split_affected BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS action_metrics (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    action VARCHAR NOT NULL,
    metric VARCHAR NOT NULL,
    value DOUBLE
);

CREATE TABLE IF NOT EXISTS action_profile_metrics (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    profile_id VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS action_fundamental_features (
    run_id VARCHAR NOT NULL,
    report_period DATE NOT NULL,
    cusip VARCHAR NOT NULL,
    ticker VARCHAR,
    issuer_cik VARCHAR,
    feature_date DATE NOT NULL,
    fundamental_report_period DATE,
    quality_score DOUBLE,
    investment_score DOUBLE,
    safety_score DOUBLE,
    fundamental_balanced_score DOUBLE,
    details_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS action_multiple_testing (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    action VARCHAR NOT NULL,
    mean_payoff DOUBLE,
    t_stat DOUBLE,
    p_value DOUBLE,
    holm_p_value DOUBLE,
    passes_t3 BOOLEAN NOT NULL,
    passes_holm BOOLEAN NOT NULL
);

ALTER TABLE action_multiple_testing
ADD COLUMN IF NOT EXISTS mean_success_rate DOUBLE;

ALTER TABLE action_multiple_testing
ADD COLUMN IF NOT EXISTS bootstrap_lower_success_edge DOUBLE;

CREATE TABLE IF NOT EXISTS action_factor_diagnostics (
    run_id VARCHAR NOT NULL,
    horizon INTEGER NOT NULL,
    factor_id VARCHAR NOT NULL,
    quarter_count INTEGER NOT NULL,
    observation_count INTEGER NOT NULL,
    coverage DOUBLE NOT NULL,
    mean_rank_ic DOUBLE,
    median_rank_ic DOUBLE,
    positive_rank_ic_fraction DOUBLE,
    hac_t_stat DOUBLE,
    p_value DOUBLE,
    holm_p_value DOUBLE,
    passes_t3 BOOLEAN NOT NULL,
    passes_holm BOOLEAN NOT NULL
);
"""


@dataclass(frozen=True)
class ActionExperimentConfig:
    horizons: tuple[int, ...] = ALLOWED_OPTIMIZATION_HORIZONS
    first_test_period: date = date(2023, 3, 31)
    embargo_sessions: int = 5
    minimum_inner_validation_quarters: int = 4
    minimum_inner_actions: int = 20
    minimum_action_types: int = 3
    minimum_feature_availability: float = 0.80
    minimum_flat_state_coverage: float = 0.80
    transaction_cost_bps: float = SELECTION_COST_BPS
    random_seed: int = 17
    profile_mode: str = "ALL"

    def __post_init__(self) -> None:
        if not self.horizons or any(
            horizon not in ALLOWED_OPTIMIZATION_HORIZONS
            for horizon in self.horizons
        ):
            raise ValueError(
                "horizons must be selected from "
                f"{ALLOWED_OPTIMIZATION_HORIZONS}"
            )
        if self.embargo_sessions < 0:
            raise ValueError("embargo_sessions must be non-negative")
        if self.minimum_inner_validation_quarters < 2:
            raise ValueError(
                "minimum_inner_validation_quarters must be at least 2"
            )
        if self.minimum_inner_actions < 1:
            raise ValueError("minimum_inner_actions must be positive")
        if not 1 <= self.minimum_action_types <= len(PORTFOLIO_ACTIONS):
            raise ValueError("minimum_action_types must be between 1 and 5")
        for value in (
            self.minimum_feature_availability,
            self.minimum_flat_state_coverage,
        ):
            if not 0 < value <= 1:
                raise ValueError("coverage thresholds must be in (0, 1]")
        if self.transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be non-negative")
        if self.profile_mode not in {"ALL", "AWFI_V2_ONLY"}:
            raise ValueError(
                "profile_mode must be ALL or AWFI_V2_ONLY"
            )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["horizons"] = list(self.horizons)
        result["first_test_period"] = self.first_test_period.isoformat()
        return result


@dataclass(frozen=True)
class ActionCandidate:
    profile_id: str
    enter_threshold: float
    increase_threshold: float
    decrease_threshold: float
    exit_threshold: float

    def __post_init__(self) -> None:
        if (
            self.profile_id != "AWFI_V2_CONTROL"
            and self.profile_id not in BLOCK_WEIGHT_PROFILES
            and self.profile_id not in FUNDAMENTAL_PROFILE_WEIGHTS
        ):
            raise ValueError(f"Unsupported profile_id: {self.profile_id}")
        if not 0 < self.enter_threshold <= 100:
            raise ValueError("enter_threshold must be in (0, 100]")
        if not 0 < self.increase_threshold <= 100:
            raise ValueError("increase_threshold must be in (0, 100]")
        if not 0 < self.decrease_threshold < self.exit_threshold <= 100:
            raise ValueError(
                "decrease_threshold must be below exit_threshold"
            )


def connect_action_database(
    path: Path,
) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    connection.execute(SCHEMA_SQL)
    return connection


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, (date, datetime, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return _clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _clean_json(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _nullable(value: Any) -> Any:
    return None if value is None or pd.isna(value) else value


def _parent_run(
    connection: duckdb.DuckDBPyConnection,
    run_id: str | None,
) -> tuple[str, str]:
    if run_id:
        row = connection.execute(
            """
            SELECT r.run_id, a.awfi_version
            FROM research_runs r
            JOIN (
                SELECT run_id, min(awfi_version) AS awfi_version,
                       count(DISTINCT awfi_version) AS version_count
                FROM awfi_scores
                GROUP BY run_id
            ) a USING (run_id)
            WHERE r.run_id = ? AND r.status = 'COMPLETE'
              AND a.version_count = 1
            """,
            [run_id],
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT r.run_id, a.awfi_version
            FROM research_runs r
            JOIN (
                SELECT run_id, min(awfi_version) AS awfi_version,
                       count(DISTINCT awfi_version) AS version_count
                FROM awfi_scores
                GROUP BY run_id
            ) a USING (run_id)
            WHERE r.status = 'COMPLETE' AND a.version_count = 1
            ORDER BY r.completed_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise ValueError("No completed parent AWFI research run was found")
    if row[1] != AWFI_VERSION:
        raise ValueError(
            f"Action experiments require {AWFI_VERSION}; found {row[1]}"
        )
    return str(row[0]), str(row[1])


def _source_fingerprint(
    connection: duckdb.DuckDBPyConnection,
    parent_run_id: str,
) -> str:
    digest = hashlib.sha256()
    for table, columns in (
        (
            "awfi_scores",
            "report_period, cusip, horizon, score, signal",
        ),
        (
            "forward_labels",
            "report_period, cusip, horizon, status, entry_index, "
            "exit_index, security_return, excess_return",
        ),
        (
            "manager_changes",
            "canonical_cik, report_period, cusip, status, "
            "previous_shares, current_shares",
        ),
        (
            "manager_snapshots",
            "canonical_cik, report_period, status, effective_accessions, "
            "source_accessions, eligible_value, position_count",
        ),
        (
            "run_top_holdings",
            "canonical_cik, report_period, holding_rank, cusip, issuer, "
            "title, portfolio_weight, reported_value",
        ),
        (
            "run_mapping",
            "cusip, ticker, market_symbol, source, retrieved_at",
        ),
        (
            "decomposed_features",
            "report_period, cusip, horizon, features_json",
        ),
    ):
        rows = connection.execute(
            f"""
            SELECT {columns}
            FROM {table}
            WHERE run_id = ?
            ORDER BY ALL
            """,
            [parent_run_id],
        ).fetchall()
        digest.update(table.encode("utf-8"))
        for row in rows:
            digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()


def _snapshot_pair_coverage(
    connection: duckdb.DuckDBPyConnection,
    parent_run_id: str,
) -> dict[date, float]:
    rows = connection.execute(
        """
        SELECT canonical_cik, report_period, status
        FROM manager_snapshots
        WHERE run_id = ?
        """,
        [parent_run_id],
    ).fetchall()
    statuses = {
        (str(cik), period): str(status)
        for cik, period, status in rows
    }
    managers = sorted({str(row[0]) for row in rows})
    periods = sorted({row[1] for row in rows})
    if not managers:
        return {}
    return {
        period: sum(
            statuses.get((cik, period)) == "VALID"
            and statuses.get((cik, previous_quarter(period))) == "VALID"
            for cik in managers
        )
        / len(managers)
        for period in periods
    }


def load_action_observations(
    connection: duckdb.DuckDBPyConnection,
    parent_run_id: str,
    config: ActionExperimentConfig,
) -> pd.DataFrame:
    feature_rows = connection.execute(
        """
        SELECT features_json
        FROM decomposed_features
        WHERE run_id = ?
        """,
        [parent_run_id],
    ).fetchall()
    if not feature_rows:
        raise ValueError("Parent run contains no decomposed features")
    features = pd.DataFrame(
        [json.loads(str(row[0])) for row in feature_rows]
    )
    features["report_period"] = pd.to_datetime(
        features["report_period"]
    ).dt.date
    features["feature_date"] = pd.to_datetime(
        features["feature_date"]
    ).dt.date

    labels = connection.execute(
        """
        SELECT report_period, cusip, ticker, market_symbol, horizon,
               status AS label_status, entry_index, exit_index,
               security_return, excess_return
        FROM forward_labels
        WHERE run_id = ?
        """,
        [parent_run_id],
    ).fetchdf()
    labels["report_period"] = pd.to_datetime(
        labels["report_period"]
    ).dt.date
    control = connection.execute(
        """
        SELECT report_period, cusip, horizon, score AS awfi_v2_score
        FROM awfi_scores
        WHERE run_id = ?
        """,
        [parent_run_id],
    ).fetchdf()
    control["report_period"] = pd.to_datetime(
        control["report_period"]
    ).dt.date
    state = connection.execute(
        """
        SELECT
            report_period,
            cusip,
            count(DISTINCT CASE WHEN previous_shares > 0
                           THEN canonical_cik END) AS prior_holder_count,
            count(DISTINCT CASE WHEN current_shares > 0
                           THEN canonical_cik END) AS current_holder_count,
            count(*) FILTER (WHERE status = 'NEW') AS manager_new_count,
            count(*) FILTER (WHERE status = 'INCREASED')
                AS manager_increased_count,
            count(*) FILTER (WHERE status = 'UNCHANGED')
                AS manager_unchanged_count,
            count(*) FILTER (WHERE status = 'DECREASED')
                AS manager_decreased_count,
            count(*) FILTER (WHERE status = 'CLOSED')
                AS manager_closed_count
        FROM manager_changes
        WHERE run_id = ?
        GROUP BY report_period, cusip
        """,
        [parent_run_id],
    ).fetchdf()
    state["report_period"] = pd.to_datetime(
        state["report_period"]
    ).dt.date

    frame = features.merge(
        labels,
        on=["report_period", "cusip", "horizon"],
        how="inner",
        suffixes=("", "_label"),
    ).merge(
        control,
        on=["report_period", "cusip", "horizon"],
        how="inner",
    ).merge(
        state,
        on=["report_period", "cusip"],
        how="left",
    )
    coverage = _snapshot_pair_coverage(connection, parent_run_id)
    frame["state_pair_coverage"] = frame["report_period"].map(coverage)
    frame["prior_holder_count"] = frame["prior_holder_count"].fillna(0)
    frame["held_before_signal"] = frame["prior_holder_count"] > 0
    frame["state_known"] = (
        frame["held_before_signal"]
        | (
            frame["state_pair_coverage"]
            >= config.minimum_flat_state_coverage
        )
    )
    frame["action_score"] = _purchase_led_action_score(frame)
    return frame


def _purchase_led_action_score(frame: pd.DataFrame) -> pd.Series:
    numerator = (
        frame["new_strength"]
        + 0.75 * frame["increased_strength"]
        - 0.25 * frame["decreased_strength"]
        - 0.25 * frame["closed_strength"]
    )
    denominator = (
        frame["new_strength"]
        + 0.75 * frame["increased_strength"]
        + 0.25 * frame["decreased_strength"]
        + 0.25 * frame["closed_strength"]
    )
    return pd.Series(
        np.where(denominator > 0, 100.0 * numerator / denominator, 0.0),
        index=frame.index,
        dtype=float,
    )


def compose_profile(
    rows: pd.DataFrame,
    profile_id: str,
) -> pd.Series:
    if profile_id == "AWFI_V2_CONTROL":
        return pd.to_numeric(rows["awfi_v2_score"], errors="coerce")
    fundamental_weights = FUNDAMENTAL_PROFILE_WEIGHTS.get(profile_id)
    if fundamental_weights is not None:
        columns = (
            "awfi_v2_score",
            "quality_score",
            "investment_score",
            "safety_score",
        )
        if profile_id == "FUNDAMENTAL_BALANCED_25":
            columns = (
                "awfi_v2_score",
                "quality_score",
                "investment_score",
                "safety_score",
            )
            weights = (0.75, 0.10, 0.0625, 0.0875)
        else:
            weights = fundamental_weights
        required = [
            column
            for column, weight in zip(columns, weights)
            if weight != 0
        ]
        available = rows[required].notna().all(axis=1)
        score = pd.Series(np.nan, index=rows.index, dtype=float)
        score.loc[available] = sum(
            weight * pd.to_numeric(rows.loc[available, column])
            for column, weight in zip(columns, weights)
            if weight != 0
        )
        return score.clip(-100.0, 100.0)
    weights = BLOCK_WEIGHT_PROFILES.get(profile_id)
    if weights is None:
        raise ValueError(f"Unsupported profile_id: {profile_id}")
    columns = (
        "alpha_score",
        "action_score",
        "portfolio_weight_score",
        "acceleration_score",
        "crowding_score",
        "persistence_score",
        "technical_score",
    )
    required = [
        column
        for column, weight in zip(columns, weights)
        if weight != 0
    ]
    available = rows[required].notna().all(axis=1)
    score = pd.Series(np.nan, index=rows.index, dtype=float)
    score.loc[available] = sum(
        weight * pd.to_numeric(rows.loc[available, column])
        for column, weight in zip(columns, weights)
        if weight != 0
    )
    return score.clip(-100.0, 100.0)


def classify_actions(
    rows: pd.DataFrame,
    candidate: ActionCandidate,
) -> pd.DataFrame:
    result = rows.copy()
    result["score"] = compose_profile(result, candidate.profile_id)
    result["action"] = "UNKNOWN"
    valid = result["state_known"] & result["score"].notna()
    flat = valid & ~result["held_before_signal"]
    held = valid & result["held_before_signal"]
    result.loc[flat, "action"] = "SKIP"
    result.loc[
        flat & (result["score"] >= candidate.enter_threshold),
        "action",
    ] = "ENTER"
    result.loc[held, "action"] = "HOLD"
    result.loc[
        held & (result["score"] >= candidate.increase_threshold),
        "action",
    ] = "INCREASE"
    result.loc[
        held & (result["score"] <= -candidate.decrease_threshold),
        "action",
    ] = "DECREASE"
    result.loc[
        held & (result["score"] <= -candidate.exit_threshold),
        "action",
    ] = "EXIT"
    return result


def add_action_payoffs(
    rows: pd.DataFrame,
    *,
    transaction_cost_bps: float,
) -> pd.DataFrame:
    result = rows.copy()
    direction = result["action"].map(
        {
            "ENTER": 1.0,
            "INCREASE": 1.0,
            "HOLD": 1.0,
            "DECREASE": -1.0,
            "EXIT": -1.0,
        }
    )
    actionable = result["action"].isin(PORTFOLIO_ACTIONS)
    ready = actionable & (result["label_status"] == "READY")
    result["benchmark_payoff"] = np.where(
        ready,
        direction * result["excess_return"],
        np.nan,
    )
    result["cash_payoff"] = np.where(
        ready,
        direction * result["security_return"],
        np.nan,
    )
    cost = np.where(
        result["action"].isin(("ENTER", "INCREASE", "DECREASE", "EXIT")),
        transaction_cost_bps / 10_000.0,
        0.0,
    )
    result["net_benchmark_payoff"] = (
        result["benchmark_payoff"] - cost
    )
    result["success"] = np.where(
        result["net_benchmark_payoff"].notna(),
        result["net_benchmark_payoff"] > 0,
        pd.NA,
    )
    return result


def _action_summary(rows: pd.DataFrame, action: str) -> dict[str, Any]:
    if rows.empty or not {
        "held_before_signal",
        "action",
        "net_benchmark_payoff",
        "report_period",
        "cusip",
        "success",
    }.issubset(rows.columns):
        return {
            "action": action,
            "opportunities": 0,
            "assigned": 0,
            "coverage": math.nan,
            "quarters": 0,
            "cusips": 0,
            "success_rate": math.nan,
            "mean_payoff": math.nan,
            "median_payoff": math.nan,
            "payoff_p10": math.nan,
            "payoff_p90": math.nan,
            "quarter_mean_payoff": math.nan,
        }
    opportunities = (
        rows[~rows["held_before_signal"]]
        if action == "ENTER"
        else rows[rows["held_before_signal"]]
    )
    selected = rows[
        (rows["action"] == action)
        & rows["net_benchmark_payoff"].notna()
    ]
    payoffs = selected["net_benchmark_payoff"].astype(float)
    successes = selected["success"].astype(bool)
    if len(selected):
        quarters = selected.groupby("report_period")[
            "net_benchmark_payoff"
        ].mean()
    else:
        quarters = pd.Series(dtype=float)
    return {
        "action": action,
        "opportunities": int(len(opportunities)),
        "assigned": int(len(selected)),
        "coverage": (
            float(len(selected) / len(opportunities))
            if len(opportunities)
            else math.nan
        ),
        "quarters": int(selected["report_period"].nunique()),
        "cusips": int(selected["cusip"].nunique()),
        "success_rate": (
            float(successes.mean()) if len(successes) else math.nan
        ),
        "mean_payoff": (
            float(payoffs.mean()) if len(payoffs) else math.nan
        ),
        "median_payoff": (
            float(payoffs.median()) if len(payoffs) else math.nan
        ),
        "payoff_p10": (
            float(payoffs.quantile(0.10)) if len(payoffs) else math.nan
        ),
        "payoff_p90": (
            float(payoffs.quantile(0.90)) if len(payoffs) else math.nan
        ),
        "quarter_mean_payoff": (
            float(quarters.mean()) if len(quarters) else math.nan
        ),
    }


def evaluate_candidate(
    rows: pd.DataFrame,
    candidate: ActionCandidate,
    config: ActionExperimentConfig,
) -> dict[str, Any]:
    scored = add_action_payoffs(
        classify_actions(rows, candidate),
        transaction_cost_bps=config.transaction_cost_bps,
    )
    feature_availability = float(scored["score"].notna().mean())
    actions = {
        action: _action_summary(scored, action)
        for action in PORTFOLIO_ACTIONS
    }
    eligible_actions = [
        values
        for values in actions.values()
        if values["assigned"] >= 3 and values["quarters"] >= 2
    ]
    long_present = any(
        actions[action]["assigned"] >= 3
        for action in ("ENTER", "INCREASE", "HOLD")
    )
    defensive_present = any(
        actions[action]["assigned"] >= 3
        for action in ("DECREASE", "EXIT")
    )
    assigned = sum(values["assigned"] for values in actions.values())
    validation_quarters = int(rows["report_period"].nunique())
    rejection_reasons = []
    if validation_quarters < config.minimum_inner_validation_quarters:
        rejection_reasons.append("MINIMUM_VALIDATION_QUARTERS")
    if assigned < config.minimum_inner_actions:
        rejection_reasons.append("MINIMUM_ACTIONS")
    if len(eligible_actions) < config.minimum_action_types:
        rejection_reasons.append("MINIMUM_ACTION_TYPES")
    if not long_present:
        rejection_reasons.append("NO_LONG_ACTION")
    if not defensive_present:
        rejection_reasons.append("NO_DEFENSIVE_ACTION")
    if feature_availability < config.minimum_feature_availability:
        rejection_reasons.append("MINIMUM_FEATURE_AVAILABILITY")
    macro_hit = (
        float(
            np.mean(
                [
                    values["success_rate"]
                    for values in eligible_actions
                ]
            )
        )
        if eligible_actions
        else math.nan
    )
    macro_payoff = (
        float(
            np.mean(
                [
                    values["quarter_mean_payoff"]
                    for values in eligible_actions
                ]
            )
        )
        if eligible_actions
        else math.nan
    )
    minimum_hit = (
        float(
            min(values["success_rate"] for values in eligible_actions)
        )
        if eligible_actions
        else math.nan
    )
    return {
        **asdict(candidate),
        "eligible": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "validation_quarters": validation_quarters,
        "feature_availability": feature_availability,
        "assigned_actions": assigned,
        "represented_actions": len(eligible_actions),
        "macro_action_hit_rate": macro_hit,
        "macro_action_payoff": macro_payoff,
        "minimum_action_hit_rate": minimum_hit,
        "actions": actions,
    }


def _selection_key(item: dict[str, Any]) -> tuple[Any, ...]:
    profile_order = (
        "AWFI_V2_CONTROL",
        "ALPHA_ONLY",
        "BALANCED",
        "ACTION_HEAVY",
        "ACCELERATION",
        "CROWDING",
        "PERSISTENCE",
        "TECHNICAL_BALANCED",
        "TECHNICAL_HEAVY",
        *FUNDAMENTAL_PROFILE_WEIGHTS,
    )
    return (
        item["represented_actions"],
        item["macro_action_hit_rate"],
        item["macro_action_payoff"],
        item["minimum_action_hit_rate"],
        item["feature_availability"],
        -profile_order.index(item["profile_id"]),
        -item["enter_threshold"],
        -item["increase_threshold"],
        -item["decrease_threshold"],
        -item["exit_threshold"],
    )


def _choose_candidate(
    trials: list[dict[str, Any]],
) -> dict[str, Any] | None:
    eligible = [item for item in trials if item["eligible"]]
    return max(eligible, key=_selection_key) if eligible else None


def _available_profiles(
    rows: pd.DataFrame,
    config: ActionExperimentConfig,
) -> tuple[str, ...]:
    if config.profile_mode == "AWFI_V2_ONLY":
        return ("AWFI_V2_CONTROL",)
    profiles = ["AWFI_V2_CONTROL", *BLOCK_WEIGHT_PROFILES]
    if {
        "quality_score",
        "investment_score",
        "safety_score",
    }.issubset(rows.columns):
        profiles.extend(FUNDAMENTAL_PROFILE_WEIGHTS)
    return tuple(profiles)


def _screen_candidates(
    rows: pd.DataFrame,
    config: ActionExperimentConfig,
) -> tuple[ActionCandidate, ...]:
    profiles = _available_profiles(rows, config)
    return tuple(
        ActionCandidate(profile, *FIXED_SCREEN_THRESHOLDS)
        for profile in profiles
    )


def _threshold_candidates(
    profile_id: str,
) -> tuple[ActionCandidate, ...]:
    result = []
    for enter in (25.0, 50.0, 75.0):
        for increase in (25.0, 50.0, 75.0):
            for decrease in (25.0, 50.0, 75.0):
                for exit_threshold in (50.0, 75.0, 100.0):
                    if exit_threshold <= decrease:
                        continue
                    result.append(
                        ActionCandidate(
                            profile_id,
                            enter,
                            increase,
                            decrease,
                            exit_threshold,
                        )
                    )
    return tuple(result)


def select_candidate(
    training: pd.DataFrame,
    config: ActionExperimentConfig,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    periods = sorted(training["report_period"].unique())
    required = config.minimum_inner_validation_quarters * 2
    if len(periods) < required:
        return None, []
    tuning_periods = periods[-config.minimum_inner_validation_quarters :]
    first_tuning = training[
        training["report_period"] == tuning_periods[0]
    ]
    if first_tuning.empty:
        return None, []
    first_tuning_entry = int(first_tuning["entry_index"].min())
    screening = training[
        (training["report_period"] < tuning_periods[0])
        & (
            training["exit_index"]
            < first_tuning_entry - config.embargo_sessions
        )
    ]
    tuning = training[training["report_period"].isin(tuning_periods)]
    screen_trials = []
    for order, candidate in enumerate(
        _screen_candidates(screening, config)
    ):
        screen_trials.append(
            {
                "stage": "SCREENING",
                "candidate_order": order,
                **evaluate_candidate(screening, candidate, config),
            }
        )
    selected_profile = _choose_candidate(screen_trials)
    if selected_profile is None:
        return None, screen_trials
    tuning_trials = []
    offset = len(screen_trials)
    for order, candidate in enumerate(
        _threshold_candidates(selected_profile["profile_id"])
    ):
        tuning_trials.append(
            {
                "stage": "TUNING",
                "candidate_order": offset + order,
                **evaluate_candidate(tuning, candidate, config),
            }
        )
    selected = _choose_candidate(tuning_trials)
    return selected, [*screen_trials, *tuning_trials]


def _control_candidate(horizon: int) -> ActionCandidate:
    threshold = HORIZON_THRESHOLDS[horizon]
    return ActionCandidate(
        "AWFI_V2_CONTROL",
        threshold,
        threshold,
        25.0,
        max(50.0, threshold),
    )


def evaluate_walk_forward(
    observations: pd.DataFrame,
    *,
    horizon: int,
    config: ActionExperimentConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = observations[
        (observations["horizon"] == horizon)
        & observations["state_known"]
        & (observations["label_status"] == "READY")
    ].copy()
    outer_periods = [
        period
        for period in sorted(rows["report_period"].unique())
        if period >= config.first_test_period
    ]
    predictions = []
    selections = []
    candidate_trials = []
    for test_period in outer_periods:
        test = rows[rows["report_period"] == test_period]
        if test.empty:
            continue
        test_entry = int(test["entry_index"].min())
        training = rows[
            (rows["report_period"] < test_period)
            & (
                rows["exit_index"]
                < test_entry - config.embargo_sessions
            )
        ]
        selected, trials = select_candidate(training, config)
        for trial in trials:
            candidate_trials.append(
                {
                    "report_period": test_period,
                    "selected": (
                        selected is not None
                        and trial["candidate_order"]
                        == selected["candidate_order"]
                    ),
                    **trial,
                }
            )
        if selected is None:
            selections.append(
                {
                    "report_period": test_period,
                    "status": "NO_SELECTABLE_CANDIDATE",
                }
            )
            continue
        candidate = ActionCandidate(
            profile_id=selected["profile_id"],
            enter_threshold=float(selected["enter_threshold"]),
            increase_threshold=float(selected["increase_threshold"]),
            decrease_threshold=float(selected["decrease_threshold"]),
            exit_threshold=float(selected["exit_threshold"]),
        )
        scored = add_action_payoffs(
            classify_actions(test, candidate),
            transaction_cost_bps=config.transaction_cost_bps,
        )
        scored["selected_profile_id"] = candidate.profile_id
        predictions.append(scored)
        selections.append(
            {
                "report_period": test_period,
                "status": "SELECTED",
                **selected,
            }
        )
    return (
        pd.concat(predictions, ignore_index=True)
        if predictions
        else pd.DataFrame(),
        pd.DataFrame(selections),
        pd.DataFrame(candidate_trials),
    )


def _holm_adjust(
    p_values: dict[str, float],
) -> dict[str, float]:
    finite = sorted(
        (
            (action, value)
            for action, value in p_values.items()
            if math.isfinite(value)
        ),
        key=lambda item: item[1],
    )
    adjusted: dict[str, float] = {
        action: math.nan for action in p_values
    }
    running = 0.0
    count = len(finite)
    for index, (action, value) in enumerate(finite):
        running = max(running, min(1.0, (count - index) * value))
        adjusted[action] = running
    return adjusted


def _moving_block_lower(
    values: pd.Series,
    *,
    block_length: int,
    random_seed: int,
    iterations: int = 5000,
) -> float:
    clean = values.dropna().to_numpy(dtype=float)
    if block_length < 1 or len(clean) < block_length:
        return math.nan
    generator = np.random.default_rng(random_seed)
    means = []
    for _ in range(iterations):
        sampled = []
        while len(sampled) < len(clean):
            start = int(generator.integers(0, len(clean)))
            sampled.extend(
                clean[(start + offset) % len(clean)]
                for offset in range(block_length)
            )
        means.append(float(np.mean(sampled[: len(clean)])))
    return float(np.quantile(means, 0.025))


def fundamental_factor_diagnostics(
    observations: pd.DataFrame,
    config: ActionExperimentConfig,
) -> list[dict[str, Any]]:
    factors = (
        "quality_score",
        "investment_score",
        "safety_score",
        "fundamental_balanced_score",
    )
    results = []
    lag_by_horizon = {126: 1, 252: 3, 378: 5, 504: 7}
    for horizon in config.horizons:
        horizon_rows = observations[
            (observations["horizon"] == horizon)
            & (observations["label_status"] == "READY")
        ]
        horizon_results = []
        for factor in factors:
            rank_values = []
            observation_count = 0
            for _, quarter in horizon_rows.dropna(
                subset=[factor, "security_return"]
            ).groupby("report_period"):
                if (
                    len(quarter) < 5
                    or quarter[factor].nunique() < 2
                    or quarter["security_return"].nunique() < 2
                ):
                    continue
                value = quarter[factor].corr(
                    quarter["security_return"],
                    method="spearman",
                )
                if pd.notna(value):
                    rank_values.append(float(value))
                    observation_count += len(quarter)
            values = np.asarray(rank_values, dtype=float)
            if len(values) >= 5 and float(values.std(ddof=1)) > 0:
                model = sm.OLS(
                    values,
                    np.ones((len(values), 1), dtype=float),
                ).fit(
                    cov_type="HAC",
                    cov_kwds={
                        "maxlags": min(
                            lag_by_horizon[horizon],
                            len(values) - 1,
                        ),
                        "use_correction": True,
                    },
                )
                t_stat = float(model.tvalues[0])
                p_value = float(
                    stats.t.sf(t_stat, df=len(values) - 1)
                )
            else:
                t_stat = math.nan
                p_value = math.nan
            horizon_results.append(
                {
                    "horizon": horizon,
                    "factor_id": factor,
                    "quarter_count": int(len(values)),
                    "observation_count": int(observation_count),
                    "coverage": (
                        float(
                            horizon_rows[factor].notna().mean()
                        )
                        if len(horizon_rows)
                        else 0.0
                    ),
                    "mean_rank_ic": (
                        float(values.mean()) if len(values) else math.nan
                    ),
                    "median_rank_ic": (
                        float(np.median(values))
                        if len(values)
                        else math.nan
                    ),
                    "positive_rank_ic_fraction": (
                        float((values > 0).mean())
                        if len(values)
                        else math.nan
                    ),
                    "hac_t_stat": t_stat,
                    "p_value": p_value,
                }
            )
        adjusted = _holm_adjust(
            {
                item["factor_id"]: item["p_value"]
                for item in horizon_results
            }
        )
        for item in horizon_results:
            holm = adjusted[item["factor_id"]]
            results.append(
                {
                    **item,
                    "holm_p_value": holm,
                    "passes_t3": (
                        math.isfinite(item["hac_t_stat"])
                        and item["hac_t_stat"] > 3.0
                    ),
                    "passes_holm": (
                        math.isfinite(holm) and holm < 0.05
                    ),
                }
            )
    return results


def action_significance(
    predictions: pd.DataFrame,
) -> list[dict[str, Any]]:
    if predictions.empty or not {
        "action",
        "report_period",
        "net_benchmark_payoff",
        "success",
    }.issubset(predictions.columns):
        return [
            {
                "action": action,
                "quarters": 0,
                "mean_success_rate": math.nan,
                "mean_payoff": math.nan,
                "t_stat": math.nan,
                "p_value": math.nan,
                "holm_p_value": math.nan,
                "passes_t3": False,
                "passes_holm": False,
                "passes_robustness": False,
                "bootstrap_lower_success_edge": math.nan,
                "inference": "HAC_3_LAGS_T_REFERENCE_DESCRIPTIVE",
            }
            for action in PORTFOLIO_ACTIONS
        ]
    p_values = {}
    raw = {}
    for action in PORTFOLIO_ACTIONS:
        action_rows = predictions.loc[
            predictions["action"] == action,
            ["report_period", "net_benchmark_payoff", "success"],
        ].dropna()
        payoffs = (
            action_rows.groupby("report_period")["net_benchmark_payoff"]
            .mean()
        )
        success_rates = (
            action_rows.groupby("report_period")["success"]
            .mean()
            .astype(float)
        )
        edges = success_rates - 0.50
        if len(edges) >= 5 and float(edges.std(ddof=1)) > 0:
            model = sm.OLS(
                edges.to_numpy(dtype=float),
                np.ones((len(edges), 1), dtype=float),
            ).fit(
                cov_type="HAC",
                cov_kwds={
                    "maxlags": min(3, len(edges) - 1),
                    "use_correction": True,
                },
            )
            t_stat = float(model.tvalues[0])
            p_value = float(stats.t.sf(t_stat, df=len(edges) - 1))
        else:
            t_stat = math.nan
            p_value = math.nan
        bootstrap_lower = _moving_block_lower(
            edges,
            block_length=min(4, len(edges)),
            random_seed=17 + PORTFOLIO_ACTIONS.index(action),
        )
        p_values[action] = p_value
        raw[action] = {
            "action": action,
            "quarters": int(len(success_rates)),
            "mean_success_rate": (
                float(success_rates.mean())
                if len(success_rates)
                else math.nan
            ),
            "mean_payoff": (
                float(payoffs.mean()) if len(payoffs) else math.nan
            ),
            "t_stat": t_stat,
            "p_value": p_value,
            "inference": "HAC_3_LAGS_T_REFERENCE_DESCRIPTIVE",
            "bootstrap_lower_success_edge": bootstrap_lower,
        }
    adjusted = _holm_adjust(p_values)
    return [
        {
            **raw[action],
            "holm_p_value": adjusted[action],
            "passes_t3": (
                math.isfinite(raw[action]["t_stat"])
                and raw[action]["t_stat"] > 3.0
            ),
            "passes_holm": (
                math.isfinite(adjusted[action])
                and adjusted[action] < 0.05
            ),
            "passes_robustness": (
                math.isfinite(
                    raw[action]["bootstrap_lower_success_edge"]
                )
                and raw[action]["bootstrap_lower_success_edge"] > 0
            ),
        }
        for action in PORTFOLIO_ACTIONS
    ]


def evaluate_current_awfi_policy(
    observations: pd.DataFrame,
    *,
    horizon: int,
    config: ActionExperimentConfig,
) -> dict[str, Any]:
    rows = observations[
        (observations["horizon"] == horizon)
        & observations["state_known"]
        & (observations["label_status"] == "READY")
        & (observations["report_period"] >= config.first_test_period)
    ].copy()
    threshold = HORIZON_THRESHOLDS[horizon]
    rows["score"] = rows["awfi_v2_score"]
    rows["action"] = "SKIP"
    held = rows["held_before_signal"]
    rows.loc[held, "action"] = "HOLD"
    rows.loc[
        ~held & (rows["score"] >= threshold),
        "action",
    ] = "ENTER"
    rows.loc[
        held & (rows["score"] >= threshold),
        "action",
    ] = "INCREASE"
    rows.loc[
        held & (rows["score"] <= -threshold),
        "action",
    ] = "EXIT"
    rows = add_action_payoffs(
        rows,
        transaction_cost_bps=config.transaction_cost_bps,
    )
    return {
        "threshold": threshold,
        "actions": {
            action: _action_summary(rows, action)
            for action in PORTFOLIO_ACTIONS
        },
        "significance": action_significance(rows),
    }


def _profile_metrics(
    observations: pd.DataFrame,
    config: ActionExperimentConfig,
) -> list[dict[str, Any]]:
    rows = []
    profiles = _available_profiles(observations, config)
    for horizon in config.horizons:
        horizon_rows = observations[
            (observations["horizon"] == horizon)
            & observations["state_known"]
            & (observations["label_status"] == "READY")
        ]
        for profile in profiles:
            base = _control_candidate(horizon)
            candidate = ActionCandidate(
                profile,
                base.enter_threshold,
                base.increase_threshold,
                base.decrease_threshold,
                base.exit_threshold,
            )
            result = evaluate_candidate(horizon_rows, candidate, config)
            for action, metrics in result["actions"].items():
                rows.append(
                    {
                        "horizon": horizon,
                        "profile_id": profile,
                        "action": action,
                        **metrics,
                    }
                )
    return rows


def _run_id(
    parent_run_id: str,
    source_fingerprint: str,
    config: ActionExperimentConfig,
    profiles: tuple[str, ...],
    implementation_fingerprint: str,
) -> str:
    payload = _json(
        {
            "protocol_version": ACTION_PROTOCOL_VERSION,
            "parent_run_id": parent_run_id,
            "source_fingerprint": source_fingerprint,
            "implementation_fingerprint": implementation_fingerprint,
            "config": config.as_dict(),
            "profiles": profiles,
            "thresholds": {
                "enter": [25, 50, 75],
                "increase": [25, 50, 75],
                "decrease": [25, 50, 75],
                "exit": [50, 75, 100],
            },
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def run_action_experiment(
    *,
    parent_db: Path = DEFAULT_PARENT_DB,
    output_db: Path = DEFAULT_OUTPUT_DB,
    parent_run_id: str | None = None,
    fundamentals_db: Path | None = None,
    config: ActionExperimentConfig | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    config = config or ActionExperimentConfig()
    parent = duckdb.connect(str(parent_db), read_only=True)
    output = connect_action_database(output_db)
    try:
        resolved_parent_run, parent_version = _parent_run(
            parent,
            parent_run_id,
        )
        source_fingerprint = _source_fingerprint(
            parent,
            resolved_parent_run,
        )
        observations = load_action_observations(
            parent,
            resolved_parent_run,
            config,
        )
        fundamental_features = pd.DataFrame()
        if fundamentals_db is not None:
            from .fundamentals import build_fundamental_features

            fundamental_features = build_fundamental_features(
                parent_db=parent_db,
                screening_db=fundamentals_db,
                parent_run_id=resolved_parent_run,
            )
            observations = observations.merge(
                fundamental_features[
                    [
                        "report_period",
                        "cusip",
                        "feature_date",
                        "quality_score",
                        "investment_score",
                        "safety_score",
                        "fundamental_balanced_score",
                    ]
                ],
                on=["report_period", "cusip", "feature_date"],
                how="left",
            )
            feature_hash = hashlib.sha256(
                fundamental_features.sort_values(
                    ["report_period", "cusip"]
                ).to_json(
                    orient="records",
                    date_format="iso",
                    default_handler=str,
                ).encode("utf-8")
            ).hexdigest()
            source_fingerprint = hashlib.sha256(
                f"{source_fingerprint}:{feature_hash}".encode("utf-8")
            ).hexdigest()
        profiles = _available_profiles(observations, config)
        implementation_digest = hashlib.sha256(Path(__file__).read_bytes())
        if fundamentals_db is not None:
            implementation_digest.update(
                (Path(__file__).with_name("fundamentals.py")).read_bytes()
            )
        implementation_fingerprint = implementation_digest.hexdigest()
        run_id = _run_id(
            resolved_parent_run,
            source_fingerprint,
            config,
            profiles,
            implementation_fingerprint,
        )
        existing = output.execute(
            """
            SELECT status, summary_json
            FROM action_experiment_runs
            WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
        if existing and existing[0] == "COMPLETE" and not replace:
            return json.loads(str(existing[1]))
        evaluations = {}
        all_predictions = []
        all_selections = []
        all_trials = []
        all_metrics = []
        significance = []
        for horizon in config.horizons:
            predictions, selections, trials = evaluate_walk_forward(
                observations,
                horizon=horizon,
                config=config,
            )
            evaluations[str(horizon)] = {
                "outer_quarters": int(
                    observations.loc[
                        (observations["horizon"] == horizon)
                        & (
                            observations["report_period"]
                            >= config.first_test_period
                        ),
                        "report_period",
                    ].nunique()
                ),
                "selected_outer_quarters": int(
                    (
                        selections.get("status", pd.Series(dtype=str))
                        == "SELECTED"
                    ).sum()
                ),
                "prediction_count": int(
                    (
                        predictions.get(
                            "action",
                            pd.Series(dtype=str),
                        ).isin(PORTFOLIO_ACTIONS)
                    ).sum()
                ),
                "actions": {
                    action: _action_summary(predictions, action)
                    for action in PORTFOLIO_ACTIONS
                },
            }
            if not predictions.empty:
                predictions["horizon"] = horizon
                all_predictions.append(predictions)
                for action, metrics in evaluations[str(horizon)][
                    "actions"
                ].items():
                    for metric, value in metrics.items():
                        if metric in {
                            "action",
                        } or not isinstance(value, (int, float)):
                            continue
                        all_metrics.append(
                            (run_id, horizon, action, metric, value)
                        )
            if not selections.empty:
                selections["horizon"] = horizon
                all_selections.append(selections)
            if not trials.empty:
                trials["horizon"] = horizon
                all_trials.append(trials)
            if horizon == 252:
                significance = action_significance(predictions)

        profile_metrics = _profile_metrics(observations, config)
        current_awfi_policy = {
            str(horizon): evaluate_current_awfi_policy(
                observations,
                horizon=horizon,
                config=config,
            )
            for horizon in config.horizons
        }
        factor_diagnostics = (
            fundamental_factor_diagnostics(observations, config)
            if not fundamental_features.empty
            else []
        )
        summary = {
            "run_id": run_id,
            "protocol_version": ACTION_PROTOCOL_VERSION,
            "parent_run_id": resolved_parent_run,
            "parent_awfi_version": parent_version,
            "source_fingerprint": source_fingerprint,
            "implementation_fingerprint": implementation_fingerprint,
            "profiles": list(profiles),
            "research_blockers": (
                [
                    "CURRENT_TICKER_TO_CIK_MAPPING_IS_NOT_POINT_IN_TIME",
                ]
                if not fundamental_features.empty
                else []
            ),
            "config": config.as_dict(),
            "observation_count": int(len(observations)),
            "known_state_count": int(observations["state_known"].sum()),
            "held_state_count": int(
                (
                    observations["state_known"]
                    & observations["held_before_signal"]
                ).sum()
            ),
            "flat_state_count": int(
                (
                    observations["state_known"]
                    & ~observations["held_before_signal"]
                ).sum()
            ),
            "evaluations": evaluations,
            "primary_12m_multiple_testing": significance,
            "fundamental_factor_diagnostics": factor_diagnostics,
            "current_awfi_fixed_policy": current_awfi_policy,
            "promotion_status": (
                "PASSED"
                if significance
                and fundamental_features.empty
                and all(
                    item["passes_t3"]
                    and item["passes_holm"]
                    and item["passes_robustness"]
                    for item in significance
                )
                else "NOT_PROMOTABLE"
            ),
        }

        output.execute("BEGIN TRANSACTION")
        for table in (
            "action_selections",
            "action_candidate_trials",
            "action_predictions",
            "action_metrics",
            "action_profile_metrics",
            "action_fundamental_features",
            "action_multiple_testing",
            "action_factor_diagnostics",
            "action_experiment_runs",
        ):
            output.execute(
                f"DELETE FROM {table} WHERE run_id = ?",
                [run_id],
            )
        output.execute(
            """
            INSERT INTO action_experiment_runs
            VALUES (?, ?, ?, ?, now(), now(), 'COMPLETE', ?, ?, ?, NULL)
            """,
            [
                run_id,
                ACTION_PROTOCOL_VERSION,
                resolved_parent_run,
                parent_version,
                _json(config.as_dict()),
                source_fingerprint,
                _json(summary),
            ],
        )
        if factor_diagnostics:
            output.executemany(
                """
                INSERT INTO action_factor_diagnostics
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item["horizon"],
                        item["factor_id"],
                        item["quarter_count"],
                        item["observation_count"],
                        item["coverage"],
                        item["mean_rank_ic"],
                        item["median_rank_ic"],
                        item["positive_rank_ic_fraction"],
                        item["hac_t_stat"],
                        item["p_value"],
                        item["holm_p_value"],
                        item["passes_t3"],
                        item["passes_holm"],
                    )
                    for item in factor_diagnostics
                ],
            )
        if not fundamental_features.empty:
            output.executemany(
                """
                INSERT INTO action_fundamental_features
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        row.report_period,
                        row.cusip,
                        row.ticker,
                        row.issuer_cik,
                        row.feature_date,
                        _nullable(
                            getattr(
                                row,
                                "fundamental_report_period",
                                None,
                            )
                        ),
                        _nullable(getattr(row, "quality_score", None)),
                        _nullable(getattr(row, "investment_score", None)),
                        _nullable(getattr(row, "safety_score", None)),
                        _nullable(
                            getattr(
                                row,
                                "fundamental_balanced_score",
                                None,
                            )
                        ),
                        _json(row._asdict()),
                    )
                    for row in fundamental_features.itertuples(index=False)
                ],
            )
        if all_selections:
            selections = pd.concat(all_selections, ignore_index=True)
            output.executemany(
                """
                INSERT INTO action_selections
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        int(row.horizon),
                        row.report_period,
                        row.status,
                        _json(row._asdict()),
                    )
                    for row in selections.itertuples(index=False)
                ],
            )
        if all_trials:
            trials = pd.concat(all_trials, ignore_index=True)
            output.executemany(
                """
                INSERT INTO action_candidate_trials
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        int(row.horizon),
                        row.report_period,
                        row.stage,
                        int(row.candidate_order),
                        bool(row.selected),
                        _json(row._asdict()),
                    )
                    for row in trials.itertuples(index=False)
                ],
            )
        if all_predictions:
            predictions = pd.concat(all_predictions, ignore_index=True)
            predictions = predictions[
                predictions["action"].isin(PORTFOLIO_ACTIONS)
                & predictions["net_benchmark_payoff"].notna()
            ]
            output.executemany(
                """
                INSERT INTO action_predictions
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        int(row.horizon),
                        row.report_period,
                        row.cusip,
                        row.ticker,
                        row.selected_profile_id,
                        float(row.score),
                        bool(row.held_before_signal),
                        row.action,
                        float(row.security_return),
                        float(row.excess_return),
                        float(row.benchmark_payoff),
                        float(row.cash_payoff),
                        float(row.net_benchmark_payoff),
                        bool(row.success),
                        bool(row.split_affected),
                    )
                    for row in predictions.itertuples(index=False)
                ],
            )
        if all_metrics:
            output.executemany(
                "INSERT INTO action_metrics VALUES (?, ?, ?, ?, ?)",
                all_metrics,
            )
        output.executemany(
            """
            INSERT INTO action_profile_metrics
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    item["horizon"],
                    item["profile_id"],
                    item["action"],
                    _json(item),
                )
                for item in profile_metrics
            ],
        )
        if significance:
            output.executemany(
                """
                INSERT INTO action_multiple_testing (
                    run_id, horizon, action, mean_payoff, t_stat,
                    p_value, holm_p_value, passes_t3, passes_holm,
                    mean_success_rate, bootstrap_lower_success_edge
                ) VALUES (?, 252, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item["action"],
                        item["mean_payoff"],
                        item["t_stat"],
                        item["p_value"],
                        item["holm_p_value"],
                        item["passes_t3"],
                        item["passes_holm"],
                        item["mean_success_rate"],
                        item["bootstrap_lower_success_edge"],
                    )
                    for item in significance
                ],
            )
        output.execute("COMMIT")
        return summary
    except Exception:
        try:
            output.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        parent.close()
        output.close()


def load_action_report(
    output_db: Path = DEFAULT_OUTPUT_DB,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    connection = duckdb.connect(str(output_db), read_only=True)
    try:
        if run_id:
            row = connection.execute(
                """
                SELECT summary_json
                FROM action_experiment_runs
                WHERE run_id = ? AND status = 'COMPLETE'
                """,
                [run_id],
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT summary_json
                FROM action_experiment_runs
                WHERE status = 'COMPLETE'
                ORDER BY completed_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise ValueError("No completed action experiment was found")
        return json.loads(str(row[0]))
    finally:
        connection.close()
