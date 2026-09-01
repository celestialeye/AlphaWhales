from __future__ import annotations

import hashlib
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .config import (
    FORMULA_MINIMUM_MANAGERS,
    FORMULA_ORDER,
    ResearchConfig,
    SPLIT_FACTORS,
)


VALID_SNAPSHOT = "VALID"
FUND_LIKE_PATTERN = re.compile(
    r"\bFUND\b|MUTUAL FUND|CLOSED[- ]END|ETF|ETN|EXCHANGE[- ]TRADED|"
    r"ISHARES|VANGUARD|SPDR|DIMENSIONAL ETF|"
    r"SCHWAB STRATEGIC|PROSHARES|WISDOMTREE|VANECK|GLOBAL X|ARK ETF|"
    r"INVESCO QQQ|INVESCO EXCH|FIRST TR EXCHANGE|FIRST TRUST EXCHANGE|"
    r"J P MORGAN EXCHANGE|JPMORGAN ETF|AMERICAN CENTY ETF|"
    r"FIDELITY (?:COVINGTON|MERRIMACK)|BLACKROCK ETF|ETF SER|PGIM ETF|"
    r"HARTFORD.*EXCHANGE|CAPITAL GRP.*ETF|GOLDMAN SACHS ETF|"
    r"CAPITAL GROUP NEW GEOGRAPHY",
    re.IGNORECASE,
)
COMMON_STOCK_PATTERN = re.compile(
    r"\b(COM|COMMON|ORDINARY|ORD SHS?|SHS?|SHARES|ADR|ADS|"
    r"SPONSORED|CLASS [A-Z]|CL [A-Z]|CAP STK)\b",
    re.IGNORECASE,
)
NON_COMMON_INSTRUMENT_PATTERN = re.compile(
    r"\b(WARRANTS?|RIGHTS?|UNITS?|PREFERRED|PFD|NOTES?|BONDS?|"
    r"DEBENTURES?|DEBT|CONVERTIBLE)\b",
    re.IGNORECASE,
)


def normalize_cusip(value: object) -> str | None:
    if value is None:
        return None
    normalized = "".join(str(value).strip().upper().split())
    if not normalized or normalized in {"NAN", "NONE", "NULL"}:
        return None
    return normalized


def is_direct_common_stock(
    *,
    issuer: object,
    title: object,
    shares_type: object,
    put_call: object,
) -> bool:
    issuer_text = str(issuer or "").strip()
    title_text = str(title or "").strip()
    description = f"{issuer_text} {title_text}"
    return (
        not str(put_call or "").strip()
        and str(shares_type or "").strip().upper() in {"SH", "SHARES"}
        and not FUND_LIKE_PATTERN.search(description)
        and not NON_COMMON_INSTRUMENT_PATTERN.search(title_text)
        and bool(COMMON_STOCK_PATTERN.search(title_text))
    )


def previous_quarter(period: date) -> date:
    if period.month == 3:
        return date(period.year - 1, 12, 31)
    if period.month == 6:
        return date(period.year, 3, 31)
    if period.month == 9:
        return date(period.year, 6, 30)
    if period.month == 12:
        return date(period.year, 9, 30)
    raise ValueError(f"Not a calendar quarter end: {period}")


@dataclass(frozen=True)
class FilingRow:
    accession_number: str
    canonical_cik: str
    source_cik: str
    report_period: date
    filing_date: date
    submission_type: str
    amendment_type: str
    manager_name: str


@dataclass(frozen=True)
class HoldingRow:
    accession_number: str
    cusip: str | None
    value_usd: float
    shares: float
    shares_type: str
    put_call: str
    issuer: str = ""
    title: str = ""


@dataclass(frozen=True)
class Position:
    cusip: str
    shares: float
    reported_value: float
    weight: float


@dataclass(frozen=True)
class ManagerSnapshot:
    canonical_cik: str
    manager_name: str
    report_period: date
    as_of_date: date
    status: str
    effective_accessions: tuple[str, ...]
    source_accessions: tuple[str, ...]
    positions: tuple[Position, ...]

    @property
    def eligible_value(self) -> float:
        return sum(position.reported_value for position in self.positions)


