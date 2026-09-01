from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date

import duckdb
import pandas as pd

from predictive_sentiment.config import (
    EXPERIMENT_SENTIMENT_ONLY,
    EXPERIMENT_DECOMPOSED_SWEEP,
    EXPERIMENT_TECHNICAL_COMBINED,
    ResearchConfig,
    candidate_specs,
)
from predictive_sentiment.pipeline import (
    _build_labels,
    _centered_rank,
    _point_in_time_market_features,
    _production_training_rows,
)
from predictive_sentiment.research import (
    FilingRow,
    FormulaScore,
    HoldingRow,
    ManagerChange,
    RawComparison,
    build_manager_changes,
    detect_split_actions,
    generate_formula_scores,
    is_direct_common_stock,
    reconstruct_snapshot,
)
from predictive_sentiment.validation import (
    EvaluationResult,
    apply_decision_policy,
    apply_selected_candidate,
    classification_metrics,
    evaluate_trust_gate,
    evaluate_walk_forward,
    wilson_interval,
)


def _filing(
    accession: str,
    filed: date,
    *,
    submission_type: str = "13F-HR",
    amendment_type: str = "",
) -> FilingRow:
    return FilingRow(
        accession_number=accession,
        canonical_cik="0000000001",
        source_cik="0000000001",
        report_period=date(2024, 3, 31),
        filing_date=filed,
        submission_type=submission_type,
        amendment_type=amendment_type,
        manager_name="Manager",
    )


def _holding(accession: str, cusip: str, shares: float) -> HoldingRow:
    return HoldingRow(
        accession_number=accession,
        cusip=cusip,
        value_usd=shares * 10,
        shares=shares,
        shares_type="SH",
        put_call="",
        issuer="TEST COMPANY",
        title="COM",
    )


def test_fixed_asof_excludes_late_restatement_and_resets_additions():
    filings = [
        _filing("base", date(2024, 5, 1)),
        _filing(
            "addition",
            date(2024, 5, 2),
            submission_type="13F-HR/A",
            amendment_type="NEW HOLDINGS",
        ),
        _filing(
            "restatement",
            date(2024, 5, 20),
            submission_type="13F-HR/A",
            amendment_type="RESTATEMENT",
        ),
        _filing(
            "late-addition",
            date(2024, 5, 21),
            submission_type="13F-HR/A",
            amendment_type="NEW HOLDINGS",
        ),
    ]
    holdings = {
        "base": [_holding("base", "A", 10)],
        "addition": [_holding("addition", "B", 5)],
        "restatement": [_holding("restatement", "C", 8)],
        "late-addition": [_holding("late-addition", "D", 2)],
    }

    early = reconstruct_snapshot(
        filings,
        holdings,
        canonical_cik="0000000001",
        manager_name="Manager",
        report_period=date(2024, 3, 31),
        as_of_date=date(2024, 5, 15),
    )
    late = reconstruct_snapshot(
        filings,
        holdings,
        canonical_cik="0000000001",
        manager_name="Manager",
        report_period=date(2024, 3, 31),
        as_of_date=date(2024, 5, 22),
    )

    assert early.effective_accessions == ("base", "addition")
    assert {item.cusip for item in early.positions} == {"A", "B"}
    assert late.effective_accessions == ("restatement", "late-addition")
    assert {item.cusip for item in late.positions} == {"C", "D"}


def test_addition_without_base_is_invalid():
    filing = _filing(
        "addition",
        date(2024, 5, 2),
        submission_type="13F-HR/A",
        amendment_type="NEW HOLDINGS",
    )
    snapshot = reconstruct_snapshot(
        [filing],
        {"addition": [_holding("addition", "A", 1)]},
        canonical_cik="0000000001",
        manager_name="Manager",
        report_period=date(2024, 3, 31),
        as_of_date=date(2024, 5, 15),
    )
    assert snapshot.status == "NO_BASE"
    assert snapshot.positions == ()


def test_direct_stock_classifier_accepts_equity_and_rejects_funds():
    assert is_direct_common_stock(
        issuer="ALPHA INC",
        title="CLASS A COMMON STOCK",
        shares_type="SH",
        put_call="",
    )
    assert is_direct_common_stock(
        issuer="FOREIGN COMPANY",
        title="SPONSORED ADR",
        shares_type="SH",
        put_call="",
    )
    assert not is_direct_common_stock(
        issuer="ACME MUTUAL FUND",
        title="COMMON SHARES",
        shares_type="SH",
        put_call="",
    )
    assert not is_direct_common_stock(
        issuer="ENERGY PARTNERS",
        title="COM UNIT LP",
        shares_type="SH",
        put_call="",
    )


