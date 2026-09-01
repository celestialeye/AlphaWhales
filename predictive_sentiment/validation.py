from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .config import (
    ACTION_PROFILES,
    ACTION_THRESHOLDS,
    CandidateSpec,
    CONTEXT_PRICE_CAP_BUY_25,
    CONTEXT_PRICE_CAP_BUY_50,
    CONTEXT_PRICE_CAP_BUY_75,
    CONTEXT_SENTIMENT_ONLY,
    CONTEXT_STRICT_TREND_SUPPORTIVE,
    CONTEXT_TREND_AND_PRICE_25,
    CONTEXT_TREND_AND_PRICE_50,
    CONTEXT_TREND_AND_PRICE_75,
    CONTEXT_TREND_SUPPORTIVE,
    EXPERIMENT_DECOMPOSED_SWEEP,
    EXPERIMENT_FUNDAMENTAL,
    EXPERIMENT_MACRO_SECTOR,
    EXPERIMENT_SENTIMENT_ONLY,
    candidate_specs,
    ResearchConfig,
)


@dataclass(frozen=True)
class EvaluationResult:
    horizon: int
    experiment_group: str
    metrics: dict[str, float | int | None]
    predictions: pd.DataFrame
    selections: pd.DataFrame
    candidate_trials: pd.DataFrame
    rank_ic_by_quarter: pd.DataFrame


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / total
            + z * z / (4.0 * total * total)
        )
        / denominator
    )
    return center - margin, center + margin


def classification_metrics(
    actual: pd.Series,
    predicted: pd.Series,
) -> dict[str, float | int]:
    actual_values = actual.astype(int).to_numpy()
    predicted_values = predicted.astype(int).to_numpy()
    tp = int(((actual_values == 1) & (predicted_values == 1)).sum())
    tn = int(((actual_values == 0) & (predicted_values == 0)).sum())
    fp = int(((actual_values == 0) & (predicted_values == 1)).sum())
    fn = int(((actual_values == 1) & (predicted_values == 0)).sum())
    total = tp + tn + fp + fn
    positive_count = tp + fn
    negative_count = tn + fp
    accuracy = (tp + tn) / total if total else math.nan
    sensitivity = tp / positive_count if positive_count else math.nan
    specificity = tn / negative_count if negative_count else math.nan
    balanced = (
        (sensitivity + specificity) / 2.0
        if math.isfinite(sensitivity) and math.isfinite(specificity)
        else math.nan
    )
    lower, upper = wilson_interval(tp + tn, total)
    return {
        "sample_size": total,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": accuracy,
        "balanced_accuracy": balanced,
        "buy_precision": tp / (tp + fp) if tp + fp else math.nan,
        "sell_precision": tn / (tn + fn) if tn + fn else math.nan,
        "buy_signal_count": tp + fp,
        "sell_signal_count": tn + fn,
        "accuracy_wilson_lower": lower,
        "accuracy_wilson_upper": upper,
    }


def _predictions(
    rows: pd.DataFrame,
    positive_threshold: float,
    negative_threshold: float,
    context_id: str = CONTEXT_SENTIMENT_ONLY,
) -> pd.DataFrame:
    if rows.empty:
        return rows.assign(prediction=pd.Series(dtype="int64"))
    selected = rows[
        (rows["score"] >= positive_threshold)
        | (rows["score"] <= -negative_threshold)
    ].copy()
    bullish = selected["score"] > 0
    bearish = selected["score"] < 0
    price_caps = {
        CONTEXT_PRICE_CAP_BUY_25: 25.0,
        CONTEXT_PRICE_CAP_BUY_50: 50.0,
        CONTEXT_PRICE_CAP_BUY_75: 75.0,
        CONTEXT_TREND_AND_PRICE_25: 25.0,
        CONTEXT_TREND_AND_PRICE_50: 50.0,
        CONTEXT_TREND_AND_PRICE_75: 75.0,
    }
    trend_contexts = {
        CONTEXT_TREND_SUPPORTIVE,
        CONTEXT_STRICT_TREND_SUPPORTIVE,
        CONTEXT_TREND_AND_PRICE_25,
        CONTEXT_TREND_AND_PRICE_50,
        CONTEXT_TREND_AND_PRICE_75,
    }
    if context_id in price_caps:
        cap = price_caps[context_id]
        price_supportive = bearish | (
            bullish
            & (selected["price_above_52_week_low_pct"] <= cap)
        )
        selected = selected[price_supportive].copy()
        bullish = selected["score"] > 0
        bearish = selected["score"] < 0
    if context_id in trend_contexts:
        if context_id == CONTEXT_STRICT_TREND_SUPPORTIVE:
            trend_supportive = (
                (bullish & (selected["trend_regime"] == "BULLISH"))
                | (
                    bearish
                    & (selected["sma_50"] < selected["sma_200"])
                    & (selected["momentum_6m_pct"] < 0)
                )
            )
        else:
            trend_supportive = (
                (
                    bullish
                    & selected["trend_regime"].isin(
                        {"BULLISH", "NEUTRAL"}
                    )
                )
                | (bearish & (selected["trend_regime"] == "BEARISH"))
            )
        selected = selected[trend_supportive].copy()
    elif context_id not in {
        CONTEXT_SENTIMENT_ONLY,
        *price_caps,
    }:
        raise ValueError(f"Unsupported context_id: {context_id}")
    selected["prediction"] = (selected["score"] > 0).astype(int)
    selected["context_id"] = context_id
    selected["decision_signal"] = np.where(
        selected["score"] > 0,
        "BUY",
        "SELL",
    )
    return selected


def _quarterly_rank_ic(rows: pd.DataFrame) -> pd.DataFrame:
    records = []
    for period, group in rows.groupby("report_period"):
        if (
            len(group) < 5
            or group["score"].nunique() < 2
            or group["target_return"].nunique() < 2
        ):
            continue
        value = group["score"].corr(
            group["target_return"], method="spearman"
        )
        if pd.notna(value):
            records.append({"report_period": period, "rank_ic": float(value)})
    return pd.DataFrame(records, columns=["report_period", "rank_ic"])