@dataclass(frozen=True)
class RawComparison:
    canonical_cik: str
    manager_name: str
    report_period: date
    as_of_date: date
    cusip: str
    previous_shares: float
    current_shares: float
    previous_value: float
    current_value: float
    previous_weight: float
    current_weight: float


@dataclass(frozen=True)
class SplitAction:
    report_period: date
    cusip: str
    factor: float
    manager_count: int
    support_count: int
    support_fraction: float
    median_ratio: float


@dataclass(frozen=True)
class ManagerChange:
    canonical_cik: str
    manager_name: str
    report_period: date
    as_of_date: date
    cusip: str
    status: str
    previous_shares: float
    current_shares: float
    comparison_current_shares: float
    previous_value: float
    current_value: float
    previous_weight: float
    current_weight: float
    share_change_pct: float | None
    typical_share_change_pct: float | None
    typical_position_weight: float | None
    position_significance: float | None
    relative_conviction: float | None
    force_routine: bool
    split_factor: float | None


@dataclass(frozen=True)
class FormulaScore:
    report_period: date
    as_of_date: date
    cusip: str
    formula_id: str
    score: float | None
    published: bool
    meaningful_count: int
    bullish_count: int
    bearish_count: int
    breadth_score: float | None
    conviction_score: float | None
    comparable_manager_count: int
    current_holder_count: int
    median_current_weight: float | None
    split_affected: bool
    reported_value: float


def _operation(filing: FilingRow) -> str:
    if filing.submission_type == "13F-HR":
        return "REPLACE"
    amendment = filing.amendment_type.upper().strip()
    if "NEW HOLDINGS" in amendment:
        return "ADD"
    if "RESTATEMENT" in amendment:
        return "REPLACE"
    return "AMBIGUOUS"


def reconstruct_snapshot(
    filings: Sequence[FilingRow],
    holdings_by_accession: Mapping[str, Sequence[HoldingRow]],
    *,
    canonical_cik: str,
    manager_name: str,
    report_period: date,
    as_of_date: date,
) -> ManagerSnapshot:
    considered = sorted(
        (
            filing
            for filing in filings
            if filing.canonical_cik == canonical_cik
            and filing.report_period == report_period
            and filing.filing_date <= as_of_date
        ),
        key=lambda item: (item.filing_date, item.accession_number),
    )
    if not considered:
        return ManagerSnapshot(
            canonical_cik,
            manager_name,
            report_period,
            as_of_date,
            "NO_FILING_BY_AS_OF",
            (),
            (),
            (),
        )

    by_date: defaultdict[date, list[FilingRow]] = defaultdict(list)
    for filing in considered:
        by_date[filing.filing_date].append(filing)
    for same_day in by_date.values():
        operations = {_operation(filing) for filing in same_day}
        if len(same_day) > 1 and len(operations) > 1:
            return ManagerSnapshot(
                canonical_cik,
                manager_name,
                report_period,
                as_of_date,
                "AMBIGUOUS_SAME_DAY_ORDER",
                (),
                tuple(item.accession_number for item in considered),
                (),
            )

    base: str | None = None
    additions: list[str] = []
    for filing in considered:
        operation = _operation(filing)
        if operation == "AMBIGUOUS":
            return ManagerSnapshot(
                canonical_cik,
                manager_name,
                report_period,
                as_of_date,
                "AMBIGUOUS_AMENDMENT",
                (),
                tuple(item.accession_number for item in considered),
                (),
            )
        if operation == "ADD":
            if base is None:
                return ManagerSnapshot(
                    canonical_cik,
                    manager_name,
                    report_period,
                    as_of_date,
                    "NO_BASE",
                    (),
                    tuple(item.accession_number for item in considered),
                    (),
                )
            additions.append(filing.accession_number)
        else:
            base = filing.accession_number
            additions = []

    if base is None:
        status = "NO_BASE"
        effective: tuple[str, ...] = ()
    else:
        status = VALID_SNAPSHOT
        effective = (base, *additions)

    aggregated: defaultdict[str, list[float]] = defaultdict(
        lambda: [0.0, 0.0]
    )
    for accession in effective:
        for holding in holdings_by_accession.get(accession, ()):
            cusip = normalize_cusip(holding.cusip)
            if (
                cusip is None
                or holding.shares <= 0
                or holding.value_usd <= 0
                or not is_direct_common_stock(
                    issuer=holding.issuer,
                    title=holding.title,
                    shares_type=holding.shares_type,
                    put_call=holding.put_call,
                )
            ):
                continue
            aggregated[cusip][0] += holding.shares
            aggregated[cusip][1] += holding.value_usd
    total_value = sum(values[1] for values in aggregated.values())
    if status == VALID_SNAPSHOT and total_value <= 0:
        status = "NO_ELIGIBLE_HOLDINGS"
    positions = tuple(
        Position(
            cusip=cusip,
            shares=values[0],
            reported_value=values[1],
            weight=100.0 * values[1] / total_value,
        )
        for cusip, values in sorted(aggregated.items())
        if total_value > 0
    )
    return ManagerSnapshot(
        canonical_cik,
        manager_name,
        report_period,
        as_of_date,
        status,
        effective,
        tuple(item.accession_number for item in considered),
        positions,
    )


