from __future__ import annotations

import math

import pandas as pd

from predictive_sentiment.action_experiments import (
    ActionCandidate,
    ActionExperimentConfig,
    _action_summary,
    _holm_adjust,
    action_significance,
    add_action_payoffs,
    classify_actions,
    compose_profile,
    evaluate_candidate,
    evaluate_current_awfi_policy,
)
from predictive_sentiment.fundamentals import (
    _centered_rank,
    _period_snapshots,
    _raw_factor_row,
)


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "report_period": [
                pd.Timestamp("2024-03-31").date(),
                pd.Timestamp("2024-03-31").date(),
                pd.Timestamp("2024-03-31").date(),
                pd.Timestamp("2024-06-30").date(),
                pd.Timestamp("2024-06-30").date(),
                pd.Timestamp("2024-06-30").date(),
            ],
            "cusip": ["A", "B", "C", "D", "E", "F"],
            "awfi_v2_score": [80.0, 60.0, -40.0, -90.0, 0.0, 10.0],
            "held_before_signal": [False, True, True, True, True, False],
            "state_known": [True, True, True, True, True, True],
            "label_status": ["READY"] * 6,
            "security_return": [0.20, 0.10, -0.05, -0.20, 0.03, 0.15],
            "excess_return": [0.15, 0.04, -0.08, -0.25, 0.01, 0.10],
        }
    )


def test_action_policy_distinguishes_all_actions_and_skip():
    candidate = ActionCandidate(
        "AWFI_V2_CONTROL",
        enter_threshold=50,
        increase_threshold=50,
        decrease_threshold=25,
        exit_threshold=75,
    )

    result = classify_actions(_rows(), candidate)

    assert result["action"].tolist() == [
        "ENTER",
        "INCREASE",
        "DECREASE",
        "EXIT",
        "HOLD",
        "SKIP",
    ]


def test_current_awfi_policy_preserves_fixed_three_signal_contract():
    rows = _rows().copy()
    rows["horizon"] = 126

    result = evaluate_current_awfi_policy(
        rows,
        horizon=126,
        config=ActionExperimentConfig(
            first_test_period=pd.Timestamp("2024-01-01").date()
        ),
    )

    assert result["actions"]["ENTER"]["assigned"] == 1
    assert result["actions"]["INCREASE"]["assigned"] == 0
    assert result["actions"]["HOLD"]["assigned"] == 3
    assert result["actions"]["DECREASE"]["assigned"] == 0
    assert result["actions"]["EXIT"]["assigned"] == 1


def test_action_payoffs_use_avoided_return_and_no_hold_cost():
    candidate = ActionCandidate(
        "AWFI_V2_CONTROL",
        enter_threshold=50,
        increase_threshold=50,
        decrease_threshold=25,
        exit_threshold=75,
    )
    result = add_action_payoffs(
        classify_actions(_rows(), candidate),
        transaction_cost_bps=25,
    ).set_index("action")

    assert math.isclose(
        result.loc["ENTER", "net_benchmark_payoff"],
        0.1475,
    )
    assert math.isclose(
        result.loc["DECREASE", "net_benchmark_payoff"],
        0.0775,
    )
    assert math.isclose(
        result.loc["EXIT", "net_benchmark_payoff"],
        0.2475,
    )
    assert math.isclose(
        result.loc["HOLD", "net_benchmark_payoff"],
        0.01,
    )
    assert pd.isna(result.loc["SKIP", "net_benchmark_payoff"])


def test_enter_coverage_retains_skipped_flat_opportunities():
    candidate = ActionCandidate(
        "AWFI_V2_CONTROL",
        enter_threshold=50,
        increase_threshold=50,
        decrease_threshold=25,
        exit_threshold=75,
    )
    result = add_action_payoffs(
        classify_actions(_rows(), candidate),
        transaction_cost_bps=25,
    )

    summary = _action_summary(result, "ENTER")

    assert summary["opportunities"] == 2
    assert summary["assigned"] == 1
    assert summary["coverage"] == 0.5


def test_candidate_objective_weights_action_types_not_row_count():
    rows = pd.concat([_rows()] * 4, ignore_index=True)
    rows["report_period"] = [
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-03-31").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-06-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-09-30").date(),
        pd.Timestamp("2023-12-31").date(),
        pd.Timestamp("2023-12-31").date(),
        pd.Timestamp("2023-12-31").date(),
        pd.Timestamp("2023-12-31").date(),
        pd.Timestamp("2023-12-31").date(),
        pd.Timestamp("2023-12-31").date(),
    ]
    candidate = ActionCandidate(
        "AWFI_V2_CONTROL",
        enter_threshold=50,
        increase_threshold=50,
        decrease_threshold=25,
        exit_threshold=75,
    )

    result = evaluate_candidate(
        rows,
        candidate,
        ActionExperimentConfig(
            minimum_inner_actions=1,
            minimum_action_types=1,
        ),
    )

    represented = [
        item["success_rate"]
        for item in result["actions"].values()
        if item["assigned"] >= 3 and item["quarters"] >= 2
    ]
    assert math.isclose(
        result["macro_action_hit_rate"],
        sum(represented) / len(represented),
    )