def _candidate_rows(
    rows: pd.DataFrame,
    spec: CandidateSpec,
) -> pd.DataFrame:
    if spec.formula_id == "awfi_msr_v1":
        required = ["base_awfi_score"]
        if spec.sector_weight > 0:
            required.append("sector_score")
        if spec.macro_weight > 0:
            required.append("macro_score")
        if spec.sensitivity_weight > 0:
            required.append("sensitivity_score")
        result = rows.dropna(subset=required).copy()
        support_weight = (
            spec.sector_weight
            + spec.macro_weight
            + spec.sensitivity_weight
        )
        result["score"] = (
            (1.0 - support_weight) * result["base_awfi_score"]
            + spec.sector_weight * result.get("sector_score", 0.0)
            + spec.macro_weight * result.get("macro_score", 0.0)
            + spec.sensitivity_weight * result.get(
                "sensitivity_score", 0.0
            )
        ).clip(-100.0, 100.0)
        result["formula_id"] = spec.formula_id
        return result
    if spec.formula_id == "awfi_f_v1":
        required = ["base_awfi_score"]
        if spec.value_weight > 0:
            required.append("value_score")
        if spec.quality_weight > 0:
            required.append("quality_score")
        if spec.safety_weight > 0:
            required.append("safety_score")
        result = rows.dropna(subset=required).copy()
        support_weight = (
            spec.value_weight + spec.quality_weight + spec.safety_weight
        )
        result["score"] = (
            (1.0 - support_weight) * result["base_awfi_score"]
            + spec.value_weight * result.get("value_score", 0.0)
            + spec.quality_weight * result.get("quality_score", 0.0)
            + spec.safety_weight * result.get("safety_score", 0.0)
        ).clip(-100.0, 100.0)
        result["formula_id"] = spec.formula_id
        return result
    if spec.formula_id != "decomposed_v1":
        return rows[rows["formula_id"] == spec.formula_id].copy()
    required = {
        "alpha_score",
        "new_strength",
        "increased_strength",
        "decreased_strength",
        "closed_strength",
        "portfolio_weight_score",
        "acceleration_score",
        "crowding_score",
        "persistence_score",
        "technical_score",
    }
    if not required.issubset(rows.columns):
        return pd.DataFrame(columns=[*rows.columns, "score"])
    result = rows.copy()
    new_weight, increased_weight, decreased_weight, closed_weight = (
        ACTION_PROFILES[spec.action_profile_id]
    )
    numerator = (
        new_weight * result["new_strength"]
        + increased_weight * result["increased_strength"]
        - decreased_weight * result["decreased_strength"]
        - closed_weight * result["closed_strength"]
    )
    denominator = (
        new_weight * result["new_strength"]
        + increased_weight * result["increased_strength"]
        + decreased_weight * result["decreased_strength"]
        + closed_weight * result["closed_strength"]
    )
    result["action_score"] = np.where(
        denominator > 0,
        100.0 * numerator / denominator,
        0.0,
    )
    result["score"] = (
        spec.alpha_weight * result["alpha_score"]
        + spec.action_weight * result["action_score"]
        + spec.portfolio_weight * result["portfolio_weight_score"]
        + spec.acceleration_weight * result["acceleration_score"]
        + spec.crowding_weight * result["crowding_score"]
        + spec.persistence_weight * result["persistence_score"]
        + spec.technical_weight * result["technical_score"]
    ).clip(-100.0, 100.0)
    result["formula_id"] = spec.formula_id
    return result


def _candidate_metrics(
    rows: pd.DataFrame,
    spec: CandidateSpec,
) -> dict[str, float | int]:
    base_rows = (
        rows[rows["formula_id"] == spec.formula_id]
        if spec.formula_id
        not in {"decomposed_v1", "awfi_msr_v1", "awfi_f_v1"}
        else rows
    )
    formula_rows = _candidate_rows(rows, spec)
    predicted = _predictions(
        formula_rows,
        spec.positive_threshold,
        spec.negative_threshold,
        spec.context_id,
    )
    metrics = classification_metrics(
        predicted["label"], predicted["prediction"]
    )
    metrics["eligibility_coverage"] = (
        len(formula_rows) / len(base_rows) if len(base_rows) else 0.0
    )
    metrics["coverage"] = (
        len(predicted) / len(base_rows) if len(base_rows) else 0.0
    )
    rank_rows = _quarterly_rank_ic(formula_rows)
    metrics["rank_ic"] = (
        float(rank_rows["rank_ic"].mean()) if not rank_rows.empty else math.nan
    )
    metrics["validation_quarters"] = int(
        formula_rows["report_period"].nunique()
    )
    quarter_balanced = []
    for period in sorted(formula_rows["report_period"].unique()):
        quarter = predicted[predicted["report_period"] == period]
        if quarter.empty:
            quarter_balanced.append(0.50)
            continue
        quarter_metrics = classification_metrics(
            quarter["label"],
            quarter["prediction"],
        )
        value = quarter_metrics["balanced_accuracy"]
        quarter_balanced.append(
            value
            if math.isfinite(value)
            else quarter_metrics["accuracy"]
        )
    metrics["balanced_quarters"] = len(quarter_balanced)
    metrics["mean_quarter_balanced_accuracy"] = (
        float(np.mean(quarter_balanced))
        if quarter_balanced
        else math.nan
    )
    metrics["quarter_positive_edge_fraction"] = (
        float(np.mean(np.asarray(quarter_balanced) > 0.50))
        if quarter_balanced
        else math.nan
    )
    metrics["quarter_balanced_std"] = (
        float(np.std(quarter_balanced, ddof=1))
        if len(quarter_balanced) > 1
        else math.nan
    )
    return metrics


def _candidate_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
    *,
    experiment_group: str,
) -> list[dict]:
    if experiment_group == EXPERIMENT_DECOMPOSED_SWEEP:
        trials, _ = _decomposed_two_stage_trials(training, config)
        return trials
    if experiment_group == EXPERIMENT_MACRO_SECTOR:
        trials, _ = _macro_two_stage_trials(training, config)
        return trials
    if experiment_group == EXPERIMENT_FUNDAMENTAL:
        trials, _ = _fundamental_two_stage_trials(training, config)
        return trials
    periods = sorted(training["report_period"].unique())
    validation_periods = periods[config.inner_warmup_quarters :]
    if len(validation_periods) < config.minimum_inner_validation_quarters:
        return []
    validation = training[
        training["report_period"].isin(validation_periods)
    ]
    candidates = []
    for candidate_order, spec in enumerate(
        candidate_specs(experiment_group)
    ):
        candidates.append(
            _trial_record(
                validation,
                spec,
                config,
                candidate_order=candidate_order,
                stage="TUNING",
            )
        )
    return candidates


