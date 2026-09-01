# Alpha Whale Forward Index

## Status

```text
Name: Alpha Whale Forward Index
Abbreviation: AWFI
Version: Research v2
Predictive protocol: alpha-whale-predictive-v9.6
Verified baseline run: ebc243d9decb46624a69
Status: Research specification requiring walk-forward revalidation; not deployable with real capital
Target horizons: 6, 12, 18, and 24 months
```

AWFI Research v2 is a forward-looking research index derived from Alpha Whale
institutional activity, portfolio conviction, and point-in-time technical
context. It is designed for medium- and long-term investors and is not
optimized for daily, weekly, or one-to-three-month trading.

Research v2 corrects a model defect in Research v1: the 378- and 504-session
scores used the same weights even though the 504-session score alone used a
materially lower decision threshold. Research v2 retains the tested production
profiles for 6, 12, and 18 months, then replaces the duplicate 24-month profile
with the strongest distinct tested profile that the live application can
compute without unavailable support features. Persisted Research v1 scores and
historical results must not be relabeled as Research v2.

AWFI does not replace the existing Alpha Whale Sentiment Index. The original
index remains the pure measure of manager-relative 13F sentiment. AWFI uses
that sentiment as one component in a separate forward-price research score.

## Universe

The AWFI Research v2 universe is frozen from the current roster:

1. Load each canonical manager's latest effective 13F portfolio, preferring
   the application cache when it contains a newer report period than the
   latest imported official quarterly SEC archive.
2. Deduplicate filing rows by CUSIP.
3. Rank direct common stocks by summed reported portfolio weight.
4. Retain the top 10 stocks per manager.
5. If a manager owns fewer than 10 qualifying stocks, retain all of them.
6. Take the union across all current roster managers.

The baseline verified on 2026-09-01 contains:

```text
29 managers
282 manager/top-holding rows
135 unique CUSIPs
```

Eligible instruments include common stock, ordinary shares, ADRs, and ADSs.
ETFs, ETNs, mutual funds, exchange-traded funds, options, preferred stock,
debt, notes, warrants, rights, and units are excluded.

## Signal timing

For report quarter `Q`:

```text
signal as-of date = Q + 45 calendar days
entry session     = first SPY trading session strictly after as-of date
```

Only 13F filings available by the as-of date may affect the signal. Original,
restatement, and `NEW HOLDINGS` amendments follow their filing chronology.

All technical features use adjusted closes strictly before the entry session.
No future price, forward return, later amendment, or current market snapshot
enters an historical score.

## Component normalization

Every AWFI component is bounded to `[-100, +100]`.

Unbounded cross-sectional quantities are converted to centered ranks within
their signal quarter:

```text
centered_rank =
    100 * (average_rank - (n + 1) / 2) / ((n - 1) / 2)
```

This produces a cross-sectional scale centered at zero with endpoints near
`-100` and `+100`.

## Component 1: original Alpha sentiment

### Meaningful breadth

```text
breadth =
    100 * (meaningful bullish managers - meaningful bearish managers)
        / meaningful managers
```

### Relative conviction

For continuing positions:

```text
relative conviction =
    signed share-change percentage
    / manager's median absolute share adjustment
```

For `NEW` and `CLOSED` positions:

```text
relative conviction =
    signed position weight
    / manager's median prior position weight
```

Relative conviction is capped to `[-2, +2]`. Adjustments to positions below
`0.25x` the manager's typical size are routine and contribute zero.

```text
conviction =
    100 * sum(capped signed conviction)
        / sum(abs(capped conviction))

original Alpha =
    0.50 * breadth
  + 0.50 * conviction
```

Research v2 retains indicative observations with at least one meaningful
manager. These low-participation observations are explicitly marked and are
not equivalent to the published three-manager Alpha index.

## Component 2: purchase-led action structure

For each action, use capped absolute relative conviction as its strength:

```text
action strength = min(2, abs(relative conviction))
```

Research v2 uses:

| Action | Coefficient |
|---|---:|
| `NEW` | +1.00 |
| `INCREASED` | +0.75 |
| `DECREASED` | -0.25 |
| `CLOSED` | -0.25 |
| `UNCHANGED` | 0.00 |

The action score is:

```text
numerator =
    1.00 * NEW strength
  + 0.75 * INCREASED strength
  - 0.25 * DECREASED strength
  - 0.25 * CLOSED strength

denominator =
    1.00 * NEW strength
  + 0.75 * INCREASED strength
  + 0.25 * DECREASED strength
  + 0.25 * CLOSED strength

purchase-led action score =
    100 * numerator / denominator
```

A zero denominator produces a neutral action score of zero.