def test_existing_alpha_formula_parity():
    changes = []
    for index, relative in enumerate((2.0, 2.0, -2.0), start=1):
        changes.append(
            ManagerChange(
                canonical_cik=str(index),
                manager_name=str(index),
                report_period=date(2024, 3, 31),
                as_of_date=date(2024, 5, 15),
                cusip="ABC",
                status="INCREASED" if relative > 0 else "DECREASED",
                previous_shares=100,
                current_shares=120 if relative > 0 else 80,
                comparison_current_shares=120 if relative > 0 else 80,
                previous_value=1000,
                current_value=1000,
                previous_weight=2,
                current_weight=2,
                share_change_pct=20 if relative > 0 else -20,
                typical_share_change_pct=10,
                typical_position_weight=2,
                position_significance=1,
                relative_conviction=relative,
                force_routine=False,
                split_factor=None,
            )
        )
    scores = {
        item.formula_id: item for item in generate_formula_scores(changes)
    }
    assert round(scores["alpha_v1_n3"].score, 2) == 33.33
    assert scores["alpha_v1_n3"].published is True
    assert scores["alpha_v1_n5"].published is False


def test_coordinated_split_is_normalized_before_conviction():
    comparisons = [
        RawComparison(
            canonical_cik=str(index),
            manager_name=str(index),
            report_period=date(2024, 3, 31),
            as_of_date=date(2024, 5, 15),
            cusip="ABC",
            previous_shares=100,
            current_shares=ratio * 100,
            previous_value=1000,
            current_value=1000,
            previous_weight=2,
            current_weight=2,
        )
        for index, ratio in enumerate((2.0, 2.02, 1.98, 2.2), start=1)
    ]
    config = ResearchConfig(split_min_support=0.75)
    splits = detect_split_actions(comparisons, config)
    changes = build_manager_changes(comparisons, splits)

    assert splits[(date(2024, 3, 31), "ABC")].factor == 2.0
    outlier = next(item for item in changes if item.canonical_cik == "4")
    assert round(outlier.share_change_pct, 6) == 10.0


def test_exact_session_label_does_not_forward_fill_terminal_price(tmp_path):
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    spy_dates = pd.date_range("2024-01-02", periods=140, freq="B").date
    spy_frame = pd.DataFrame(
        {"date": spy_dates, "symbol": "SPY", "close": range(100, 240)}
    )
    aaa_frame = pd.DataFrame(
        {
            "date": spy_dates[:-20],
            "symbol": "AAA",
            "close": range(50, 170),
        }
    )
    performance = duckdb.connect(str(tmp_path / "performance.duckdb"))
    performance.execute(
        """
        CREATE TABLE price_manifest (
            symbol VARCHAR, status VARCHAR, parquet_path VARCHAR,
            parquet_sha256 VARCHAR
        )
        """
    )
    performance.execute(
        """
        CREATE TABLE cusip_ticker_mapping (
            cusip VARCHAR, ticker VARCHAR, market_symbol VARCHAR,
            source VARCHAR, retrieved_at TIMESTAMP
        )
        """
    )
    for symbol, frame in (("SPY", spy_frame), ("AAA", aaa_frame)):
        path = price_dir / f"{symbol}.parquet"
        frame.to_parquet(path, index=False)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        performance.execute(
            "INSERT INTO price_manifest VALUES (?, 'READY', ?, ?)",
            [symbol, str(path), digest],
        )
    score = FormulaScore(
        report_period=date(2023, 12, 31),
        as_of_date=date(2024, 1, 1),
        cusip="CUSIP",
        formula_id="alpha_v1_n3",
        score=50,
        published=True,
        meaningful_count=3,
        bullish_count=3,
        bearish_count=0,
        breadth_score=100,
        conviction_score=100,
        comparable_manager_count=3,
        current_holder_count=5,
        median_current_weight=2.0,
        split_affected=False,
        reported_value=1000,
    )
    labels, quality = _build_labels(
        [score, replace(score, cusip="MISSING")],
        mapping={"CUSIP": ("AAA", "AAA", "test", "now")},
        performance=performance,
        config=ResearchConfig(horizons=(126,)),
    )
    performance.close()

    by_cusip = {item["cusip"]: item for item in labels}
    assert by_cusip["CUSIP"]["status"] == "NO_EXIT_PRICE"
    assert by_cusip["CUSIP"]["security_return"] is None
    assert by_cusip["MISSING"]["status"] == "NO_MAPPING"
    assert quality["missing_terminal_price_rate_126"] == 1.0


