# Methodology and Financial Definitions

## Scope and limitations

Form 13F is a delayed quarter-end snapshot of reportable long holdings. It
does not disclose transaction dates, execution prices, cost basis, cash,
short positions, or a manager's complete portfolio. Derived metrics are not
actual manager performance or trade proceeds.

## Holdings and ownership

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