Purchase actions receive more weight because the historical tests consistently
found that reductions and closures were weak predictors of an absolute stock
price decline.

## Component 3: portfolio conviction

For every stock and signal quarter:

```text
portfolio conviction =
    0.50 * centered rank of median manager portfolio weight
  + 0.30 * centered rank of maximum manager portfolio weight
  + 0.20 * centered rank of current holder count
```

This distinguishes meaningful manager positions from incidental holdings while
retaining both typical and maximum conviction.

## Component 4: technical support

```text
technical support =
    0.35 * centered rank of 12-minus-1-month momentum
  + 0.25 * centered rank of six-month momentum
  + 0.25 * centered rank of 52-week-high proximity
  + 0.15 * trend regime
```

Trend regime is:

```text
+100  BULLISH
   0  NEUTRAL
-100  BEARISH
```

The regime votes are:

1. price above SMA200;
2. SMA50 above SMA200;
3. positive six-month momentum.

Technical measurements are explanatory features only. AWFI is evaluated
exclusively against exact 126-, 252-, 378-, and 504-session outcomes.

## Horizon-specific AWFI definitions

The weights are ordered as original Alpha, purchase-led action, portfolio
conviction, and technical support. They come from the stored production
candidate study rather than an imposed monotonic term structure:

1. The 6-month production selection was `ACTION_HEAVY_T15`.
2. The 12-month production selection was `BALANCED_T15`.
3. The 18-month production selection was `BALANCED_T00`.
4. Research v1 also selected `BALANCED_T00` at 24 months, producing a duplicate
   score. Research v2 instead uses the tested `ALPHA_ONLY` candidate, the
   strongest distinct candidate supported by the existing live inputs.

The tested 24-month `CROWDING` candidate had slightly higher production-screen
BUY precision, but it requires acceleration and crowding inputs that the live
application cannot yet reproduce without conflating unavailable history with
neutral values. Research v2 therefore uses the operationally valid
`ALPHA_ONLY` profile rather than inventing missing components.

### AWFI-6M

```text
AWFI-6M =
    0.34 * original Alpha
  + 0.34 * purchase-led action score
  + 0.17 * portfolio conviction
  + 0.15 * technical support

BUY threshold  = +75
SELL threshold = -75
```

### AWFI-12M

```text
AWFI-12M =
    0.4250 * original Alpha
  + 0.2125 * purchase-led action score
  + 0.2125 * portfolio conviction
  + 0.1500 * technical support

BUY threshold  = +75
SELL threshold = -75
```

### AWFI-18M

```text
AWFI-18M =
    0.50 * original Alpha
  + 0.25 * purchase-led action score
  + 0.25 * portfolio conviction

BUY threshold  = +75
SELL threshold = -75
```

### AWFI-24M

```text
AWFI-24M =
    1.00 * original Alpha

BUY threshold  = +25
SELL threshold = -25
```

These thresholds are the boundaries paired with the stored production
candidates. The first three horizons use `+75/-75`. The distinct 24-month
Alpha-only candidate was screened at `+25/-25`; its production-screen BUY
precision was 82.4% across 564 BUY observations with 83.1% coverage. Those
figures are production-training evidence, not an untouched prospective
validation result. The threshold remains a research classification boundary,
not an execution instruction.

## Why decomposed support signals are not in Research v2

The research pipeline can calculate acceleration, crowding, and persistence,
but they are intentionally excluded from the Research v2 score:

- decomposed selection was unstable at six months and sparse at 12 months;
- no selectable decomposed walk-forward model was available at 18 or 24 months
  under the leakage-safe maturity requirements;
- the hypothesized long-run interaction between persistence and crowding has
  not earned promotion; and
- the current live scoring contract supplies original Alpha, action,
  portfolio, and technical components only.

Adding those inputs would require a later model version and an explicit live
contract for three separately normalized `[-100, +100]` values, including
point-in-time missingness behavior. Until that contract and validation exist,
silently treating missing values as zero would mix “neutral” with
“unavailable” and is not acceptable.

## Production candidate evidence used by Research v2

| Horizon | Profile | BUY precision | BUY observations | Balanced accuracy | Coverage |
|---|---|---:|---:|---:|---:|
| 6 months | `ACTION_HEAVY_T15` | 80.0% | 40 | 67.3% | 15.8% |
| 12 months | `BALANCED_T15` | 81.5% | 27 | 66.5% | 11.6% |
| 18 months | `BALANCED_T00` | 83.6% | 55 | 60.7% | 28.3% |
| 24 months | `ALPHA_ONLY` | 82.4% | 564 | 54.0% | 83.1% |

