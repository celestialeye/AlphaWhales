from __future__ import annotations

from collections.abc import Iterable


# Research v2 corrects the duplicate 18M/24M formula from v1. Each profile is
# taken from the stored production candidate study; v2 keeps the selected 6M,
# 12M, and 18M profiles and uses the strongest tested distinct 24M profile that
# the live scoring contract can compute without unavailable support features.
AWFI_VERSION = "awfi-research-v2"

# Component order:
# (original Alpha, purchase-led action, portfolio conviction, technical support)
HORIZON_WEIGHTS = {
    126: (0.34, 0.34, 0.17, 0.15),
    252: (0.425, 0.2125, 0.2125, 0.15),
    378: (0.50, 0.25, 0.25, 0.0),
    504: (1.0, 0.0, 0.0, 0.0),
}

# These are the thresholds paired with the tested production profiles. The
# 24M Alpha-only profile retained high BUY precision at the broader boundary;
# thresholds remain research classifications rather than execution rules.
HORIZON_THRESHOLDS = {
    126: 75.0,
    252: 75.0,
    378: 75.0,
    504: 25.0,
}


def purchase_led_action_score(
    investor_changes: Iterable[dict],
) -> float:
    coefficients = {
        "NEW": 1.0,
        "INCREASED": 0.75,
        "DECREASED": 0.25,
        "CLOSED": 0.25,
    }
    numerator = 0.0
    denominator = 0.0
    for change in investor_changes:
        status = str(change.get("status", "")).upper()
        relative = change.get("relative_conviction")
        if status not in coefficients or relative is None:
            continue
        if change.get("position_size_gate_applied") or abs(relative) < 0.25:
            continue
        strength = min(2.0, abs(float(relative)))
        coefficient = coefficients[status]
        direction = 1.0 if status in {"NEW", "INCREASED"} else -1.0
        numerator += direction * coefficient * strength
        denominator += coefficient * strength
    return 100.0 * numerator / denominator if denominator > 0 else 0.0


def compose_awfi_score(
    *,
    horizon: int,
    alpha_score: float,
    action_score: float,
    portfolio_score: float,
    technical_score: float,
) -> float:
    alpha_weight, action_weight, portfolio_weight, technical_weight = (
        HORIZON_WEIGHTS[horizon]
    )
    score = (
        alpha_weight * alpha_score
        + action_weight * action_score
        + portfolio_weight * portfolio_score
        + technical_weight * technical_score
    )
    return max(-100.0, min(100.0, score))


def awfi_signal(horizon: int, score: float) -> str:
    threshold = HORIZON_THRESHOLDS[horizon]
    if score >= threshold:
        return "BUY"
    if score <= -threshold:
        return "SELL"
    return "HOLD"
