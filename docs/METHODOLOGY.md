# Methodology and Financial Definitions

## Scope and limitations

Form 13F is a delayed quarter-end snapshot of reportable long holdings. It
does not disclose transaction dates, execution prices, cost basis, cash,
short positions, or a manager's complete portfolio. Derived metrics are not
actual manager performance or trade proceeds.

Universe-wide manager screening uses the separately documented model in
`investor_screening/SCREENING_MODEL.md`. Its default is a configurable research
preset, not a claim that $10 billion, 40% top-10 concentration, or any other
cutoff is a universal predictor of manager skill.

The default screening result requires:

- A four-quarter median reported 13F value of at least $10 billion.
- Twelve consecutive quarterly observations.
- At least 80% direct-company-stock exposure in the non-option sleeve.
- At least 40% in the top ten direct-stock positions.
- The concentration threshold in at least six of eight quarters.
- Estimated annualized material turnover no greater than 100%.

Routine adjustments below either 0.25 portfolio-weight percentage points or a
10% share-count change do not contribute to the material-turnover proxy.
New/closed positions below 0.5% are retained as reported facts but excluded
from material conviction turnover. Full price-drift adjustment remains a
documented future refinement, so turnover is labeled an estimate.

## Holdings and ownership

- Legacy 13F filings may expose market values in dollars, thousands of dollars,
  or with a 1,000x amendment error. Values are normalized using the median
  implied price (`Value / Shares`), and portfolio totals and weights are rebuilt
  from normalized holdings rather than trusting inconsistent summary totals.
- When original and amended filings share a report date, the application keeps
  candidates with the largest holdings count and selects the latest complete
  submission.
- A holder is one configured manager with a positive reported position.
- Holder counts use funds, not holding rows.
- Mean weight is calculated among holders only.
- Median weight is the preferred measure of a typical holder position.
- Conviction rankings require five holders and rank by median weight.
- Big Bets rank the largest single-manager weight and measure concentration.

## QoQ actions

Actions are determined by reported share count:

| Status | Definition |
|---|---|
| `NEW` | Previous shares were zero and current shares are positive |
| `INCREASED` | Current shares exceed previous shares |
| `DECREASED` | Shares declined but remain positive |
| `CLOSED` | Previous shares were positive and current shares are zero |
| `UNCHANGED` | Share count did not change |

A decreased position can have a positive reported-value change when price
appreciation offsets the lower share count. The UI therefore separates share
action from reported value change.

Net owner change is `new holders - closed holders` among comparable filings.

## Estimated institutional flow

```text
estimated flow = share change * quarter-end price
gross inflow = sum of positive estimated flows
gross outflow = absolute sum of negative estimated flows
net flow = gross inflow - gross outflow
```

This is not actual cash flow because execution dates and prices are unknown.

## Latest 52-week-low context

The ticker hero and overview value signal use the latest cached OpenBB market
close and trailing 52-week low, even when the user is viewing an older 13F
filing period:

```text
percentage above 52-week low =
  100 * (latest close / latest trailing 52-week low - 1)
```

The ticker hero displays both the low price and the percentage above it.
Proximity is colored green at 10% or less, yellow above 10% through 25%, and
orange above 25%. This is current market context, not a manager return, cost
basis, or valuation conclusion.

Investor portfolio tables use the same cached market context without issuing
one live request per holding:

```text
implied reported price = filing-period reported value / reported shares

since-report price movement =
  100 * (latest cached price / implied reported price - 1)
```

The implied reported price is a quarter-end filing value, not an execution
price. Since-report movement is security-price context, not manager return or
gain/loss. Rows without cached market coverage display unavailable values
rather than triggering a live fan-out.

## Estimated Alpha Whale price

The Alpha Whale price is an estimated weighted-average basis for shares still
held by tracked managers.

1. Positive additions use the quarter's average daily close.
2. Reductions remove basis proportionally.
3. Fully closed positions reset their modeled basis.
4. Boundary holdings initialize at the oldest quarter's average close.

```text
estimated whale basis = remaining modeled cost / currently held shares
```

It is most uncertain for positions acquired before the 20-quarter window.