def _trial_record(
    rows: pd.DataFrame,
    spec: CandidateSpec,
    config: ResearchConfig,
    *,
    candidate_order: int,
    stage: str,
) -> dict:
    metrics = _candidate_metrics(rows, spec)
    rejection_reasons = []
    if metrics["sample_size"] < config.minimum_inner_predictions:
        rejection_reasons.append("MINIMUM_PREDICTIONS")
    if metrics["positive_count"] < config.minimum_inner_class_count:
        rejection_reasons.append("MINIMUM_POSITIVE_CLASS")
    if metrics["negative_count"] < config.minimum_inner_class_count:
        rejection_reasons.append("MINIMUM_NEGATIVE_CLASS")
    if metrics["coverage"] < config.minimum_inner_coverage:
        rejection_reasons.append("MINIMUM_COVERAGE")
    if metrics["eligibility_coverage"] < 0.80:
        rejection_reasons.append("MINIMUM_FEATURE_AVAILABILITY")
    if (
        metrics["balanced_quarters"]
        < config.minimum_inner_validation_quarters
    ):
        rejection_reasons.append("MINIMUM_VALIDATION_QUARTERS")
    if not math.isfinite(metrics["balanced_accuracy"]):
        rejection_reasons.append("BALANCED_ACCURACY_UNAVAILABLE")
    return {
        "formula_id": spec.formula_id,
        "positive_threshold": spec.positive_threshold,
        "negative_threshold": spec.negative_threshold,
        "context_id": spec.context_id,
        "experiment_group": spec.experiment_group,
        "score_profile_id": spec.score_profile_id,
        "action_profile_id": spec.action_profile_id,
        "alpha_weight": spec.alpha_weight,
        "action_weight": spec.action_weight,
        "portfolio_weight": spec.portfolio_weight,
        "acceleration_weight": spec.acceleration_weight,
        "crowding_weight": spec.crowding_weight,
        "persistence_weight": spec.persistence_weight,
        "technical_weight": spec.technical_weight,
        "sector_weight": spec.sector_weight,
        "macro_weight": spec.macro_weight,
        "sensitivity_weight": spec.sensitivity_weight,
        "value_weight": spec.value_weight,
        "quality_weight": spec.quality_weight,
        "safety_weight": spec.safety_weight,
        "candidate_order": candidate_order,
        "stage": stage,
        "eligible": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        **metrics,
    }


def _decomposed_two_stage_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
) -> tuple[list[dict], dict | None]:
    periods = sorted(training["report_period"].unique())
    required = config.minimum_inner_validation_quarters * 2
    if len(periods) < required:
        return [], None
    tuning_periods = periods[-config.minimum_inner_validation_quarters :]
    first_tuning = training[
        training["report_period"] == tuning_periods[0]
    ]
    if first_tuning.empty:
        return [], None
    first_tuning_entry = int(first_tuning["entry_index"].min())
    screening = training[
        (training["report_period"] < tuning_periods[0])
        & (
            training["exit_index"]
            < first_tuning_entry - config.embargo_sessions
        )
    ]
    if (
        screening["report_period"].nunique()
        < config.minimum_inner_validation_quarters
    ):
        return [], None
    tuning = training[training["report_period"].isin(tuning_periods)]

    base_specs = []
    seen = set()
    for spec in candidate_specs(EXPERIMENT_DECOMPOSED_SWEEP):
        identity = (spec.score_profile_id, spec.action_profile_id)
        if (
            spec.positive_threshold != 25.0
            or spec.negative_threshold != 25.0
            or spec.technical_weight != 0.0
            or identity in seen
        ):
            continue
        seen.add(identity)
        base_specs.append(spec)
    if len(base_specs) > 12:
        raise ValueError("Decomposed screening registry exceeds 12 candidates")
    screen_trials = [
        _trial_record(
            screening,
            spec,
            config,
            candidate_order=index,
            stage="SCREENING",
        )
        for index, spec in enumerate(base_specs)
    ]
    selected_base = _choose_candidate(screen_trials)
    if selected_base is None:
        return screen_trials, None

    tuning_specs = []
    base_weights = (
        selected_base["alpha_weight"],
        selected_base["action_weight"],
        selected_base["portfolio_weight"],
        selected_base["acceleration_weight"],
        selected_base["crowding_weight"],
        selected_base["persistence_weight"],
    )
    for technical_weight in (0.0, 0.15):
        scale = 1.0 - technical_weight
        for positive_threshold in ACTION_THRESHOLDS:
            for negative_threshold in ACTION_THRESHOLDS:
                tuning_specs.append(
                    CandidateSpec(
                        formula_id="decomposed_v1",
                        positive_threshold=positive_threshold,
                        negative_threshold=negative_threshold,
                        context_id=CONTEXT_SENTIMENT_ONLY,
                        experiment_group=EXPERIMENT_DECOMPOSED_SWEEP,
                        score_profile_id=(
                            f"{selected_base['score_profile_id']}"
                            f"_T{int(technical_weight * 100):02d}"
                        ),
                        action_profile_id=selected_base[
                            "action_profile_id"
                        ],
                        alpha_weight=base_weights[0] * scale,
                        action_weight=base_weights[1] * scale,
                        portfolio_weight=base_weights[2] * scale,
                        acceleration_weight=base_weights[3] * scale,
                        crowding_weight=base_weights[4] * scale,
                        persistence_weight=base_weights[5] * scale,
                        technical_weight=technical_weight,
                    )
                )
    if len(tuning_specs) > 32:
        raise ValueError("Decomposed tuning registry exceeds 32 candidates")
    tuning_trials = [
        _trial_record(
            tuning,
            spec,
            config,
            candidate_order=len(screen_trials) + index,
            stage="TUNING",
        )
        for index, spec in enumerate(tuning_specs)
    ]
    selected = _choose_candidate(tuning_trials)
    return [*screen_trials, *tuning_trials], selected