def test_primary_label_tracks_stock_direction_not_spy_excess(tmp_path):
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    dates = pd.date_range("2023-01-02", periods=400, freq="B").date
    spy = pd.DataFrame(
        {
            "date": dates,
            "symbol": "SPY",
            "close": [100 + index * 0.20 for index in range(len(dates))],
        }
    )
    stock = pd.DataFrame(
        {
            "date": dates,
            "symbol": "AAA",
            "close": [100 + index * 0.10 for index in range(len(dates))],
        }
    )
    performance = duckdb.connect(str(tmp_path / "performance.duckdb"))
    performance.execute(
        """
        CREATE TABLE price_manifest (
            symbol VARCHAR, status VARCHAR, parquet_path VARCHAR,
            parquet_sha256 VARCHAR
        )
        """
    )
    for symbol, frame in (("SPY", spy), ("AAA", stock)):
        path = price_dir / f"{symbol}.parquet"
        frame.to_parquet(path, index=False)
        performance.execute(
            "INSERT INTO price_manifest VALUES (?, 'READY', ?, ?)",
            [symbol, str(path), hashlib.sha256(path.read_bytes()).hexdigest()],
        )
    score = FormulaScore(
        report_period=date(2023, 9, 30),
        as_of_date=date(2023, 11, 14),
        cusip="CUSIP",
        formula_id="alpha_v1_n3",
        score=50,
        published=True,
        meaningful_count=3,
        bullish_count=3,
        bearish_count=0,
        breadth_score=100,
        conviction_score=100,
        comparable_manager_count=3,
        current_holder_count=5,
        median_current_weight=2.0,
        split_affected=False,
        reported_value=1000,
    )
    labels, _ = _build_labels(
        [score],
        mapping={"CUSIP": ("AAA", "AAA", "test", "now")},
        performance=performance,
        config=ResearchConfig(horizons=(126,)),
    )
    performance.close()

    assert labels[0]["security_return"] > 0
    assert labels[0]["excess_return"] < 0
    assert labels[0]["label"] == 1
    assert labels[0]["excess_label"] == 0


def test_signal_before_price_history_is_not_shifted_into_the_future(tmp_path):
    price_dir = tmp_path / "prices"
    price_dir.mkdir()
    dates = pd.date_range("2024-01-02", periods=300, freq="B").date
    performance = duckdb.connect(str(tmp_path / "performance.duckdb"))
    performance.execute(
        """
        CREATE TABLE price_manifest (
            symbol VARCHAR, status VARCHAR, parquet_path VARCHAR,
            parquet_sha256 VARCHAR
        )
        """
    )
    for symbol in ("SPY", "AAA"):
        frame = pd.DataFrame(
            {"date": dates, "symbol": symbol, "close": range(100, 400)}
        )
        path = price_dir / f"{symbol}.parquet"
        frame.to_parquet(path, index=False)
        performance.execute(
            "INSERT INTO price_manifest VALUES (?, 'READY', ?, ?)",
            [symbol, str(path), hashlib.sha256(path.read_bytes()).hexdigest()],
        )
    score = FormulaScore(
        report_period=date(2020, 3, 31),
        as_of_date=date(2020, 5, 15),
        cusip="CUSIP",
        formula_id="alpha_v1_n3",
        score=50,
        published=True,
        meaningful_count=3,
        bullish_count=3,
        bearish_count=0,
        breadth_score=100,
        conviction_score=100,
        comparable_manager_count=5,
        current_holder_count=5,
        median_current_weight=2.0,
        split_affected=False,
        reported_value=1000,
    )
    labels, _ = _build_labels(
        [score],
        mapping={"CUSIP": ("AAA", "AAA", "test", "now")},
        performance=performance,
        config=ResearchConfig(horizons=(126,)),
    )
    performance.close()

    assert labels[0]["status"] == "NO_SPY_ENTRY_SESSION"
    assert labels[0]["entry_date"] is None


