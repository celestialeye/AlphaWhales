# Long-Term Best-Bet Screening Plan

**Status:** Draft criteria for review; backend implementation is intentionally
deferred.

## Objective

Identify managers whose highest-commitment stock positions remain meaningful
portfolio weights over time. The screen should answer a position-level
question:

> How many of the manager's current best bets stayed above the selected
> portfolio-weight threshold for the entire selected period?

The model is not intended to infer patience from estimated total portfolio
turnover or from merely retaining a small residual position.

## Proposed position rule

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

## Proposed filters

| Filter | Draft choices | Draft default |
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

## Institutional position limits

An absolute portfolio-weight threshold can understate commitment at
institutions with formal position limits. The cap-aware rule is not finalized.
Before backend implementation, choose whether a position below the absolute
threshold may qualify based on manager-relative evidence such as:

- rank among the manager's largest positions;
- percentage of the manager's historically observed maximum position size; or
- active weight above an appropriate benchmark.

This decision must remain explicit. The backend must not silently infer that a
small position is a best bet.

## UI transition

During criteria review, the Investor Screening page may show a disabled design
preview of the three proposed controls. It must be labeled as a draft and must
not change screening results.

After criteria approval:

1. Replace the annual-turnover filter and one-position durable checkbox.
2. Keep estimated turnover as a diagnostic table column only.
3. Show the qualifying best-bet count and matching positions in results.
4. Update presets and documentation to use the approved defaults.

## Data-layer plan

Screening criteria must query reusable facts rather than trigger full rebuilds.

### Base facts

Store one row per manager, report period, and security with:

- effective accession and report period;
- reported position value;
- total reported non-option 13F value;
- reported portfolio weight;
- direct-stock classification; and
- normalized security identifier.

### Dynamic best-bet query

Compute consecutive threshold-qualified streaks from the base position-quarter
facts. Weight, duration, and required-count changes should be SQL filters over
the same data, not new ingestion or performance runs.

### Independent performance facts

Manager performance must be keyed to the underlying filing and price-data
version, not to a screening preset or filter fingerprint. Criteria changes
must reuse existing manager performance and calculate only genuinely missing
or stale manager records.

## Work intentionally deferred

- No production snapshot schema change.
- No best-bet fact-table build.
- No manager-performance refresh.
- No replacement of current API parameters.
- No final default calibration.

These steps begin only after the cap-aware commitment rule and final filter
defaults are approved.