def _macro_two_stage_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
) -> tuple[list[dict], dict | None]:
    periods = sorted(training["report_period"].unique())
    required = config.minimum_inner_validation_quarters * 2
    if len(periods) < required:
        return [], None
    tuning_periods = periods[-config.minimum_inner_validation_quarters :]
    first_tuning = training[
        training["report_period"] == tuning_periods[0]
    ]
    if first_tuning.empty:
        return [], None
    first_tuning_entry = int(first_tuning["entry_index"].min())
    screening = training[
        (training["report_period"] < tuning_periods[0])
        & (
            training["exit_index"]
            < first_tuning_entry - config.embargo_sessions
        )
    ]
    if (
        screening["report_period"].nunique()
        < config.minimum_inner_validation_quarters
    ):
        return [], None
    tuning = training[training["report_period"].isin(tuning_periods)]
    horizon = int(training["horizon"].iloc[0])
    frozen_threshold = 25.0 if horizon == 504 else 75.0
    profiles = (
        ("CONTROL", 0.0, 0.0, 0.0),
        ("SECTOR_ONLY", 1.0, 0.0, 0.0),
        ("MACRO_ONLY", 0.0, 1.0, 0.0),
        ("SECTOR_MACRO_50_50", 0.5, 0.5, 0.0),
        ("SECTOR_HEAVY_67_33", 0.67, 0.33, 0.0),
        ("MACRO_HEAVY_33_67", 0.33, 0.67, 0.0),
        ("SECTOR_MACRO_BETA_40_40_20", 0.4, 0.4, 0.2),
    )
    screen_specs = [
        CandidateSpec(
            formula_id="awfi_msr_v1",
            positive_threshold=frozen_threshold,
            negative_threshold=frozen_threshold,
            context_id=CONTEXT_SENTIMENT_ONLY,
            experiment_group=EXPERIMENT_MACRO_SECTOR,
            score_profile_id=name,
            sector_weight=0.20 * sector_ratio,
            macro_weight=0.20 * macro_ratio,
            sensitivity_weight=0.20 * sensitivity_ratio,
        )
        for name, sector_ratio, macro_ratio, sensitivity_ratio in profiles
    ]
    screen_trials = [
        _trial_record(
            screening,
            spec,
            config,
            candidate_order=index,
            stage="SCREENING",
        )
        for index, spec in enumerate(screen_specs)
    ]
    selected_base = _choose_candidate(screen_trials)
    if selected_base is None:
        return screen_trials, None
    selected_support = (
        selected_base["sector_weight"] + selected_base["macro_weight"]
        + selected_base["sensitivity_weight"]
    )
    if selected_support > 0:
        sector_ratio = selected_base["sector_weight"] / selected_support
        macro_ratio = selected_base["macro_weight"] / selected_support
        sensitivity_ratio = (
            selected_base["sensitivity_weight"] / selected_support
        )
    else:
        sector_ratio = macro_ratio = sensitivity_ratio = 0.0
    tuning_specs = []
    for support_weight in (0.10, 0.20):
        for positive_threshold in ACTION_THRESHOLDS:
            for negative_threshold in ACTION_THRESHOLDS:
                tuning_specs.append(
                    CandidateSpec(
                        formula_id="awfi_msr_v1",
                        positive_threshold=positive_threshold,
                        negative_threshold=negative_threshold,
                        context_id=CONTEXT_SENTIMENT_ONLY,
                        experiment_group=EXPERIMENT_MACRO_SECTOR,
                        score_profile_id=(
                            f"{selected_base['score_profile_id']}"
                            f"_W{int(support_weight * 100):02d}"
                        ),
                        sector_weight=support_weight * sector_ratio,
                        macro_weight=support_weight * macro_ratio,
                        sensitivity_weight=(
                            support_weight * sensitivity_ratio
                        ),
                    )
                )
    tuning_trials = [
        _trial_record(
            tuning,
            spec,
            config,
            candidate_order=len(screen_trials) + index,
            stage="TUNING",
        )
        for index, spec in enumerate(tuning_specs)
    ]
    return [*screen_trials, *tuning_trials], _choose_candidate(tuning_trials)


def _fundamental_two_stage_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
) -> tuple[list[dict], dict | None]:
    periods = sorted(training["report_period"].unique())
    required = config.minimum_inner_validation_quarters * 2
    if len(periods) < required:
        return [], None
    tuning_periods = periods[-config.minimum_inner_validation_quarters :]
    first_tuning = training[
        training["report_period"] == tuning_periods[0]
    ]
    if first_tuning.empty:
        return [], None
    first_tuning_entry = int(first_tuning["entry_index"].min())
    screening = training[
        (training["report_period"] < tuning_periods[0])
        & (
            training["exit_index"]
            < first_tuning_entry - config.embargo_sessions
        )
    ]
    if (
        screening["report_period"].nunique()
        < config.minimum_inner_validation_quarters
    ):
        return [], None
    tuning = training[training["report_period"].isin(tuning_periods)]
    horizon = int(training["horizon"].iloc[0])
    frozen_threshold = 25.0 if horizon == 504 else 75.0
    profiles = (
        ("CONTROL", 0.0, 0.0, 0.0),
        ("VALUE_ONLY", 1.0, 0.0, 0.0),
        ("QUALITY_ONLY", 0.0, 1.0, 0.0),
        ("SAFETY_ONLY", 0.0, 0.0, 1.0),
        ("VALUE_QUALITY", 0.5, 0.5, 0.0),
        ("QUALITY_SAFETY", 0.0, 0.6, 0.4),
        ("BALANCED", 0.35, 0.40, 0.25),
    )
    screen_specs = [
        CandidateSpec(
            formula_id="awfi_f_v1",
            positive_threshold=frozen_threshold,
            negative_threshold=frozen_threshold,
            context_id=CONTEXT_SENTIMENT_ONLY,
            experiment_group=EXPERIMENT_FUNDAMENTAL,
            score_profile_id=name,
            value_weight=0.20 * value_ratio,
            quality_weight=0.20 * quality_ratio,
            safety_weight=0.20 * safety_ratio,
        )
        for name, value_ratio, quality_ratio, safety_ratio in profiles
    ]
    screen_trials = [
        _trial_record(
            screening,
            spec,
            config,
            candidate_order=index,
            stage="SCREENING",
        )
        for index, spec in enumerate(screen_specs)
    ]
    selected_base = _choose_candidate(screen_trials)
    if selected_base is None:
        return screen_trials, None
    selected_support = (
        selected_base["value_weight"]
        + selected_base["quality_weight"]
        + selected_base["safety_weight"]
    )
    if selected_support > 0:
        ratios = (
            selected_base["value_weight"] / selected_support,
            selected_base["quality_weight"] / selected_support,
            selected_base["safety_weight"] / selected_support,
        )
    else:
        ratios = (0.0, 0.0, 0.0)
    tuning_specs = []
    for support_weight in (0.10, 0.20):
        for positive_threshold in ACTION_THRESHOLDS:
            for negative_threshold in ACTION_THRESHOLDS:
                tuning_specs.append(
                    CandidateSpec(
                        formula_id="awfi_f_v1",
                        positive_threshold=positive_threshold,
                        negative_threshold=negative_threshold,
                        context_id=CONTEXT_SENTIMENT_ONLY,
                        experiment_group=EXPERIMENT_FUNDAMENTAL,
                        score_profile_id=(
                            f"{selected_base['score_profile_id']}"
                            f"_W{int(support_weight * 100):02d}"
                        ),
                        value_weight=support_weight * ratios[0],
                        quality_weight=support_weight * ratios[1],
                        safety_weight=support_weight * ratios[2],
                    )
                )
    tuning_trials = [
        _trial_record(
            tuning,
            spec,
            config,
            candidate_order=len(screen_trials) + index,
            stage="TUNING",
        )
        for index, spec in enumerate(tuning_specs)
    ]
    return [*screen_trials, *tuning_trials], _choose_candidate(tuning_trials)


