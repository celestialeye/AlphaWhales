# Methodology and Financial Definitions

## Scope and limitations

Form 13F is a delayed quarter-end snapshot of reportable long holdings. It
does not disclose transaction dates, execution prices, cost basis, cash,
short positions, or a manager's complete portfolio. Derived metrics are not
actual manager performance or trade proceeds.

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

### Estimated trade weight

For each directional action:

```text
estimated trade weight =
  share change * quarter-average market price
  / manager's prior reported portfolio value
```

This estimates how many percentage points of the manager's portfolio the trade
represented while avoiding price appreciation being mistaken for trading.
Quarter-end price is used only when the quarter-average price is unavailable.
Execution dates and prices remain unknown, so it is still an estimate.

### Manager-relative conviction

Managers have different normal position sizes. A 2-point trade is routine for
a five-position fund but potentially exceptional for a fifty-position fund.

```text
relative conviction =
  estimated trade weight / manager's median prior position weight
```

Classification:

| Absolute relative conviction | Classification |
|---|---|
| Below 0.25x normal position | Routine |
| 0.25x to below 0.75x | Meaningful |
| 0.75x to below 1.50x | High |
| 1.50x or more | Exceptional |

Only meaningful, high, and exceptional trades enter sentiment. Contributions
are capped at 2x a manager's typical position.

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

Estimated net dollar flow is a dollar-weighted cross-check, not a score input.
It `CONFIRMS` when its direction agrees with a non-neutral sentiment regime,
`DIVERGES` when it opposes the regime, and is otherwise `NEUTRAL`.

The manager conviction heatmap displays estimated trade size as a multiple of
each manager's normal position. Contributor lists display the capped multiple
used by the score and the estimated trade weight as secondary context.

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