def build_snapshots(
    filings: Sequence[FilingRow],
    holdings_by_accession: Mapping[str, Sequence[HoldingRow]],
    managers: Mapping[str, str],
    *,
    as_of_days: int,
) -> list[ManagerSnapshot]:
    periods = sorted({filing.report_period for filing in filings})
    return [
        reconstruct_snapshot(
            filings,
            holdings_by_accession,
            canonical_cik=cik,
            manager_name=name,
            report_period=period,
            as_of_date=period + timedelta(days=as_of_days),
        )
        for cik, name in sorted(managers.items())
        for period in periods
    ]


def build_raw_comparisons(
    snapshots: Sequence[ManagerSnapshot],
) -> list[RawComparison]:
    by_key = {
        (snapshot.canonical_cik, snapshot.report_period): snapshot
        for snapshot in snapshots
    }
    comparisons: list[RawComparison] = []
    for current in snapshots:
        if current.status != VALID_SNAPSHOT:
            continue
        previous = by_key.get(
            (current.canonical_cik, previous_quarter(current.report_period))
        )
        if previous is None or previous.status != VALID_SNAPSHOT:
            continue
        current_positions = {item.cusip: item for item in current.positions}
        previous_positions = {item.cusip: item for item in previous.positions}
        for cusip in sorted(set(current_positions) | set(previous_positions)):
            current_position = current_positions.get(cusip)
            previous_position = previous_positions.get(cusip)
            comparisons.append(
                RawComparison(
                    canonical_cik=current.canonical_cik,
                    manager_name=current.manager_name,
                    report_period=current.report_period,
                    as_of_date=current.as_of_date,
                    cusip=cusip,
                    previous_shares=(
                        previous_position.shares if previous_position else 0.0
                    ),
                    current_shares=(
                        current_position.shares if current_position else 0.0
                    ),
                    previous_value=(
                        previous_position.reported_value
                        if previous_position
                        else 0.0
                    ),
                    current_value=(
                        current_position.reported_value
                        if current_position
                        else 0.0
                    ),
                    previous_weight=(
                        previous_position.weight if previous_position else 0.0
                    ),
                    current_weight=(
                        current_position.weight if current_position else 0.0
                    ),
                )
            )
    return comparisons