def _choose_candidate(candidates: list[dict]) -> dict | None:
    eligible = [item for item in candidates if item["eligible"]]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            item["mean_quarter_balanced_accuracy"]
            if math.isfinite(item["mean_quarter_balanced_accuracy"])
            else -math.inf,
            item["quarter_positive_edge_fraction"]
            if math.isfinite(item["quarter_positive_edge_fraction"])
            else -math.inf,
            item["balanced_accuracy"],
            item["rank_ic"] if math.isfinite(item["rank_ic"]) else -math.inf,
            item["coverage"],
            -item["positive_threshold"],
            -item["negative_threshold"],
            -item["candidate_order"],
        ),
    )


def _select_candidate(
    training: pd.DataFrame,
    config: ResearchConfig,
    *,
    experiment_group: str,
) -> dict | None:
    _, selected = _selection_trials(
        training,
        config,
        experiment_group=experiment_group,
    )
    return selected


def _selection_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
    *,
    experiment_group: str,
) -> tuple[list[dict], dict | None]:
    if experiment_group == EXPERIMENT_DECOMPOSED_SWEEP:
        return _decomposed_two_stage_trials(training, config)
    if experiment_group == EXPERIMENT_MACRO_SECTOR:
        return _macro_two_stage_trials(training, config)
    if experiment_group == EXPERIMENT_FUNDAMENTAL:
        return _fundamental_two_stage_trials(training, config)
    trials = _candidate_trials(
        training,
        config,
        experiment_group=experiment_group,
    )
    return trials, _choose_candidate(trials)


def select_candidate(
    training: pd.DataFrame,
    config: ResearchConfig,
    *,
    experiment_group: str,
) -> dict | None:
    """Select a production candidate using only completed historical labels."""
    return _select_candidate(
        training,
        config,
        experiment_group=experiment_group,
    )


def select_candidate_with_trials(
    training: pd.DataFrame,
    config: ResearchConfig,
    *,
    experiment_group: str,
) -> tuple[dict | None, list[dict]]:
    trials, selected = _selection_trials(
        training,
        config,
        experiment_group=experiment_group,
    )
    return selected, trials


def apply_decision_policy(
    rows: pd.DataFrame,
    *,
    formula_id: str,
    positive_threshold: float,
    negative_threshold: float,
    context_id: str,
) -> pd.DataFrame:
    """Return BUY/HOLD/SELL decisions with Alpha sentiment as direction."""
    selected_formula = rows[rows["formula_id"] == formula_id].copy()
    selected_formula["decision_signal"] = "HOLD"
    actions = _predictions(
        selected_formula,
        positive_threshold,
        negative_threshold,
        context_id,
    )
    selected_formula.loc[
        selected_formula.index.intersection(actions.index),
        "decision_signal",
    ] = actions["decision_signal"]
    selected_formula["selected_formula_id"] = formula_id
    selected_formula["selected_positive_threshold"] = positive_threshold
    selected_formula["selected_negative_threshold"] = negative_threshold
    selected_formula["selected_context_id"] = context_id
    return selected_formula


def apply_selected_candidate(
    rows: pd.DataFrame,
    selection: dict,
) -> pd.DataFrame:
    spec = _spec_from_selection(selection)
    scored = _candidate_rows(rows, spec)
    scored["decision_signal"] = "HOLD"
    actions = _predictions(
        scored,
        spec.positive_threshold,
        spec.negative_threshold,
        spec.context_id,
    )
    scored.loc[
        scored.index.intersection(actions.index),
        "decision_signal",
    ] = actions["decision_signal"]
    scored["selected_formula_id"] = spec.formula_id
    scored["selected_positive_threshold"] = spec.positive_threshold
    scored["selected_negative_threshold"] = spec.negative_threshold
    scored["selected_context_id"] = spec.context_id
    scored["selected_score_profile_id"] = spec.score_profile_id
    scored["selected_action_profile_id"] = spec.action_profile_id
    return scored


