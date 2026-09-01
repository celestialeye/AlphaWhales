from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from itertools import product

from .awfi import AWFI_VERSION


PROTOCOL_VERSION = "alpha-whale-predictive-v9.6"
FORMULA_MINIMUM_MANAGERS = {
    "alpha_v1_n3": 3,
    "breadth_v1_n3": 3,
    "conviction_v1_n3": 3,
    "alpha_v1_n5": 5,
}
FORMULA_ORDER = tuple(FORMULA_MINIMUM_MANAGERS)
ACTION_THRESHOLDS = (25.0, 50.0, 75.0, 100.0)
ALLOWED_OPTIMIZATION_HORIZONS = (126, 252, 378, 504)
EXPERIMENT_SENTIMENT_ONLY = "SENTIMENT_ONLY"
EXPERIMENT_TECHNICAL_COMBINED = "TECHNICAL_COMBINED"
EXPERIMENT_DECOMPOSED_SWEEP = "DECOMPOSED_SWEEP"
EXPERIMENT_MACRO_SECTOR = "MACRO_SECTOR_CHALLENGER"
EXPERIMENT_FUNDAMENTAL = "FUNDAMENTAL_CHALLENGER"
EXPERIMENT_GROUPS = (
    EXPERIMENT_SENTIMENT_ONLY,
    EXPERIMENT_TECHNICAL_COMBINED,
    EXPERIMENT_DECOMPOSED_SWEEP,
    EXPERIMENT_MACRO_SECTOR,
)
CONTEXT_SENTIMENT_ONLY = "SENTIMENT_ONLY"
CONTEXT_PRICE_CAP_BUY_25 = "PRICE_CAP_BUY_25"
CONTEXT_PRICE_CAP_BUY_50 = "PRICE_CAP_BUY_50"
CONTEXT_PRICE_CAP_BUY_75 = "PRICE_CAP_BUY_75"
CONTEXT_TREND_SUPPORTIVE = "TREND_SUPPORTIVE"
CONTEXT_STRICT_TREND_SUPPORTIVE = "STRICT_TREND_SUPPORTIVE"
CONTEXT_TREND_AND_PRICE_25 = "TREND_AND_PRICE_25"
CONTEXT_TREND_AND_PRICE_50 = "TREND_AND_PRICE_50"
CONTEXT_TREND_AND_PRICE_75 = "TREND_AND_PRICE_75"


@dataclass(frozen=True)
class CandidateSpec:
    formula_id: str
    positive_threshold: float
    negative_threshold: float
    context_id: str
    experiment_group: str
    score_profile_id: str = "ALPHA"
    action_profile_id: str = "SYMMETRIC"
    alpha_weight: float = 1.0
    action_weight: float = 0.0
    portfolio_weight: float = 0.0
    acceleration_weight: float = 0.0
    crowding_weight: float = 0.0
    persistence_weight: float = 0.0
    technical_weight: float = 0.0
    sector_weight: float = 0.0
    macro_weight: float = 0.0
    sensitivity_weight: float = 0.0
    value_weight: float = 0.0
    quality_weight: float = 0.0
    safety_weight: float = 0.0


ACTION_PROFILES = {
    "SYMMETRIC": (1.0, 1.0, 1.0, 1.0),
    "BUY_ASYMMETRIC": (1.0, 0.75, 0.25, 0.25),
}