def test_production_selection_excludes_latest_and_overlapping_labels():
    rows = pd.DataFrame(
        [
            {
                "horizon": 126,
                "report_period": date(2025, 6, 30),
                "exit_index": 900,
            },
            {
                "horizon": 126,
                "report_period": date(2025, 9, 30),
                "exit_index": 998,
            },
            {
                "horizon": 126,
                "report_period": date(2025, 12, 31),
                "exit_index": 1100,
            },
        ]
    )
    training = _production_training_rows(
        rows,
        horizon=126,
        latest_period=date(2025, 12, 31),
        current_entry_index=1000,
        embargo_sessions=5,
    )

    assert training["report_period"].tolist() == [date(2025, 6, 30)]


def test_market_context_uses_only_closes_before_entry():
    dates = pd.date_range("2023-01-02", periods=260, freq="B").date
    series = pd.Series(
        range(100, 360),
        index=pd.Index(dates, name="date"),
        dtype=float,
    )
    entry_date = dates[-1]
    features = _point_in_time_market_features(series, entry_date)

    assert features["feature_date"] == dates[-2]
    assert features["trend_regime"] == "BULLISH"
    assert features["sma_50"] < float(series.iloc[-2])


def test_alpha_direction_with_support_gate_produces_buy_hold_sell():
    rows = pd.DataFrame(
        [
            {
                "formula_id": "alpha_v1_n3",
                "score": 50.0,
                "price_above_52_week_low_pct": 20.0,
                "trend_regime": "BULLISH",
            },
            {
                "formula_id": "alpha_v1_n3",
                "score": 50.0,
                "price_above_52_week_low_pct": 80.0,
                "trend_regime": "BULLISH",
            },
            {
                "formula_id": "alpha_v1_n3",
                "score": -50.0,
                "price_above_52_week_low_pct": 10.0,
                "trend_regime": "BEARISH",
            },
        ]
    )
    decisions = apply_decision_policy(
        rows,
        formula_id="alpha_v1_n3",
        positive_threshold=25.0,
        negative_threshold=25.0,
        context_id="TREND_AND_PRICE_50",
    )

    assert decisions["decision_signal"].tolist() == [
        "BUY",
        "HOLD",
        "SELL",
    ]


def test_asymmetric_threshold_grid_and_decisions():
    sentiment_specs = candidate_specs(EXPERIMENT_SENTIMENT_ONLY)
    technical_specs = candidate_specs(EXPERIMENT_TECHNICAL_COMBINED)
    assert len(sentiment_specs) == 16
    assert len(technical_specs) == 32
    assert len(candidate_specs(EXPERIMENT_DECOMPOSED_SWEEP)) == 240
    assert {
        (item.positive_threshold, item.negative_threshold)
        for item in sentiment_specs
    } == {
        (positive, negative)
        for positive in (25.0, 50.0, 75.0, 100.0)
        for negative in (25.0, 50.0, 75.0, 100.0)
    }

    decisions = apply_decision_policy(
        pd.DataFrame(
            [
                {
                    "formula_id": "alpha_v1_n3",
                    "score": 49.0,
                    "price_above_52_week_low_pct": 10.0,
                    "trend_regime": "BULLISH",
                },
                {
                    "formula_id": "alpha_v1_n3",
                    "score": -76.0,
                    "price_above_52_week_low_pct": 10.0,
                    "trend_regime": "BEARISH",
                },
            ]
        ),
        formula_id="alpha_v1_n3",
        positive_threshold=50.0,
        negative_threshold=75.0,
        context_id="SENTIMENT_ONLY",
    )
    assert decisions["decision_signal"].tolist() == ["HOLD", "SELL"]