def detect_split_actions(
    comparisons: Sequence[RawComparison],
    config: ResearchConfig,
) -> dict[tuple[date, str], SplitAction]:
    grouped: defaultdict[tuple[date, str], list[float]] = defaultdict(list)
    for item in comparisons:
        if item.previous_shares > 0 and item.current_shares > 0:
            grouped[(item.report_period, item.cusip)].append(
                item.current_shares / item.previous_shares
            )

    actions: dict[tuple[date, str], SplitAction] = {}
    for key, ratios in grouped.items():
        if len(ratios) < config.split_min_managers:
            continue
        candidates = []
        for factor in SPLIT_FACTORS:
            support = sum(
                abs(ratio / factor - 1.0) <= config.split_ratio_tolerance
                for ratio in ratios
            )
            fraction = support / len(ratios)
            if fraction >= config.split_min_support:
                distance = statistics.median(
                    abs(ratio / factor - 1.0) for ratio in ratios
                )
                candidates.append((support, -distance, factor))
        if not candidates:
            continue
        candidates.sort(reverse=True)
        if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
            continue
        support, _, factor = candidates[0]
        actions[key] = SplitAction(
            report_period=key[0],
            cusip=key[1],
            factor=factor,
            manager_count=len(ratios),
            support_count=support,
            support_fraction=support / len(ratios),
            median_ratio=statistics.median(ratios),
        )
    return actions


def _relative_conviction(
    *,
    status: str,
    share_change_pct: float | None,
    typical_share_change_pct: float | None,
    previous_weight: float,
    current_weight: float,
    typical_position_weight: float | None,
) -> tuple[float | None, float | None, bool]:
    significance = (
        max(previous_weight, current_weight) / typical_position_weight
        if typical_position_weight is not None
        and typical_position_weight > 0
        else None
    )
    if status in {"INCREASED", "DECREASED"}:
        if (
            share_change_pct is None
            or typical_share_change_pct is None
            or typical_share_change_pct <= 0
            or significance is None
        ):
            return None, significance, False
        direction = 1.0 if status == "INCREASED" else -1.0
        return (
            direction * abs(share_change_pct) / typical_share_change_pct,
            significance,
            significance < 0.25,
        )
    if status in {"NEW", "CLOSED"} and significance is not None:
        return (
            (1.0 if status == "NEW" else -1.0) * significance,
            significance,
            False,
        )
    return None, significance, False


