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
- At least one current stock that remained at or above 3% of total reported
  non-option 13F value in each of the latest five quarterly snapshots.

Routine adjustments below either 0.25 portfolio-weight percentage points or a
10% share-count change do not contribute to the material-turnover proxy.
New/closed positions below 0.5% are retained as reported facts but excluded
from material conviction turnover. Turnover remains a diagnostic rather than a
default eligibility filter. Full price-drift adjustment remains a documented
future refinement, so turnover is labeled an estimate.

## Holdings and ownership

- Legacy 13F filings may expose market values in dollars, thousands of dollars,
  or with a 1,000x amendment error. Values are normalized using the median
  implied price (`Value / Shares`), and portfolio totals and weights are rebuilt
  from normalized holdings rather than trusting inconsistent summary totals.
- For each report date, originals and explicit restatements replace the
  portfolio base in filing chronology. Subsequent `NEW HOLDINGS` amendments
  supplement that base. Effective accessions are retained; unknown amendment
  types and missing bases are errors rather than guessed complete portfolios.
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

### QoQ Signal Desk

The default Signals view uses selected-period comparison rows and keeps individual
manager actions separate from AWFI research, ticker consensus, and existing Big
Bets concentration rankings. Buy and increase previews appear together by
default; the Sector x Action matrix focuses on buy (`NEW`), increase, decrease,
hold (`UNCHANGED`), or exit (`CLOSED`). Exact ticker search takes precedence; otherwise
search matches ticker, issuer, manager, or fund by case-insensitive substring.
The matrix has sector rows, five action columns, and an All sectors total row.
Each cell shows notable / reported position counts for the current search and
significance thresholds. Sector rows partition the total counts, including
Unclassified. Clicking a cell selects both sector and action for the detail
lists; clicking a sector name preserves the current action (including the
combined buy + increase view). The matrix keeps other sectors visible for
comparison instead of filtering itself to the selected sector. Reset filters
restores all sectors. Column headers, the total row, and sector names remain
visible while scrolling within the matrix.
Rows reuse the ticker page's cached company-profile sector and industry, first
from memory and then disk, before falling back to the bundled
`data/reference/full_universe.csv` snapshot. Classification metadata remains
usable after the quote cache expires; this does not make expired prices usable.
Each ticker is resolved once per QoQ aggregation, without market-data requests.
The reference snapshot is loaded once per process. SEC share-class ticker aliases
use the same mapping as market lookups. Only missing coverage in both sources,
including funds without a corporate sector, is Unclassified; classifications
are not inferred from issuer names or holdings.
These are cached-profile or reference classifications, not point-in-time historical
sectors; they do not enter sentiment or AWFI calculations. A selected sector
with no positions is retained with an empty result when switching filing periods.
Matrix counts show qualifying positions against all reported positions of that
action in the search results, not unique manager counts. Adjustable defaults are:

- Meaningful initiations: `NEW` positions with at least 2% ending portfolio weight.
- Major build-ups: `INCREASED` positions with at least 2% ending weight, at least
  50% share growth, and at least 1 percentage point of portfolio-weight growth.
- Meaningful reductions: `DECREASED` positions with at least 2% previous weight
  and a share cut of at least 25%. A fall in weight or reported value is not
  required because appreciation can offset a share reduction.
- Hold: `UNCHANGED` reported shares with at least 2% ending weight. This is not
  an AWFI HOLD recommendation or evidence of an intention to continue holding.
- Meaningful exits: `CLOSED` positions with at least 2% previous weight. Current
  weight is zero, so filtering on ending weight would hide these exits.

Initiations default to descending reported position value; build-ups default to
descending portfolio-weight growth. Decreases rank largest share cuts first;
holds rank ending weight; exits rank previous weight. Active positions also
support ending-weight, manager-relative-size, and reported-value ordering;
reductions and exits support previous-weight and previous-value ordering.
Each category previews five
positions and exposes the complete qualifying list with a count and View all.
Missing required metrics cannot qualify; missing ranking metrics sort last.
Missing filings cannot imply a hold or exit. The Changes log intentionally
excludes unchanged positions and does not apply the Signal Desk's significance
thresholds.