def _spec_from_selection(selection: dict) -> CandidateSpec:
    return CandidateSpec(
        formula_id=selection["formula_id"],
        positive_threshold=float(selection["positive_threshold"]),
        negative_threshold=float(selection["negative_threshold"]),
        context_id=selection["context_id"],
        experiment_group=selection["experiment_group"],
        score_profile_id=selection["score_profile_id"],
        action_profile_id=selection["action_profile_id"],
        alpha_weight=float(selection["alpha_weight"]),
        action_weight=float(selection["action_weight"]),
        portfolio_weight=float(selection["portfolio_weight"]),
        acceleration_weight=float(selection["acceleration_weight"]),
        crowding_weight=float(selection["crowding_weight"]),
        persistence_weight=float(selection["persistence_weight"]),
        technical_weight=float(selection["technical_weight"]),
        sector_weight=float(selection.get("sector_weight", 0.0)),
        macro_weight=float(selection.get("macro_weight", 0.0)),
        sensitivity_weight=float(
            selection.get("sensitivity_weight", 0.0)
        ),
        value_weight=float(selection.get("value_weight", 0.0)),
        quality_weight=float(selection.get("quality_weight", 0.0)),
        safety_weight=float(selection.get("safety_weight", 0.0)),
    )


def _rank_ic_summary(rank_rows: pd.DataFrame) -> dict[str, float | int]:
    if rank_rows.empty:
        return {
            "mean_rank_ic": math.nan,
            "median_rank_ic": math.nan,
            "rank_ic_quarters": 0,
            "positive_rank_ic_fraction": math.nan,
            "rank_ic_ci_lower": math.nan,
            "rank_ic_ci_upper": math.nan,
        }
    values = rank_rows["rank_ic"].astype(float)
    mean = float(values.mean())
    if len(values) > 1:
        sem = float(values.std(ddof=1) / math.sqrt(len(values)))
        critical = float(stats.t.ppf(0.975, len(values) - 1))
        lower, upper = mean - critical * sem, mean + critical * sem
    else:
        lower = upper = math.nan
    return {
        "mean_rank_ic": mean,
        "median_rank_ic": float(values.median()),
        "rank_ic_quarters": int(len(values)),
        "positive_rank_ic_fraction": float((values > 0).mean()),
        "rank_ic_ci_lower": lower,
        "rank_ic_ci_upper": upper,
    }


def _quarter_block_bootstrap_lower(
    predictions: pd.DataFrame,
    *,
    random_seed: int,
    iterations: int = 2000,
) -> float:
    quarters = list(predictions["report_period"].drop_duplicates())
    if len(quarters) < 2:
        return math.nan
    grouped = {
        quarter: predictions[predictions["report_period"] == quarter]
        for quarter in quarters
    }
    generator = np.random.default_rng(random_seed)
    values = []
    for _ in range(iterations):
        sampled = generator.choice(quarters, size=len(quarters), replace=True)
        frame = pd.concat(
            [grouped[quarter] for quarter in sampled],
            ignore_index=True,
        )
        result = classification_metrics(
            frame["label"], frame["prediction"]
        )
        value = result["balanced_accuracy"]
        values.append(
            value if math.isfinite(value) else result["accuracy"]
        )
    return float(np.quantile(values, 0.025)) if values else math.nan


def _quarter_block_edge_lower(
    predictions: pd.DataFrame,
    *,
    random_seed: int,
    iterations: int = 2000,
) -> float:
    quarters = list(predictions["report_period"].drop_duplicates())
    if len(quarters) < 2:
        return math.nan
    grouped = {
        quarter: predictions[predictions["report_period"] == quarter]
        for quarter in quarters
    }
    generator = np.random.default_rng(random_seed)
    values = []
    for _ in range(iterations):
        sampled = generator.choice(quarters, size=len(quarters), replace=True)
        frame = pd.concat(
            [grouped[quarter] for quarter in sampled],
            ignore_index=True,
        )
        strategy_metrics = classification_metrics(
            frame["label"], frame["prediction"]
        )
        baseline_metrics = classification_metrics(
            frame["label"], frame["baseline_prediction"]
        )
        strategy = strategy_metrics["balanced_accuracy"]
        baseline = baseline_metrics["balanced_accuracy"]
        if not math.isfinite(strategy):
            strategy = strategy_metrics["accuracy"]
        if not math.isfinite(baseline):
            baseline = baseline_metrics["accuracy"]
        values.append(strategy - baseline)
    return float(np.quantile(values, 0.025)) if values else math.nan