BLOCK_WEIGHT_PROFILES = {
    "ALPHA_ONLY": (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "BALANCED": (0.50, 0.25, 0.25, 0.0, 0.0, 0.0, 0.0),
    "ACTION_HEAVY": (0.40, 0.40, 0.20, 0.0, 0.0, 0.0, 0.0),
    "ACCELERATION": (0.40, 0.25, 0.20, 0.15, 0.0, 0.0, 0.0),
    "CROWDING": (0.40, 0.25, 0.15, 0.10, 0.10, 0.0, 0.0),
    "PERSISTENCE": (0.40, 0.20, 0.15, 0.10, 0.05, 0.10, 0.0),
    "TECHNICAL_BALANCED": (
        0.40,
        0.20,
        0.15,
        0.10,
        0.05,
        0.0,
        0.10,
    ),
    "TECHNICAL_HEAVY": (
        0.35,
        0.20,
        0.15,
        0.10,
        0.05,
        0.0,
        0.15,
    ),
}


def candidate_specs(experiment_group: str) -> tuple[CandidateSpec, ...]:
    if experiment_group == EXPERIMENT_SENTIMENT_ONLY:
        contexts = (CONTEXT_SENTIMENT_ONLY,)
    elif experiment_group == EXPERIMENT_TECHNICAL_COMBINED:
        contexts = (
            CONTEXT_TREND_SUPPORTIVE,
            CONTEXT_STRICT_TREND_SUPPORTIVE,
        )
    elif experiment_group == EXPERIMENT_DECOMPOSED_SWEEP:
        specs = []
        seen = set()
        for score_profile_id, weights in BLOCK_WEIGHT_PROFILES.items():
            for action_profile_id in ACTION_PROFILES:
                identity = (score_profile_id, action_profile_id)
                if score_profile_id == "ALPHA_ONLY":
                    identity = (score_profile_id, "SYMMETRIC")
                if identity in seen:
                    continue
                seen.add(identity)
                for positive_threshold, negative_threshold in product(
                    ACTION_THRESHOLDS,
                    repeat=2,
                ):
                    specs.append(
                        CandidateSpec(
                            formula_id="decomposed_v1",
                            positive_threshold=positive_threshold,
                            negative_threshold=negative_threshold,
                            context_id=CONTEXT_SENTIMENT_ONLY,
                            experiment_group=experiment_group,
                            score_profile_id=score_profile_id,
                            action_profile_id=identity[1],
                            alpha_weight=weights[0],
                            action_weight=weights[1],
                            portfolio_weight=weights[2],
                            acceleration_weight=weights[3],
                            crowding_weight=weights[4],
                            persistence_weight=weights[5],
                            technical_weight=weights[6],
                        )
                    )
        return tuple(specs)
    else:
        raise ValueError(f"Unsupported experiment_group: {experiment_group}")
    return tuple(
        CandidateSpec(
            formula_id="alpha_v1_n3",
            positive_threshold=positive_threshold,
            negative_threshold=negative_threshold,
            context_id=context_id,
            experiment_group=experiment_group,
        )
        for context_id in contexts
        for positive_threshold, negative_threshold in product(
            ACTION_THRESHOLDS,
            repeat=2,
        )
    )
SPLIT_FACTORS = (
    1.25,
    1.5,
    2.0,
    3.0,
    4.0,
    5.0,
    10.0,
    0.8,
    2.0 / 3.0,
    0.5,
    1.0 / 3.0,
    0.25,
    0.2,
    0.1,
)


@dataclass(frozen=True)
class ResearchConfig:
    """Immutable, pre-registered research protocol."""

    as_of_days: int = 45
    horizons: tuple[int, ...] = ALLOWED_OPTIMIZATION_HORIZONS
    first_test_period: date = date(2023, 3, 31)
    embargo_sessions: int = 5
    inner_warmup_quarters: int = 4
    minimum_inner_validation_quarters: int = 4
    minimum_inner_predictions: int = 20
    minimum_inner_class_count: int = 3
    minimum_inner_coverage: float = 0.10
    split_min_managers: int = 3
    split_min_support: float = 0.80
    split_ratio_tolerance: float = 0.05
    minimum_current_holders: int = 0
    minimum_meaningful_managers: int = 1
    universe_mode: str = "LATEST_AVAILABLE_ROSTER_TOP10"
    top_holdings_per_manager: int = 10
    random_seed: int = 17
    cohort_mode: str = "CURRENT_ROSTER_RETROSPECTIVE"

    def __post_init__(self) -> None:
        if self.as_of_days < 0:
            raise ValueError("as_of_days must be non-negative")
        if not self.horizons or any(
            value not in ALLOWED_OPTIMIZATION_HORIZONS
            for value in self.horizons
        ):
            raise ValueError(
                "horizons must be selected from "
                f"{ALLOWED_OPTIMIZATION_HORIZONS}"
            )
        if self.embargo_sessions < 0:
            raise ValueError("embargo_sessions must be non-negative")
        if not 0 < self.minimum_inner_coverage <= 1:
            raise ValueError("minimum_inner_coverage must be in (0, 1]")
        if not 0 < self.split_min_support <= 1:
            raise ValueError("split_min_support must be in (0, 1]")
        if self.minimum_current_holders < 0:
            raise ValueError("minimum_current_holders must be non-negative")
        if self.minimum_meaningful_managers < 1:
            raise ValueError("minimum_meaningful_managers must be positive")
        if self.universe_mode != "LATEST_AVAILABLE_ROSTER_TOP10":
            raise ValueError(
                "Only LATEST_AVAILABLE_ROSTER_TOP10 is supported by this protocol"
            )
        if self.top_holdings_per_manager != 10:
            raise ValueError("top_holdings_per_manager must remain 10")
        if self.cohort_mode != "CURRENT_ROSTER_RETROSPECTIVE":
            raise ValueError(
                "POINT_IN_TIME cohort construction is not implemented; "
                "a mode flag cannot substitute for dated roster evidence"
            )

    def as_dict(self) -> dict:
        result = asdict(self)
        result["first_test_period"] = self.first_test_period.isoformat()
        result["horizons"] = list(self.horizons)
        return result