Consensus, existing concentration, reported-dollar rankings, and market charts
live in Context rather than competing with the default action view. AWFI has its
own research view and 6-24 month horizon selector. All views share the selected
filing period. Changing discovery filters does not request new filing data.

Manager-relative size is current reported portfolio weight divided by the
manager's median positive prior-quarter position weight, or unavailable without
a positive baseline. This uncapped descriptive ratio is not the sentiment score.
Reported dollars are position values, not cash spent; weight changes also reflect
security prices and changes elsewhere in the reported portfolio. These are
discovery heuristics, not validated return predictors, and do not modify
sentiment, AWFI, or their participation thresholds.

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

The ticker hero displays the low price, the trading date on which that low was
observed, and the percentage above it. Positive arithmetic differences are
green and negative differences are red. This is current market context, not a
manager return, cost basis, or valuation conclusion.

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

Investor Activity History uses each selected filing-period snapshot's QoQ
comparison and excludes `UNCHANGED` rows. It reports signed share change,
share-change percentage, portfolio-weight change in percentage points, and
reported-value change. The period header shows the actual selected filing date
for that manager.

Investor Portfolio History lists the latest 20 report periods in descending
order. Reported portfolio value remains the normalized 13F total for that
period. Top holdings are ranked by reported portfolio weight, then reported
value, and capped at 20 securities per period.

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

### Method selection

Valuation is selected by company economics rather than averaging every
available formula. The service classifies the company from its sector,
industry, growth, payout, and statement coverage, then recommends a framework:

- Financial institutions: residual income with P/B and ROE context.
- Regulated utilities and established payers: two-stage dividend discount with
  a cash-flow cross-check.
- High-growth operating companies: scenario FCF DCF plus reverse DCF.
- Cyclical and commodity companies: midcycle FCF DCF plus normalized earnings.
- Conglomerates: SOTP is identified as the structurally correct method, but no
  SOTP value is produced without segment-level financials.
- REITs: NAV and price/AFFO are identified as the structurally correct methods,
  but no property NAV or AFFO value is produced from generic summary data.
- Other operating companies: scenario FCF DCF plus normalized historical P/E.

The primary fair-value estimate comes from the first valid calculated method in
the recommended framework. Incompatible values are not blended into a
composite. The UI opens with a compact `Decision Set` containing the methods
selected for the company's profile, then provides separate `Intrinsic`,
`Relative`, `Graham`, `Asset & Special`, and `All` tabs. Methods that are not
applicable or require specialized data remain visible in their category. Every
method card has its own methodology, applicability, and limitation tooltip.

The method-agreement bar counts only models that produce a positive per-share
value and classifies their individual assessments as undervalued, neutral, or
overvalued. It is a disagreement diagnostic, not a vote, composite fair value,
or replacement for the recommended primary method.

Method cards separate internal data readiness from the visible `Method read`.
The UI must never present `AVAILABLE` as an investment signal:

- Fair-value methods show `MARGIN OF SAFETY`, `NEAR FAIR VALUE`, or
  `ABOVE METHOD VALUE`.
- Reverse DCF compares modeled and price-implied growth and shows whether
  expectations look low, aligned, or demanding.
- PEG below 1.0 receives a growth-adjusted attractive read; 1.0-2.0 is
  balanced; above 2.0 is rich. The text still requires confirmation that
  growth and business quality are durable.
- P/E, P/B, or enterprise multiples without an adequate comparison set show
  `PEER BENCHMARK NEEDED`, not cheap or expensive.
- Inapplicable and unsupported methods show `NOT A FIT` or `MORE DATA NEEDED`.

### Scenario FCF DCF and reverse DCF

Historical free cash flow to the firm is approximated from cash flow after
restoring after-tax interest:

```text
FCFF = operating cash flow
       + interest expense * (1 - tax rate)
       - absolute capital expenditure
```

The base cash flow blends the latest observation with the recent full-series
median, including negative years; cyclical companies use the full recent
median. FCF growth is calculated only across an uninterrupted positive history.
Growth also uses available revenue history with profile-sensitive caps. Bear,
base, and bull cases vary growth, WACC, and terminal growth. Enterprise value
is converted to equity value by subtracting net debt and dividing by shares
outstanding.