def test_cross_sectional_rank_is_zero_centered():
    ranked = _centered_rank(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert ranked.tolist() == [-100.0, -50.0, 0.0, 50.0, 100.0]
    assert ranked.mean() == 0.0


def test_decomposed_action_asymmetry_changes_combined_score():
    rows = pd.DataFrame(
        [
            {
                "alpha_score": 0.0,
                "new_strength": 1.0,
                "increased_strength": 1.0,
                "decreased_strength": 1.0,
                "closed_strength": 1.0,
                "portfolio_weight_score": 0.0,
                "acceleration_score": 0.0,
                "crowding_score": 0.0,
                "persistence_score": 0.0,
                "technical_score": 0.0,
            }
        ]
    )
    base = {
        "formula_id": "decomposed_v1",
        "positive_threshold": 25.0,
        "negative_threshold": 25.0,
        "context_id": "SENTIMENT_ONLY",
        "experiment_group": "DECOMPOSED_SWEEP",
        "score_profile_id": "ACTION_ONLY",
        "alpha_weight": 0.0,
        "action_weight": 1.0,
        "portfolio_weight": 0.0,
        "acceleration_weight": 0.0,
        "crowding_weight": 0.0,
        "persistence_weight": 0.0,
        "technical_weight": 0.0,
    }
    symmetric = apply_selected_candidate(
        rows,
        {**base, "action_profile_id": "SYMMETRIC"},
    )
    asymmetric = apply_selected_candidate(
        rows,
        {**base, "action_profile_id": "BUY_ASYMMETRIC"},
    )

    assert symmetric.iloc[0]["score"] == 0.0
    assert symmetric.iloc[0]["decision_signal"] == "HOLD"
    assert round(asymmetric.iloc[0]["score"], 2) == 55.56
    assert asymmetric.iloc[0]["decision_signal"] == "BUY"


def test_default_horizons_cover_six_through_twenty_four_months():
    assert ResearchConfig().horizons == (126, 252, 378, 504)


def test_metrics_and_current_roster_trust_blocker():
    metrics = classification_metrics(
        pd.Series([1, 1, 0, 0]),
        pd.Series([1, 0, 0, 0]),
    )
    assert metrics["accuracy"] == 0.75
    assert metrics["balanced_accuracy"] == 0.75
    assert wilson_interval(3, 4)[0] < 0.75

    passing = {
        "outer_quarters": 10,
        "sample_size": 300,
        "positive_count": 150,
        "negative_count": 150,
        "coverage": 0.5,
        "accuracy": 0.95,
        "balanced_accuracy": 0.95,
        "accuracy_wilson_lower": 0.90,
        "baseline_accuracy": 0.55,
        "mean_rank_ic": 0.20,
        "rank_ic_ci_lower": 0.10,
        "positive_rank_ic_fraction": 0.80,
        "balanced_accuracy_bootstrap_lower": 0.80,
    }
    evaluations = [
        EvaluationResult(
            horizon=horizon,
            experiment_group="TECHNICAL_COMBINED",
            metrics=passing,
            predictions=pd.DataFrame(),
            selections=pd.DataFrame(),
            candidate_trials=pd.DataFrame(),
            rank_ic_by_quarter=pd.DataFrame(),
        )
        for horizon in (126, 252)
    ]
    status, criteria = evaluate_trust_gate(
        evaluations,
        mapping_coverage=1.0,
        label_coverage=1.0,
        missing_terminal_price_rate=0.0,
        config=ResearchConfig(),
    )
    assert status == "NOT_TRUSTWORTHY"
    blocker = next(
        item for item in criteria if item["criterion"] == "point_in_time_cohort"
    )
    assert blocker["passed"] is False
    mapping_blocker = next(
        item
        for item in criteria
        if item["criterion"] == "historical_security_master"
    )
    assert mapping_blocker["passed"] is False


def test_point_in_time_mode_cannot_be_enabled_by_flag_only():
    try:
        ResearchConfig(cohort_mode="POINT_IN_TIME")
    except ValueError as exc:
        assert "dated roster evidence" in str(exc)
    else:
        raise AssertionError("POINT_IN_TIME must require actual dated evidence")


def test_unselectable_outer_quarter_reduces_end_to_end_coverage():
    periods = [
        date(2020, 3, 31),
        date(2020, 6, 30),
        date(2020, 9, 30),
        date(2020, 12, 31),
    ]
    rows = []
    for period_index, period in enumerate(periods):
        for item_index, (score, label) in enumerate(
            ((50.0, 1), (-50.0, 0))
        ):
            rows.append(
                {
                    "report_period": period,
                    "cusip": f"{period_index}-{item_index}",
                    "formula_id": "alpha_v1_n3",
                    "score": score,
                    "split_affected": False,
                    "horizon": 126,
                    "entry_index": period_index * 100,
                    "exit_index": period_index * 100 + 126,
                    "excess_return": 0.1 if label else -0.1,
                    "label": label,
                }
            )
    result = evaluate_walk_forward(
        pd.DataFrame(rows),
        horizon=126,
        config=ResearchConfig(
            first_test_period=date(2020, 6, 30),
            inner_warmup_quarters=1,
            minimum_inner_validation_quarters=1,
            minimum_inner_predictions=2,
            minimum_inner_class_count=1,
            minimum_inner_coverage=0.1,
        ),
    )

    assert result.metrics["outer_quarters"] == 3
    assert result.metrics["selected_outer_quarters"] == 1
    assert result.metrics["selection_availability"] == 1 / 3
    assert result.metrics["coverage"] == 2 / 6
    assert result.metrics["threshold_coverage"] == 1.0