def build_manager_changes(
    comparisons: Sequence[RawComparison],
    split_actions: Mapping[tuple[date, str], SplitAction],
) -> list[ManagerChange]:
    by_manager_period: defaultdict[
        tuple[str, date], list[RawComparison]
    ] = defaultdict(list)
    for item in comparisons:
        by_manager_period[(item.canonical_cik, item.report_period)].append(item)

    changes: list[ManagerChange] = []
    for items in by_manager_period.values():
        previous_weights = [
            item.previous_weight for item in items if item.previous_weight > 0
        ]
        typical_position_weight = (
            statistics.median(previous_weights) if previous_weights else None
        )
        prepared = []
        continuing_changes = []
        for item in items:
            split = split_actions.get((item.report_period, item.cusip))
            comparison_current = (
                item.current_shares / split.factor
                if split is not None
                and item.current_shares > 0
                and item.previous_shares > 0
                else item.current_shares
            )
            if item.previous_shares <= 0 < comparison_current:
                status = "NEW"
            elif comparison_current <= 0 < item.previous_shares:
                status = "CLOSED"
            elif math.isclose(
                comparison_current,
                item.previous_shares,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                status = "UNCHANGED"
            elif comparison_current > item.previous_shares:
                status = "INCREASED"
            else:
                status = "DECREASED"
            share_change_pct = (
                100.0
                * (comparison_current / item.previous_shares - 1.0)
                if item.previous_shares > 0 and comparison_current > 0
                else None
            )
            if (
                status in {"INCREASED", "DECREASED"}
                and share_change_pct is not None
            ):
                continuing_changes.append(abs(share_change_pct))
            prepared.append((item, split, comparison_current, status, share_change_pct))
        typical_share_change_pct = (
            statistics.median(continuing_changes)
            if continuing_changes
            else None
        )
        for item, split, comparison_current, status, share_change_pct in prepared:
            relative, significance, force_routine = _relative_conviction(
                status=status,
                share_change_pct=share_change_pct,
                typical_share_change_pct=typical_share_change_pct,
                previous_weight=item.previous_weight,
                current_weight=item.current_weight,
                typical_position_weight=typical_position_weight,
            )
            changes.append(
                ManagerChange(
                    canonical_cik=item.canonical_cik,
                    manager_name=item.manager_name,
                    report_period=item.report_period,
                    as_of_date=item.as_of_date,
                    cusip=item.cusip,
                    status=status,
                    previous_shares=item.previous_shares,
                    current_shares=item.current_shares,
                    comparison_current_shares=comparison_current,
                    previous_value=item.previous_value,
                    current_value=item.current_value,
                    previous_weight=item.previous_weight,
                    current_weight=item.current_weight,
                    share_change_pct=share_change_pct,
                    typical_share_change_pct=typical_share_change_pct,
                    typical_position_weight=typical_position_weight,
                    position_significance=significance,
                    relative_conviction=relative,
                    force_routine=force_routine,
                    split_factor=split.factor if split is not None else None,
                )
            )
    return changes


def generate_formula_scores(
    changes: Sequence[ManagerChange],
) -> list[FormulaScore]:
    grouped: defaultdict[tuple[date, str], list[ManagerChange]] = defaultdict(list)
    for change in changes:
        grouped[(change.report_period, change.cusip)].append(change)

    scores: list[FormulaScore] = []
    for (period, cusip), items in sorted(grouped.items()):
        meaningful = [
            item
            for item in items
            if item.status != "UNCHANGED"
            and item.relative_conviction is not None
            and not item.force_routine
            and abs(item.relative_conviction) >= 0.25
        ]
        bullish = sum(item.relative_conviction > 0 for item in meaningful)
        bearish = sum(item.relative_conviction < 0 for item in meaningful)
        meaningful_count = bullish + bearish
        breadth = (
            100.0 * (bullish - bearish) / meaningful_count
            if meaningful_count
            else None
        )
        capped = [
            max(-2.0, min(2.0, float(item.relative_conviction)))
            for item in meaningful
        ]
        conviction = (
            100.0 * sum(capped) / sum(abs(value) for value in capped)
            if capped and sum(abs(value) for value in capped) > 0
            else None
        )
        alpha = (
            0.5 * breadth + 0.5 * conviction
            if breadth is not None and conviction is not None
            else None
        )
        values = {
            "alpha_v1_n3": alpha,
            "breadth_v1_n3": breadth,
            "conviction_v1_n3": conviction,
            "alpha_v1_n5": alpha,
        }
        reported_value = sum(
            max(item.previous_value, item.current_value) for item in items
        )
        current_weights = [
            item.current_weight for item in items if item.current_shares > 0
        ]
        current_holder_count = len(current_weights)
        median_current_weight = (
            statistics.median(current_weights)
            if current_weights
            else None
        )
        for formula_id in FORMULA_ORDER:
            minimum = FORMULA_MINIMUM_MANAGERS[formula_id]
            published = (
                meaningful_count >= minimum
                and values[formula_id] is not None
            )
            scores.append(
                FormulaScore(
                    report_period=period,
                    as_of_date=items[0].as_of_date,
                    cusip=cusip,
                    formula_id=formula_id,
                    score=values[formula_id],
                    published=published,
                    meaningful_count=meaningful_count,
                    bullish_count=bullish,
                    bearish_count=bearish,
                    breadth_score=breadth,
                    conviction_score=conviction,
                    comparable_manager_count=len(items),
                    current_holder_count=current_holder_count,
                    median_current_weight=median_current_weight,
                    split_affected=any(
                        item.split_factor is not None for item in items
                    ),
                    reported_value=reported_value,
                )
            )
    return scores


def build_decomposed_signal_rows(
    changes: Sequence[ManagerChange],
    scores: Sequence[FormulaScore],
) -> list[dict]:
    """Build raw institutional feature blocks without using return labels."""
    alpha_by_key = {
        (score.report_period, score.cusip): score
        for score in scores
        if score.formula_id == "alpha_v1_n3"
        and score.score is not None
        and score.meaningful_count >= 1
    }
    changes_by_key: defaultdict[
        tuple[date, str], list[ManagerChange]
    ] = defaultdict(list)
    for change in changes:
        changes_by_key[(change.report_period, change.cusip)].append(change)

    rows: list[dict] = []
    for key, score in sorted(alpha_by_key.items()):
        items = changes_by_key.get(key, [])
        action_strength = {
            "NEW": 0.0,
            "INCREASED": 0.0,
            "DECREASED": 0.0,
            "CLOSED": 0.0,
        }
        action_count = {status: 0 for status in action_strength}
        current_weights = []
        current_values = []
        significance = []
        for item in items:
            if item.current_shares > 0:
                current_weights.append(item.current_weight)
                current_values.append(item.current_value)
            if (
                item.status in action_strength
                and item.relative_conviction is not None
                and not item.force_routine
                and abs(item.relative_conviction) >= 0.25
            ):
                action_count[item.status] += 1
                action_strength[item.status] += min(
                    2.0,
                    abs(float(item.relative_conviction)),
                )
                if item.position_significance is not None:
                    significance.append(item.position_significance)
        held_value = sum(current_values)
        held_shares = [
            value / held_value for value in current_values if held_value > 0
        ]
        rows.append(
            {
                "report_period": score.report_period,
                "as_of_date": score.as_of_date,
                "cusip": score.cusip,
                "alpha_score": float(score.score),
                "breadth_score": score.breadth_score,
                "conviction_score": score.conviction_score,
                "meaningful_count": score.meaningful_count,
                "current_holder_count": score.current_holder_count,
                "median_current_weight": score.median_current_weight,
                "average_current_weight": (
                    statistics.mean(current_weights)
                    if current_weights
                    else None
                ),
                "max_current_weight": (
                    max(current_weights) if current_weights else None
                ),
                "median_position_significance": (
                    statistics.median(significance)
                    if significance
                    else None
                ),
                "held_value_hhi": (
                    sum(value * value for value in held_shares)
                    if held_shares
                    else None
                ),
                "top_holder_value_share": (
                    max(held_shares) if held_shares else None
                ),
                "new_count": action_count["NEW"],
                "increased_count": action_count["INCREASED"],
                "decreased_count": action_count["DECREASED"],
                "closed_count": action_count["CLOSED"],
                "new_strength": action_strength["NEW"],
                "increased_strength": action_strength["INCREASED"],
                "decreased_strength": action_strength["DECREASED"],
                "closed_strength": action_strength["CLOSED"],
                "split_affected": score.split_affected,
            }
        )

    row_by_key = {
        (row["report_period"], row["cusip"]): row for row in rows
    }
    for row in rows:
        prior = row_by_key.get(
            (previous_quarter(row["report_period"]), row["cusip"])
        )
        row["alpha_acceleration"] = (
            max(
                -100.0,
                min(100.0, row["alpha_score"] - prior["alpha_score"]),
            )
            if prior is not None
            else None
        )

    rows_by_cusip: defaultdict[str, list[dict]] = defaultdict(list)
    for row in rows:
        rows_by_cusip[row["cusip"]].append(row)
    for ticker_rows in rows_by_cusip.values():
        streak = 0
        previous_sign = 0
        previous_period = None
        for row in sorted(ticker_rows, key=lambda item: item["report_period"]):
            sign = (
                1
                if row["alpha_score"] >= 25
                else -1
                if row["alpha_score"] <= -25
                else 0
            )
            contiguous = (
                previous_period is not None
                and previous_quarter(row["report_period"]) == previous_period
            )
            if sign == 0:
                streak = 0
            elif contiguous and sign == previous_sign:
                streak += 1
            else:
                streak = 1
            row["alpha_sign_streak"] = streak * sign
            row["persistence_score"] = (
                sign * min(streak, 4) / 4.0 * 100.0
                if sign
                else 0.0
            )
            previous_sign = sign
            previous_period = row["report_period"]
    return rows


def source_fingerprint(
    filings: Sequence[FilingRow],
    holdings_by_accession: Mapping[str, Sequence[HoldingRow]],
) -> str:
    digest = hashlib.sha256()
    for filing in sorted(
        filings,
        key=lambda item: (
            item.canonical_cik,
            item.report_period,
            item.filing_date,
            item.accession_number,
        ),
    ):
        digest.update(repr(filing).encode("utf-8"))
        for holding in holdings_by_accession.get(
            filing.accession_number, ()
        ):
            digest.update(repr(holding).encode("utf-8"))
    return digest.hexdigest()
