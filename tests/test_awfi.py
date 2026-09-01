import pytest

from predictive_sentiment.awfi import (
    HORIZON_THRESHOLDS,
    HORIZON_WEIGHTS,
    awfi_signal,
    compose_awfi_score,
)


HORIZONS = (126, 252, 378, 504)


def test_horizon_weight_profiles_are_distinct_and_normalized():
    assert tuple(HORIZON_WEIGHTS) == HORIZONS
    profiles = [HORIZON_WEIGHTS[horizon] for horizon in HORIZONS]

    assert len(set(profiles)) == len(HORIZONS)
    assert all(sum(profile) == pytest.approx(1.0) for profile in profiles)
    assert all(
        0.0 <= weight <= 1.0
        for profile in profiles
        for weight in profile
    )


def test_horizon_weights_match_evidence_backed_production_profiles():
    assert HORIZON_WEIGHTS == {
        126: (0.34, 0.34, 0.17, 0.15),
        252: (0.425, 0.2125, 0.2125, 0.15),
        378: (0.50, 0.25, 0.25, 0.0),
        504: (1.0, 0.0, 0.0, 0.0),
    }


def test_non_uniform_components_produce_four_distinct_horizon_scores():
    components = {
        "alpha_score": 80.0,
        "action_score": -60.0,
        "portfolio_score": 20.0,
        "technical_score": 40.0,
    }

    scores = [
        compose_awfi_score(horizon=horizon, **components)
        for horizon in HORIZONS
    ]

    assert scores == pytest.approx([16.2, 31.5, 30.0, 80.0])
    assert len(set(scores)) == len(HORIZONS)


def test_scores_remain_bounded_to_awfi_range():
    for horizon in HORIZONS:
        assert compose_awfi_score(
            horizon=horizon,
            alpha_score=1_000.0,
            action_score=1_000.0,
            portfolio_score=1_000.0,
            technical_score=1_000.0,
        ) == 100.0
        assert compose_awfi_score(
            horizon=horizon,
            alpha_score=-1_000.0,
            action_score=-1_000.0,
            portfolio_score=-1_000.0,
            technical_score=-1_000.0,
        ) == -100.0


def test_horizons_use_their_tested_production_thresholds():
    assert tuple(HORIZON_THRESHOLDS) == HORIZONS
    assert HORIZON_THRESHOLDS == {
        126: 75.0,
        252: 75.0,
        378: 75.0,
        504: 25.0,
    }

    for horizon in (126, 252, 378):
        assert awfi_signal(horizon, 50.0) == "HOLD"
        assert awfi_signal(horizon, 75.0) == "BUY"
        assert awfi_signal(horizon, -75.0) == "SELL"

    assert awfi_signal(504, 24.9) == "HOLD"
    assert awfi_signal(504, 25.0) == "BUY"
    assert awfi_signal(504, -25.0) == "SELL"


def test_24_month_score_is_not_a_threshold_only_copy_of_18_month_score():
    components = {
        "alpha_score": 40.0,
        "action_score": 90.0,
        "portfolio_score": -60.0,
        "technical_score": 20.0,
    }

    score_18m = compose_awfi_score(horizon=378, **components)
    score_24m = compose_awfi_score(horizon=504, **components)

    assert score_18m == pytest.approx(27.5)
    assert score_24m == pytest.approx(40.0)
    assert score_18m != score_24m