def evaluate_walk_forward(
    observations: pd.DataFrame,
    *,
    horizon: int,
    config: ResearchConfig,
    experiment_group: str = EXPERIMENT_SENTIMENT_ONLY,
) -> EvaluationResult:
    rows = observations[observations["horizon"] == horizon].copy()
    if "target_return" not in rows:
        rows["target_return"] = rows["excess_return"]
    if "price_above_52_week_low_pct" not in rows:
        rows["price_above_52_week_low_pct"] = math.nan
    if "trend_regime" not in rows:
        rows["trend_regime"] = None
    if "sma_50" not in rows:
        rows["sma_50"] = math.nan
    if "sma_200" not in rows:
        rows["sma_200"] = math.nan
    if "momentum_6m_pct" not in rows:
        rows["momentum_6m_pct"] = math.nan
    rows["report_period"] = pd.to_datetime(
        rows["report_period"]
    ).dt.date
    outer_periods = [
        period
        for period in sorted(rows["report_period"].unique())
        if period >= config.first_test_period
    ]
    outer_rows = rows[rows["report_period"].isin(outer_periods)]
    fixed_outer_rows = outer_rows.copy()
    if (
        experiment_group != EXPERIMENT_SENTIMENT_ONLY
        and "alpha_score" in fixed_outer_rows
    ):
        fixed_outer_rows["formula_id"] = "alpha_v1_n3"
        fixed_outer_rows["score"] = fixed_outer_rows["alpha_score"]
    fixed_alpha = _candidate_metrics(
        fixed_outer_rows,
        CandidateSpec(
            formula_id="alpha_v1_n3",
            positive_threshold=25.0,
            negative_threshold=25.0,
            context_id=CONTEXT_SENTIMENT_ONLY,
            experiment_group=EXPERIMENT_SENTIMENT_ONLY,
        ),
    )
    fixed_alpha_rows = _predictions(
        fixed_outer_rows[
            fixed_outer_rows["formula_id"] == "alpha_v1_n3"
        ],
        25.0,
        25.0,
        CONTEXT_SENTIMENT_ONLY,
    )
    prediction_frames = []
    selection_rows = []
    candidate_trial_rows = []
    rank_frames = []
    rank_without_splits_frames = []
    selected_formula_denominator = 0
    total_opportunities = 0
    selected_quarters = 0
    for test_period in outer_periods:
        test_all = rows[rows["report_period"] == test_period]
        if test_all.empty:
            continue
        total_opportunities += len(
            test_all[["report_period", "cusip"]].drop_duplicates()
        )
        test_entry = int(test_all["entry_index"].min())
        training = rows[
            (rows["report_period"] < test_period)
            & (
                rows["exit_index"]
                < test_entry - config.embargo_sessions
            )
        ]
        fold_candidates, selected = _selection_trials(
            training,
            config,
            experiment_group=experiment_group,
        )
        for candidate in fold_candidates:
            candidate_trial_rows.append(
                {
                    "report_period": test_period,
                    "selected": (
                        selected is not None
                        and candidate["candidate_order"]
                        == selected["candidate_order"]
                    ),
                    **candidate,
                }
            )
        if selected is None:
            selection_rows.append(
                {
                    "report_period": test_period,
                    "status": "NO_SELECTABLE_CANDIDATE",
                }
            )
            continue
        formula_id = selected["formula_id"]
        positive_threshold = float(selected["positive_threshold"])
        negative_threshold = float(selected["negative_threshold"])
        context_id = selected["context_id"]
        selected_spec = _spec_from_selection(selected)
        test_formula = _candidate_rows(test_all, selected_spec)
        selected_quarters += 1
        selected_formula_denominator += len(test_formula)
        predicted = _predictions(
            test_formula,
            positive_threshold,
            negative_threshold,
            context_id,
        )
        majority = int(
            training[training["formula_id"] == formula_id]["label"].mean()
            >= 0.5
        )
        predicted["baseline_prediction"] = majority
        predicted["selected_formula_id"] = formula_id
        predicted["selected_positive_threshold"] = positive_threshold
        predicted["selected_negative_threshold"] = negative_threshold
        predicted["selected_context_id"] = context_id
        predicted["experiment_group"] = experiment_group
        predicted["outer_period"] = test_period
        prediction_frames.append(predicted)
        rank = _quarterly_rank_ic(test_formula)
        if not rank.empty:
            rank["selected_formula_id"] = formula_id
            rank_frames.append(rank)
        rank_without_splits = _quarterly_rank_ic(
            test_formula[~test_formula["split_affected"]]
        )
        if not rank_without_splits.empty:
            rank_without_splits["selected_formula_id"] = formula_id
            rank_without_splits_frames.append(rank_without_splits)
        selection_rows.append(
            {
                "report_period": test_period,
                "status": "SELECTED",
                **selected,
            }
        )

    predictions = (
        pd.concat(prediction_frames, ignore_index=True)
        if prediction_frames
        else pd.DataFrame()
    )
    selections = pd.DataFrame(selection_rows)
    candidate_trials = pd.DataFrame(candidate_trial_rows)
    rank_rows = (
        pd.concat(rank_frames, ignore_index=True)
        if rank_frames
        else pd.DataFrame(columns=["report_period", "rank_ic"])
    )
    rank_without_splits_rows = (
        pd.concat(rank_without_splits_frames, ignore_index=True)
        if rank_without_splits_frames
        else pd.DataFrame(columns=["report_period", "rank_ic"])
    )
    if predictions.empty:
        metrics = {
            "horizon": horizon,
            "experiment_group": experiment_group,
            "sample_size": 0,
            "coverage": 0.0,
            "threshold_coverage": 0.0,
            "outer_quarters": len(outer_periods),
            "selected_outer_quarters": selected_quarters,
            "selection_availability": (
                selected_quarters / len(outer_periods)
                if outer_periods
                else 0.0
            ),
        }
    else:
        metrics = classification_metrics(
            predictions["label"], predictions["prediction"]
        )
        baseline = classification_metrics(
            predictions["label"], predictions["baseline_prediction"]
        )
        without_splits = predictions[~predictions["split_affected"]]
        without_splits_metrics = classification_metrics(
            without_splits["label"], without_splits["prediction"]
        )
        without_splits_rank = _rank_ic_summary(
            rank_without_splits_rows
        )
        metrics.update(
            {
                "horizon": horizon,
                "experiment_group": experiment_group,
                "coverage": len(predictions) / total_opportunities
                if total_opportunities
                else 0.0,
                "threshold_coverage": (
                    len(predictions) / selected_formula_denominator
                    if selected_formula_denominator
                    else 0.0
                ),
                "outer_quarters": len(outer_periods),
                "selected_outer_quarters": selected_quarters,
                "selection_availability": (
                    selected_quarters / len(outer_periods)
                    if outer_periods
                    else 0.0
                ),
                "prediction_outer_quarters": int(
                    predictions["report_period"].nunique()
                ),
                "baseline_accuracy": baseline["accuracy"],
                "baseline_balanced_accuracy": baseline[
                    "balanced_accuracy"
                ],
                "always_positive_accuracy": float(
                    predictions["label"].mean()
                ),
                "always_positive_balanced_accuracy": 0.5,
                "balanced_accuracy_bootstrap_lower": (
                    _quarter_block_bootstrap_lower(
                        predictions,
                        random_seed=config.random_seed + horizon,
                    )
                ),
                "balanced_accuracy_edge": (
                    metrics["balanced_accuracy"]
                    - baseline["balanced_accuracy"]
                ),
                "balanced_accuracy_edge_bootstrap_lower": (
                    _quarter_block_edge_lower(
                        predictions,
                        random_seed=config.random_seed + horizon + 1000,
                    )
                ),
                "split_excluded_balanced_accuracy": (
                    without_splits_metrics["balanced_accuracy"]
                ),
                "split_excluded_rank_ic": without_splits_rank[
                    "mean_rank_ic"
                ],
            }
        )
        metrics["split_balanced_accuracy_degradation"] = (
            metrics["balanced_accuracy"]
            - metrics["split_excluded_balanced_accuracy"]
            if math.isfinite(metrics["balanced_accuracy"])
            and math.isfinite(metrics["split_excluded_balanced_accuracy"])
            else math.nan
        )
        metrics.update(_rank_ic_summary(rank_rows))
    selected_rows = selections[selections["status"] == "SELECTED"].copy()
    if selected_rows.empty:
        metrics["selected_unique_candidates"] = 0
        metrics["dominant_candidate_fraction"] = math.nan
    else:
        identity_columns = [
            "score_profile_id",
            "action_profile_id",
            "positive_threshold",
            "negative_threshold",
            "context_id",
        ]
        identities = selected_rows[identity_columns].astype(str).agg(
            "|".join,
            axis=1,
        )
        counts = identities.value_counts()
        metrics["selected_unique_candidates"] = int(len(counts))
        metrics["dominant_candidate_fraction"] = float(
            counts.iloc[0] / len(identities)
        )
    metrics.update(
        {
            f"fixed_alpha_t25_{key}": value
            for key, value in fixed_alpha.items()
            if key != "validation_quarters"
        }
    )
    metrics["fixed_alpha_t25_outer_quarters"] = int(
        fixed_alpha_rows["report_period"].nunique()
        if not fixed_alpha_rows.empty
        else 0
    )
    metrics["fixed_alpha_t25_always_positive_accuracy"] = (
        float(fixed_alpha_rows["label"].mean())
        if not fixed_alpha_rows.empty
        else math.nan
    )
    return EvaluationResult(
        horizon=horizon,
        experiment_group=experiment_group,
        metrics=metrics,
        predictions=predictions,
        selections=selections,
        candidate_trials=candidate_trials,
        rank_ic_by_quarter=rank_rows,
    )


