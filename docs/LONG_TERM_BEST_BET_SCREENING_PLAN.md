# Long-Term Best-Bet Screening Plan

**Status:** Implemented dynamic baseline.

## Objective

Identify managers whose highest-commitment stock positions remain meaningful
portfolio weights over time. The screen should answer a position-level
question:

> How many of the manager's current best bets stayed above the selected
> portfolio-weight threshold for the entire selected period?

The model is not intended to infer patience from estimated total portfolio
turnover or from merely retaining a small residual position.

## Implemented position rule

For each direct-company stock in each quarterly filing:

```text
reported portfolio weight =
    reported position value
    / total reported non-option 13F value
```

A current stock qualifies as a persistent best bet when:

1. Its current reported portfolio weight is at least the selected best-bet
   threshold.
2. Its reported portfolio weight remained at or above that same threshold in
   every required quarterly snapshot.
3. The filing sequence is complete for the selected period.

Falling below the threshold, disappearing from a filing, or being completely
closed breaks the streak. Share increases and reductions do not break the
streak when the position remains above the selected weight.

## Current filters

| Filter | Choices | Default |
|---|---|---:|
| Minimum best-bet weight | 1%-10%, in 0.5-point increments | 3% |
| Time continuously held as a best bet | 6, 12, 18, or 24 months | 12 months |
| Minimum long-term best bets | 1-10+ qualifying current stocks | 1 |

Duration maps to consecutive quarterly snapshots:

| Duration | Required snapshots |
|---|---:|
| 6 months | 3 |
| 12 months | 5 |
| 18 months | 7 |
| 24 months | 9 |

Example: a manager currently holds five stocks above 3%. With a 12-month
duration selected, each stock is evaluated across the latest five quarterly
snapshots. If three stayed at or above 3% in all five snapshots, the manager
has three 12-month best bets.

## Future institutional position-limit support

An absolute portfolio-weight threshold can understate commitment at
institutions with formal position limits. The current baseline intentionally
uses only the explicit absolute threshold. A future cap-aware mode may allow a
position below that threshold to qualify based on manager-relative evidence:

- rank among the manager's largest positions;
- percentage of the manager's historically observed maximum position size; or
- active weight above an appropriate benchmark.

The position-quarter cube already retains direct-sleeve weight and rank so this
can be added without rebuilding the historical 13F foundation. Any cap-aware
rule must remain explicit; the backend must not silently infer that a small
position is a best bet.

## Current UI

The Investor Screening page exposes all three controls. Estimated turnover is
retained as a diagnostic result column but is no longer a default eligibility
filter. Results show the qualifying best-bet count and up to three matching
position chips.

## Data-layer implementation

Screening criteria must query reusable facts rather than trigger full rebuilds.

### Base facts

`manager_position_quarters` stores one row per manager, report period, and
security with:

- effective accession and report period;
- reported position value;
- total reported non-option 13F value;
- reported portfolio weight;
- normalized security identifier;
- direct-sleeve weight and position rank for future cap-aware rules.

The compact snapshot stores direct-stock rows with overall non-option weight of
at least 1%, plus each quarter's top ten positions regardless of weight, across
the latest 20 snapshots. The floor matches the minimum selectable best-bet
threshold, while rank retention preserves evidence needed for a future
institution-relative position-limit rule.

### Dynamic best-bet query

Consecutive threshold-qualified streaks are computed from the position-quarter
facts at query time. Weight, duration, and required-count changes are SQL
filters over the same 1,809,508-row cube, not new ingestion or performance
runs.

### Independent performance facts

Manager performance remains keyed to the underlying filing and price-data
version, not to the best-bet thresholds. Criteria changes reuse existing
manager performance. Future performance refreshes use a broad size/history
universe rather than the currently selected screening style.

## Future enhancements

- Optional institution-relative position-limit logic.
- UI options beyond 24 months using the existing 20-quarter cube.
- Security-identity continuity through mergers and identifier changes.
- A lower selectable weight only after rebuilding the cube with a lower
  pruning floor.