## Valuation

### Historical P/E

For each positive-EPS fiscal year with at least 200 trading days:

```text
fiscal-year P/E =
  average close during the 365 days ending at fiscal year-end
  / diluted annual EPS
```

Values above 100x are rejected. At least three observations are required, and
the displayed multiple is their median.

### Graham models

The composite can use:

```text
Graham Number = sqrt(22.5 * trailing EPS * book value per share)

Conservative Graham =
  trailing EPS * (7 + capped growth)
  * (20-year average Aaa yield / current Aaa yield)

Normalized P/E value = trailing EPS * historical median P/E
```

EPS growth is capped at 15%. At least two models are required. Composite fair
value is the median of the available models.

```text
purchase price = composite fair value * 0.80
```

Classification:

- `UNDERVALUED`: at or below the 20% safety price.
- `NEUTRAL`: above the safety price but no more than 10% above fair value.
- `OVERVALUED`: more than 10% above fair value.
- `UNAVAILABLE`: fewer than two valid models.

## Technical timing

Value, trend, and entry timing remain separate:

- Trend: price versus 200-day SMA, 50-day versus 200-day SMA, and six-month
  momentum.
- Momentum: RSI(14) and one-, three-, six-, and twelve-month returns.
- Entry timing: RSI(2), RSI(14), price versus 50-day SMA, and the 200-day gate.
- Risk: 63-trading-day annualized volatility.

An RSI(2) dip is only favorable above the 200-day trend. Overbought or extended
conditions produce a wait-for-pullback state.

## Illustrative All Weather-style sizing

Sizing is educational and non-personalized. It does not model existing
positions, correlations, taxes, liquidity, horizon, or risk tolerance.

- Assumed equity sleeve: 30% of total portfolio.
- Maximum single stock: 5% of the equity sleeve.
- Valuation, trend, estimated flow, and volatility determine a range.
- Missing data, overvaluation, or a bearish trend produces zero.
- Annualized volatility of 30% or more reduces the range.

## Alpha Whale Sentiment

The ticker sentiment view separates raw share activity from manager-relative
trade conviction while keeping estimated dollar flow separate.

### Raw share activity

```text
buy actions = NEW + INCREASED
sell actions = DECREASED + CLOSED

raw activity breadth =
  100 * (buy actions - sell actions)
  / (buy actions + sell actions)
```

Raw activity is displayed as context but does not directly determine the
sentiment regime.

### Manager-relative conviction

No external price series, quarter-average trade-price estimate, or cost-basis
estimate enters the sentiment score. Reported position weights for `NEW` and
`CLOSED` holdings still reflect the filing's quarter-end market values.

For continuing holdings, the application compares the reported share-change
percentage with that manager's median absolute share adjustment across all
continuing positions in the same quarter:

```text
relative conviction =
  signed share-change percentage
  / manager's median absolute share-change percentage
```

For `NEW` and `CLOSED` positions there is no prior-share denominator. These
actions use position significance:

```text
relative conviction =
  signed new-or-closed position weight
  / manager's median prior position weight
```

This distinguishes a tiny exploratory entry or cleanup exit from a new or
closed position that is large relative to the manager's normal holding.

Classification:

| Absolute relative conviction | Classification |
|---|---|
| Below 0.25x normal operation | Routine |
| 0.25x to below 0.75x | Meaningful |
| 0.75x to below 1.50x | High |
| 1.50x or more | Exceptional |

Only meaningful, high, and exceptional trades enter sentiment. Contributions
are capped at 2x a manager's normal operation. Continuing-position adjustments
are also treated as routine when the position itself is below 0.25x the
manager's normal holding size, preventing tiny starter positions from driving
the regime.

```text
meaningful breadth =
  100 * (meaningful bullish trades - meaningful bearish trades)
  / total meaningful trades

relative conviction score =
  100 * (positive capped conviction - negative capped conviction)
  / total absolute capped conviction
```

### Composite and regimes

At least three meaningful managers are required:

```text
sentiment = 50% meaningful breadth + 50% relative conviction score
```