Reverse DCF solves for the starting FCF growth rate, fading toward the terminal
rate across five years, that makes the same model equal the current market
price. It exposes market expectations and does not create a second fair-value
estimate.

### Residual income

Residual income starts with book value per share and adds discounted future
earnings above the cost of equity:

```text
residual income = (ROE - cost of equity) * opening book value
```

Excess ROE fades across the forecast and terminal periods. This is the primary
calculated method for banks and other financial institutions, where debt is an
operating input and generic enterprise DCF is misleading. It is down-weighted
for intangible-heavy businesses.

### Dividend discount

The two-stage DDM forecasts five years of dividends with growth fading toward a
2.5% terminal rate. Dividend growth uses at least three annual per-share payment
observations and sustainable growth from ROE and retention, capped at 6%. It is
shown only when the reported payout is positive and no more than 85%.

Absolute per-share methods are disabled for foreign issuers or non-USD quotes
when the summary provider data cannot prove that financial-statement currency,
ordinary shares, and the traded ADR/share basis are aligned. Per-share
dimensionless metrics such as P/E or PEG may still be shown with an explicit
data-basis warning; EV multiples are withheld because their numerator and
denominator may use different currencies.

### Historical multiple and defensive asset methods

For each positive-EPS fiscal year with at least 200 trading days:

```text
fiscal-year P/E =
  average close during the 365 days ending at fiscal year-end
  / diluted annual EPS
```

Values above 100x are rejected. At least three observations are required, and
the displayed multiple is their median. The normalized P/E value applies this
multiple to trailing EPS.

The Graham family is displayed as separate methods rather than a single
combined value.

Graham Number:

```text
Graham Number = sqrt(22.5 * trailing EPS * book value per share)
```

Revised Graham growth formula:

```text
value = trailing EPS * (8.5 + 2 * capped EPS growth)
        * 4.4 / current Aaa corporate bond yield
```

The AlphaWhales conservative Graham adaptation reduces both the base multiple
and the growth coefficient:

```text
value = trailing EPS * (7 + capped EPS growth)
        * (20-year average Aaa yield / current Aaa yield)
```

NCAV / net-net is shown only when current assets exceed all liabilities:

```text
NCAV per share = (current assets - total liabilities) / shares outstanding
Graham net-net buy target = NCAV per share * 2/3
```

Relative PEG, forward P/E, EV/EBITDA, EV/revenue, and P/B values are context,
not peer-derived fair values. A relative fair value requires a clean,
fundamentals-adjusted industry peer set and is not fabricated from a single
company's multiples.

The catalog also displays tangible book/asset value, SOTP, REIT NAV/AFFO, and
contingent-claim or real-options valuation. SOTP, property NAV/AFFO, and option
values remain visibly unavailable until the required segment, property,
reserve, pipeline, volatility, debt-maturity, or exercise data exists.

```text
purchase price = primary fair value * 0.80
```

Classification:

- `UNDERVALUED`: at or below the 20% safety price.
- `NEUTRAL`: above the safety price but no more than 10% above fair value.
- `OVERVALUED`: more than 10% above fair value.
- `UNAVAILABLE`: no valid calculated anchor for the recommended framework.

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

## Alpha Whale Forward Index

AWFI is a separate forward-price research model built from:

- Alpha Whale manager-relative sentiment;
- purchase-led action structure;
- cross-sectional portfolio conviction; and
- point-in-time technical support.

It produces distinct 6-, 12-, 18-, and 24-month scores. The current Research
v2 profiles and thresholds come from the stored production candidate study;
they are not interchangeable with the Alpha Whale Sentiment regime.

Historical scores use the standardized quarter-end-plus-45-day information
cutoff and prices strictly before the following entry session. The current
application period keeps filing inputs fixed while technical inputs advance
through the latest cached market session.

The complete formulas, evidence, interpretation limits, and trust blockers are
documented in
[`predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md`](../predictive_sentiment/ALPHA_WHALE_FORWARD_INDEX.md).
Data lineage and publication controls are documented in
[`AWFI_DATA_LINEAGE.md`](AWFI_DATA_LINEAGE.md).

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