def test_holm_adjustment_is_monotonic_in_sorted_order():
    adjusted = _holm_adjust(
        {
            "ENTER": 0.001,
            "INCREASE": 0.01,
            "HOLD": 0.02,
            "DECREASE": 0.20,
            "EXIT": math.nan,
        }
    )

    assert adjusted["ENTER"] == 0.004
    assert adjusted["INCREASE"] == 0.03
    assert adjusted["HOLD"] == 0.04
    assert adjusted["DECREASE"] == 0.20
    assert math.isnan(adjusted["EXIT"])


def test_empty_action_significance_returns_unavailable_rows():
    result = action_significance(pd.DataFrame())

    assert [item["action"] for item in result] == [
        "ENTER",
        "INCREASE",
        "HOLD",
        "DECREASE",
        "EXIT",
    ]
    assert all(item["quarters"] == 0 for item in result)
    assert not any(item["passes_t3"] for item in result)


def test_fundamental_profile_requires_all_preregistered_blocks():
    rows = pd.DataFrame(
        {
            "awfi_v2_score": [40.0, 40.0],
            "quality_score": [60.0, 60.0],
            "investment_score": [20.0, math.nan],
            "safety_score": [80.0, 80.0],
        }
    )

    score = compose_profile(rows, "FUNDAMENTAL_BALANCED_25")

    assert math.isclose(score.iloc[0], 44.25)
    assert math.isnan(score.iloc[1])


def test_point_in_time_fundamentals_exclude_later_amendment():
    events = pd.DataFrame(
        {
            "available_at": pd.to_datetime(
                [
                    "2024-02-01 12:00:00",
                    "2024-02-01 12:00:00",
                    "2024-05-20 12:00:00",
                ]
            ),
            "report_period": [
                pd.Timestamp("2023-12-31").date(),
                pd.Timestamp("2022-12-31").date(),
                pd.Timestamp("2023-12-31").date(),
            ],
            "metric": ["assets", "assets", "assets"],
            "fact_value": [100.0, 80.0, 120.0],
            "sic": [3571, 3571, 3571],
            "accession_number": ["original", "prior", "amendment"],
            "priority": [0, 0, 0],
            "source_row_number": [1, 1, 1],
        }
    )

    early = _period_snapshots(
        events,
        pd.Timestamp("2024-04-01").date(),
    )
    late = _period_snapshots(
        events,
        pd.Timestamp("2024-06-01").date(),
    )

    assert early[0]["assets"] == 100.0
    assert late[0]["assets"] == 120.0


def test_raw_fundamental_factors_use_prior_annual_assets():
    result = _raw_factor_row(
        [
            {
                "report_period": pd.Timestamp("2023-12-31").date(),
                "available_at": pd.Timestamp("2024-02-01"),
                "accessions": ["current"],
                "sic": 3571,
                "assets": 120.0,
                "gross_profit": 36.0,
                "net_income": 12.0,
                "operating_cash_flow": 18.0,
                "capital_expenditure": 6.0,
                "cash": 24.0,
                "equity": 60.0,
                "long_term_debt_noncurrent": 30.0,
                "current_assets": 48.0,
                "current_liabilities": 24.0,
                "operating_income": 15.0,
                "interest_expense": 3.0,
                "revenue": 100.0,
            },
            {
                "report_period": pd.Timestamp("2022-12-31").date(),
                "available_at": pd.Timestamp("2023-02-01"),
                "accessions": ["prior"],
                "sic": 3571,
                "assets": 100.0,
            },
        ]
    )

    assert math.isclose(result["asset_growth"], 0.20)
    assert math.isclose(result["gross_profit_assets"], 0.30)
    assert math.isclose(result["free_cash_flow_assets"], 0.10)
    assert math.isclose(result["debt_assets"], 0.25)


def test_fundamental_centered_rank_has_zero_mean_and_full_range():
    ranked = _centered_rank(pd.Series([10.0, 20.0, 30.0, 40.0, 50.0]))

    assert ranked.tolist() == [-100.0, -50.0, 0.0, 50.0, 100.0]
    assert math.isclose(ranked.mean(), 0.0)


def test_asset_growth_rejects_nonannual_period_gap():
    result = _raw_factor_row(
        [
            {
                "report_period": pd.Timestamp("2023-12-31").date(),
                "available_at": pd.Timestamp("2024-02-01"),
                "accessions": ["current"],
                "sic": 3571,
                "assets": 120.0,
            },
            {
                "report_period": pd.Timestamp("2021-12-31").date(),
                "available_at": pd.Timestamp("2022-02-01"),
                "accessions": ["prior"],
                "sic": 3571,
                "assets": 100.0,
            },
        ],
        feature_date=pd.Timestamp("2024-03-01").date(),
    )

    assert math.isnan(result["asset_growth"])