The 6/12/18 figures are the selected production tuning candidates. The
24-month row is a production screening candidate chosen to remove the duplicate
18-month formula while preserving high observed BUY precision and live
computability. These metrics must not be presented as proof of deployability.

## Signal interpretation

For each horizon:

```text
score >= BUY threshold   -> BUY research candidate
score <= -SELL threshold -> SELL/avoid research candidate
otherwise                -> HOLD
```

Because SELL precision remains weak, negative AWFI should initially be
interpreted as:

```text
avoid new exposure
review an existing position
reduce confidence
```

It should not automatically trigger a short position or forced liquidation.

## Legacy Research v1 historical results

The following results belong to the legacy Research v1 formula and run
`eb0e09f716a66c832ad4`. They motivate continued research but do not validate
the corrected Research v2 weights or threshold policy.

### AWFI decomposed score

| Horizon | Accuracy | Balanced accuracy | BUY precision | SELL precision | OOS actions | Bootstrap lower edge |
|---|---:|---:|---:|---:|---:|---:|
| 6 months | 64.0% | 60.6% | 83.7% | 31.9% | 247 | +1.8 pp |
| 12 months | 70.8% | 58.0% | 78.1% | 41.0% | 308 | +1.3 pp |
| 18 months | 53.4% | 55.0% | 74.6% | 33.8% | 131 | -2.6 pp |
| 24 months | 68.0% | 57.3% | 85.3% | 25.0% | 153 | -6.5 pp |

Six- and 12-month AWFI produced a positive quarter-bootstrap lower edge over
their matched balanced baseline. Eighteen- and 24-month AWFI did not.

### Separate technical-confirmation experiment

| Horizon | Accuracy | Balanced accuracy | BUY precision | SELL precision |
|---|---:|---:|---:|---:|
| 6 months | 70.6% | 56.9% | 77.6% | 39.7% |
| 12 months | 72.8% | 55.5% | 78.3% | 38.5% |
| 18 months | 76.5% | 57.5% | 83.3% | 35.7% |
| 24 months | 80.5% | 56.6% | 87.3% | 28.6% |

Raw accuracy is affected by the high long-horizon positive-return base rate.
Balanced accuracy, BUY/SELL precision, coverage, and bootstrap edge must always
be shown with headline accuracy.

## Paper-trading protocol

AWFI Research v2 may enter a new frozen paper-trading cohort only after the
historical pipeline has been rerun and reviewed:

1. Rebuild the top-10 direct-stock universe after each quarterly filing cycle.
2. Freeze all universe members, component definitions, weights, and thresholds
   before observing outcomes.
3. Form the signal 45 days after quarter-end.
4. Record next-session open and close paper entries.
5. Record outcomes at exactly 126, 252, 378, and 504 sessions.
6. Preserve every BUY, HOLD, SELL/avoid, unavailable, and delisted observation.
7. Do not retune Research v2 after paper trading begins.

All deployable signals remain `HOLD` until the research trust gate passes.

## Further experiments

Further work should use separate challenger versions rather than silently
changing AWFI Research v2.

### AWFI-F: value, quality, and safety

Add point-in-time SEC XBRL:

- earnings and free-cash-flow yield;
- gross profitability and ROIC;
- accrual quality and cash conversion;
- leverage, interest coverage, and equity/assets.

This is the highest-priority next experiment for improving 12-24 month
prediction.

### AWFI-S: sector and market-relative support

Add:

- stock momentum relative to its sector;
- sector momentum relative to SPY;
- SPY 12-minus-1-month regime;
- effective-dated sector classification.

### AWFI-R: downside-risk index

Build a separate negative-outcome model rather than forcing bullish and bearish
predictions into one symmetric score. Potential inputs include:

- severe balance-sheet distress;
- negative earnings revisions;
- idiosyncratic and downside volatility;
- crowded ownership combined with deteriorating momentum;
- persistent manager exits.

The current AWFI should remain primarily a BUY/HOLD opportunity index until a
downside model demonstrates materially better SELL precision.

## Limitations and trust blockers

- Today's top-10 holdings are applied retrospectively.
- Current roster membership is not historically point-in-time.
- Indicative one-manager observations differ from the published Alpha index.
- CUSIP mapping is not a fully effective-dated historical security master.
- The actual 13F trade date is unknown within each reported quarter.
- Long-horizon observations overlap and remain cross-sectionally dependent.
- Several per-stock and long-horizon slices are small.
- SELL precision is not adequate for deployment.

These limitations are warnings in research reports and hard blockers for
real-capital deployment.

Data lineage, freshness detection, atomic publication, integrity gates, and
the 2026-09-01 missing-history incident are documented in
[`docs/AWFI_DATA_LINEAGE.md`](../docs/AWFI_DATA_LINEAGE.md).