| Score | Regime |
|---|---|
| 60 to 100 | Strongly bullish |
| 25 to below 60 | Bullish |
| Above -25 to below 25 | Neutral |
| Above -60 to -25 | Bearish |
| -100 to -60 | Strongly bearish |
| Fewer than three meaningful managers | Low participation |

The chart still displays an `INDICATIVE TREND` when one or two meaningful
managers provide a calculable score. These values are connected with a dashed
line and marked as low participation. The thicker validated sentiment trace
appears only when at least three managers qualify. The dashed line may bridge
quarters with no meaningful trades for visual continuity, but those quarters
have no marker or hover score and are never assigned a neutral zero.

The sentiment chart keeps all diagnostic series visible by default:

- `INDICATIVE TREND` connects calculable scores, including low-participation
  quarters.
- `VALIDATED SENTIMENT` emphasizes quarters meeting the three-manager floor.
- `MEANINGFUL BREADTH` shows the directional balance of qualifying managers.
- `RELATIVE CONVICTION` shows the balance of capped manager-relative trade
  magnitudes.
- `RAW ACTIVITY` shows unfiltered buy-versus-sell action counts.
- `LOW PARTICIPATION` marks quarters that are calculable but not validated.
- `STOCK PRICE` overlays daily OpenBB closes on a separate right-hand dollar
  axis. The series starts at the oldest of the 20 report periods and continues
  through the latest available close.
- `EXPECTED 13F DEADLINE` places a cyan marker and vertical guide exactly 45
  calendar days after each report-period end.

The component traces use subdued opacity so they explain the score without
competing visually with the validated regime.

The 45-day marker is a standardized availability assumption, not an actual
filing timestamp and not a claim that every manager files on that date.
Managers may file earlier, and calendar or amendment timing may differ. The
marker exists to separate the quarter-end holdings measurement from the
approximate date when an observer could expect the filings to be public.
Its hover detail uses the latest trading close on or before the 45-day mark.

The daily price overlay and expected-deadline markers support visual
leading/lagging analysis. They do not enter any sentiment formula, and visual
co-movement does not establish predictive correlation or causation.

Estimated net dollar flow is a dollar-weighted cross-check, not a score input.
It `CONFIRMS` when its direction agrees with a non-neutral sentiment regime,
`DIVERGES` when it opposes the regime, and is otherwise `NEUTRAL`.

The sentiment summary displays exact `NEW`, `INCREASED`, `DECREASED`, and
`CLOSED` counts rather than combining them into ambiguous buy/sell wording.
Gross inflow and gross outflow are shown inside the Dollar Flow Cross-Check
metric. Each metric tooltip contains its formula and interpretation; the
section-level tooltip only explains the current ticker's composite arithmetic
and regime threshold.

The manager conviction heatmap displays reported share adjustment versus
normal adjustment for continuing positions, and position size versus normal
position size for new and closed positions. Contributor lists show the capped
multiple used by the score plus the underlying reported percentage.

## Pair-trading research

Pair signals are hypothesis-tier and have no live forward record. Economic
peers are restricted to the same industry using the local semantic universe.

Candidate requirements:

- Five years of dividend-adjusted prices.
- Bidirectional Engle-Granger tests.
- Bonferroni correction across candidates and both directions.
- A 70/30 out-of-sample ADF persistence test.
- At least one stable non-overlapping sub-window.
- Positive OLS price hedge ratio.
- A 10-120 day spread half-life.

`READY` additionally requires an absolute spread z-score of at least 1.5.
Execution instructions are hidden for `WAIT` and `NO VALID PAIR`.

Two expressions are described:

1. Long the cheap stock and short the expensive stock using the OLS price
   hedge ratio.
2. Long the cheap stock and buy a put on the expensive stock.

The stock-plus-put expression is not market-neutral and adds option premium,
theta, implied-volatility, strike-selection, and expiry risk.

## Investor Screening

Universe-wide manager and holding screening uses a separate methodology so
evidence quality, manager characteristics, and hypothetical outcomes are not
collapsed into one score. Definitions, default thresholds, materiality rules,
and interpretation boundaries are documented in
`investor_screening/SCREENING_MODEL.md`.