def evaluate_trust_gate(
    evaluations: list[EvaluationResult],
    *,
    mapping_coverage: float,
    label_coverage: float,
    missing_terminal_price_rate: float,
    config: ResearchConfig,
) -> tuple[str, list[dict]]:
    criteria: list[dict] = []

    def add(
        horizon: int | None,
        criterion: str,
        passed: bool,
        observed: float | int | str | None,
        required: float | int | str,
    ) -> None:
        criteria.append(
            {
                "horizon": horizon,
                "criterion": criterion,
                "passed": bool(passed),
                "observed_value": observed,
                "required_value": required,
            }
        )

    for evaluation in evaluations:
        metric = evaluation.metrics
        horizon = evaluation.horizon
        add(horizon, "outer_test_quarters", metric.get("outer_quarters", 0) >= 8, metric.get("outer_quarters", 0), 8)
        add(horizon, "candidate_selection_availability", metric.get("selection_availability", 0.0) >= 0.80, metric.get("selection_availability", 0.0), 0.80)
        add(horizon, "selected_predictions", metric.get("sample_size", 0) >= 200, metric.get("sample_size", 0), 200)
        add(horizon, "positive_examples", metric.get("positive_count", 0) >= 50, metric.get("positive_count", 0), 50)
        add(horizon, "negative_examples", metric.get("negative_count", 0) >= 50, metric.get("negative_count", 0), 50)
        add(horizon, "prediction_coverage", metric.get("coverage", 0.0) >= 0.25, metric.get("coverage", 0.0), 0.25)
        add(horizon, "target_accuracy", metric.get("accuracy", 0.0) >= 0.90, metric.get("accuracy"), 0.90)
        add(horizon, "balanced_accuracy", metric.get("balanced_accuracy", 0.0) >= 0.70, metric.get("balanced_accuracy"), 0.70)
        wilson_floor = max(float(metric.get("baseline_accuracy", 0.0)), 0.80)
        add(horizon, "accuracy_wilson_floor", metric.get("accuracy_wilson_lower", 0.0) > wilson_floor, metric.get("accuracy_wilson_lower"), wilson_floor)
        add(horizon, "mean_rank_ic", metric.get("mean_rank_ic", -1.0) >= 0.05, metric.get("mean_rank_ic"), 0.05)
        add(horizon, "rank_ic_ci", metric.get("rank_ic_ci_lower", -1.0) > 0.0, metric.get("rank_ic_ci_lower"), 0.0)
        add(horizon, "positive_rank_ic_quarters", metric.get("positive_rank_ic_fraction", 0.0) >= 0.60, metric.get("positive_rank_ic_fraction"), 0.60)
        add(horizon, "block_bootstrap", metric.get("balanced_accuracy_bootstrap_lower", 0.0) > 0.50, metric.get("balanced_accuracy_bootstrap_lower"), 0.50)
        add(horizon, "baseline_edge_bootstrap", metric.get("balanced_accuracy_edge_bootstrap_lower", -1.0) > 0.0, metric.get("balanced_accuracy_edge_bootstrap_lower"), 0.0)
        add(horizon, "split_excluded_balanced_accuracy", metric.get("split_excluded_balanced_accuracy", 0.0) >= 0.53, metric.get("split_excluded_balanced_accuracy"), 0.53)
        add(horizon, "split_sensitivity_degradation", metric.get("split_balanced_accuracy_degradation", 1.0) <= 0.03, metric.get("split_balanced_accuracy_degradation"), 0.03)
        add(horizon, "split_excluded_rank_ic", metric.get("split_excluded_rank_ic", -1.0) > 0.0, metric.get("split_excluded_rank_ic"), 0.0)

    add(None, "mapping_coverage", mapping_coverage >= 0.95, mapping_coverage, 0.95)
    add(None, "label_coverage", label_coverage >= 0.90, label_coverage, 0.90)
    add(None, "missing_terminal_price_rate", missing_terminal_price_rate <= 0.05, missing_terminal_price_rate, 0.05)
    add(None, "point_in_time_cohort", False, config.cohort_mode, "DATED_ROSTER_EVIDENCE")
    add(None, "historical_security_master", False, "CURRENT_CUSIP_MAPPING", "EFFECTIVE_DATED_MAPPING")
    add(
        None,
        "point_in_time_universe",
        False,
        config.universe_mode,
        "HISTORICALLY_AVAILABLE_UNIVERSE",
    )
    add(
        None,
        "published_participation_floor",
        config.minimum_meaningful_managers >= 3,
        config.minimum_meaningful_managers,
        3,
    )
    status = (
        "TRUSTWORTHY"
        if criteria and all(item["passed"] for item in criteria)
        else "NOT_TRUSTWORTHY"
    )
    return status, criteria
