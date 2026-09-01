# Investor Screening Model

**Status:** Implemented dynamic baseline, version 2

**Last updated:** August 29, 2026

**Primary use:** Dynamic institutional-investor and high-conviction holding
screening from point-in-time SEC data

> The long-term best-bet model is documented in
> [`docs/LONG_TERM_BEST_BET_SCREENING_PLAN.md`](../docs/LONG_TERM_BEST_BET_SCREENING_PLAN.md).
>
> Research and definitions for the Performance & Benchmarking filters are
> documented in
> [`docs/PERFORMANCE_BENCHMARKING_FILTER_PLAN.md`](../docs/PERFORMANCE_BENCHMARKING_FILTER_PLAN.md).

## Purpose

The screening model identifies institutional managers whose public filings
show a repeatable combination of:

- Reliable reporting history.
- Meaningful direct-company-stock exposure.
- Concentrated, differentiated positions.
- Patient ownership.
- Limited material portfolio rotation.
- Sufficient scale and liquidity for further research.

The model is a research filter, not a manager rating or investment
recommendation. Most investment characteristics are configurable rather than
hard-coded because valid value, growth, activist, quantitative, index, and
multi-strategy processes behave differently.

## Interpretation boundary

Form 13F reports a delayed, manager-level snapshot of certain long securities.
It does not disclose a complete fund portfolio, actual trade dates, cost basis,
cash, short positions, most bonds, private assets, complete derivatives,
leverage, fees, or account-level allocations.

Use these terms:

- **Reported 13F value**, not AUM.
- **Observed holding duration**, not proven continuous ownership.
- **Estimated 13F turnover**, not actual trading turnover.
- **Hypothetical reported-sleeve performance**, not fund performance.
- **Manager-level proxy**, unless a specific fund or series is identified.

## Model structure

The UI must keep three dimensions separate:

1. **Evidence quality:** identity, continuity, timeliness, amendments,
   confidential treatment, mapping coverage, and reconciliation.
2. **Portfolio characteristics:** style, direct-stock exposure, concentration,
   patience, material turnover, liquidity, and crowding.
3. **Hypothetical outcomes:** disclosure-lagged returns, benchmark-relative
   results, drawdowns, and factor-adjusted estimates.

Do not combine these dimensions into one opaque quality score.

## Default preset: Mega High-Conviction

| Criterion | Default | UI range or alternatives |
|---|---:|---|
| Reporting period | Latest broadly complete quarter | No historical as-of UI selector |
| Minimum reported size | Four-quarter median reported 13F value >= $10B | $500M, $1B, $3.5B, $10B, or $50B |
| Minimum current direct-stock positions | >=1 | 1-10+ |
| Filing history | 12 consecutive quarterly filings | Fixed snapshot foundation |
| Direct-company-stock percentage | >=80% of the non-option 13F sleeve | 50%-100% in 5-point increments |
| Latest top-10 concentration | >=40% of the direct-stock sleeve | 20%-100% |
| Concentration persistence | Top-10 concentration >=40% in at least 6 of the last 8 quarters | Configurable X-of-Y |
| Minimum best-bet weight | >=3% of total reported non-option 13F value in every required snapshot | 1%-10% |
| Time continuously held as a best bet | 12 months / 5 snapshots | 6, 12, 18, or 24 months |
| Minimum long-term best bets | >=1 qualifying current stock | 1-10+ |
| Benchmark hurdle | Disabled | Beat SPY, QQQ, or both |
| Minimum excess CAGR | Any positive benchmark margin | +1, +2, +5 percentage points, custom |
| Benchmark beat consistency | Disabled | 50%, 60%, or 70% of measured quarters |
| Maximum drawdown | No limit | 15%, 20%, 30%, or 40% |

### Manager eligibility versus position results

The default manager screen requires at least one current stock that remained at
or above 3% of total reported non-option 13F value in each of the latest five
quarterly snapshots. Weight, duration, and required count remain independently
configurable.

## Current and target evidence-quality gates

The current structural snapshot requires the latest broadly complete period
and 12 available quarterly observations. The 95% identifier-mapping and
priced-value gates apply to disclosure-lagged performance intervals, not to
every structural screening result.

The broader target evidence-quality requirements are:

1. Manager identity is confirmed or mapped with high confidence.
2. Latest expected filing is available.
3. There is no unexplained filing gap.
4. The point-in-time filing version is available for a future selected as-of
   date.
5. Performance intervals map and price at least 95% of included reported
   value.
6. There is no unresolved material reconciliation error.
7. Original filings, restatements, and additive `NEW HOLDINGS` amendments are
   handled according to their actual meaning.

Missing data is `UNKNOWN`; it is never converted to zero or a passing result.

## Size

### Default

Use the median reported 13F value from the latest four filings:

```text
manager size = median(latest four reported 13F portfolio values)
default minimum = $10 billion
```

The legal Form 13F threshold is $100 million. It is a disclosure threshold, not
an investment-quality threshold. Research does not establish $10 billion as a
skill threshold. It is the agreed product default because it reduces the
initial qualified universe to a reviewable number of large managers.

The $500 million research threshold remains available for broad discovery.
Users can lower the default when screening smaller concentrated managers.

Form ADV regulatory AUM may be shown separately when entity identity and dates
are reconciled. It must not be substituted for reported 13F value.

## Filing history

### Default

Require 12 consecutive quarterly observations, representing three years.

This supports:

- Multi-period turnover estimates.
- Concentration and style stability.
- Persistent-position analysis.
- Reduced sensitivity to one amendment or abnormal quarter.

CIK changes and legal reorganizations must be joined into a canonical manager
history before applying this rule. An unexplained missing filing is not an
empty portfolio.

## Direct-company-stock sleeve

The default screen requires at least 80% of the non-option reported sleeve to
be direct-company securities.

Exclude by default:

- Broad and sector ETFs.
- Index products.
- Closed-end and pooled investment products when reliably classified.
- Reportable puts and calls.

Users may include these instruments through advanced controls.

Security classification should ultimately use a maintained point-in-time
security-type reference. Issuer-name and title heuristics are acceptable for
initial calibration but must be labeled as provisional.

## Persistent best-bet eligibility

Current persistent-best-bet eligibility uses reported value as a percentage of
total reported non-option 13F value. A current stock must remain at or above
the selected 1%-10% threshold in every required quarterly snapshot.

The current rule does not require a top-ten rank. Direct-sleeve weight,
position rank, and benchmark-relative active weight are retained or proposed
for future institution-relative conviction modes; they do not determine the
default screen.

## Observed holding duration

### Default

Require the security to appear in the latest and four preceding quarter-end
snapshots. Five observations span at least 12 months.

This is an observed-quarter-end duration. It does not prove that the manager
held the security continuously between filings.

The UI should also provide:

- Six-month discovery mode.
- Twelve-month default.
- Twenty-four-month patient-capital mode.

Routine additions and reductions do not reset duration.

## Routine versus material operations

Portfolio turnover currently compares quarter-to-quarter reported direct-stock
sleeve weights and reported share counts. It does not yet price-drift prior
weights or normalize corporate actions.

### Default action bands

| Classification | Default rule |
|---|---|
| Unchanged | Reported shares unchanged |
| Routine rebalance | Absolute weight change <0.25 percentage points **or** absolute share change <10% |
| Meaningful change | Absolute weight change >=0.25 pp **and** absolute share change >=10% |
| Major conviction change | Absolute weight change >=1.0 pp **and** absolute share change >=20% |
| New or closed | Always recorded; material for conviction analytics when position weight is at least 0.5% |

Small routine changes:

- Contribute zero to meaningful-turnover calculations.
- Do not reset holding duration.
- Do not create a conviction-change alert.

## Estimated turnover

Calculate one-way quarterly turnover from included reported-weight changes:

```text
quarterly turnover =
    0.5 * sum(abs(material weight changes))

annualized turnover =
    average quarterly turnover * 4
```

The factor of 0.5 prevents a sale and corresponding purchase from being
double-counted. This is an estimated material-operation proxy and remains a
diagnostic rather than a default eligibility filter.

Turnover remains a diagnostic result column. It is no longer a manager
eligibility filter because the long-term-investor screen now directly measures
how many current best bets stayed above the selected portfolio-weight threshold
for the full selected duration.

## Concentration

Calculate concentration on the direct-company-stock sleeve.

Default manager rule:

- Latest top-10 weight >=40%.
- The threshold is met in at least 6 of the last 8 quarters.

Also display:

- Top-5 and top-10 weight.
- Maximum position weight.
- Herfindahl-Hirschman Index.
- Effective number of holdings: `1 / HHI`.
- Industry and sector concentration.
- Active Share when benchmark confidence is high.

There is no default upper concentration limit. Top-10 weight above 80% and
single positions above 20% are visible risk flags rather than exclusions.

## Planned multi-label investor-style enrichment

Style is multi-label and confidence-scored:

- Value.
- Growth.
- Quality/profitability.
- Concentrated fundamental.
- Activist.
- Index-like/passive.
- Quantitative/systematic.
- Sector specialist.

Classifications use holdings characteristics, sector-relative valuation,
profitability, growth, size, momentum, active share, turnover, concentration,
and cross-form evidence.

The target UI may show:

- Style confidence.
- Stated versus inferred style.
- Raw versus sector- and size-neutral style.
- Rolling style drift.
- Manager-level versus fund/series-level classification.

## Performance & benchmarking

Performance is not a default hard eligibility filter.

The screening UI defaults to the 3Y window because it is the minimum supported
comparison horizon and currently provides substantially broader manager
coverage. Five-year results remain the higher-confidence view and are required
for any future long-record badge.

### Default mode: disclosure-lagged clone

- Reconstruct point-in-time filing versions in filing-date/accession order.
- Treat originals and restatements as same-period replacement bases and
  `NEW HOLDINGS` amendments as additive to the latest same-period base.
- Do not let a late amendment to an older period roll back a newer active
  report period.
- Enter at the first SPY trading-session close strictly after filing.
- Consolidate events sharing an execution date so the final state wins.
- Value-weight eligible direct-stock, non-option positions aggregated by
  CUSIP.
- Use OpenBB/yfinance daily closes adjusted for splits and dividends.
- Rebalance at each eligible filing event. Cost basis points are present in
  storage keys and default to 0.

Current CUSIP mapping comes only from
`edgar.reference.tickers.cusip_ticker_mapping()`. Symbol formatting may be
normalized for yfinance share-class syntax, but missing CUSIPs are not guessed.
Mapping coverage is mapped eligible reported value divided by total eligible
reported value.

Every interval requires both mapping coverage and fully priced value coverage
of at least 95%. Fully priced means positive adjusted closes at entry and end
plus a usable path on the SPY session calendar. Up to five sessions of forward
fill may bridge isolated security non-trading gaps. There is no backfill before
listing and no extension after the final observed security price. Coverage is
reported before fully priced positions are renormalized.

The calculation end is the latest common SPY/QQQ date on or before the
requested as-of date. Eligible interval NAVs are buy-and-hold paths chained at
event dates. Monthly manager, SPY, and QQQ returns feed 3Y, 5Y, and
full-fetched-window summaries. Monthly Sharpe explicitly assumes a 0% risk-free
rate. Information
ratios and quarterly beat rates are reported separately for SPY and QQQ.
Unavailable intervals and windows retain a reason rather than a zero return.

Performance filters are evaluated directly against these stored per-manager,
per-window facts:

- Benchmark hurdle selects SPY, QQQ, or both.
- Minimum excess CAGR sets the required annualized winning margin.
- Beat consistency sets the minimum quarterly benchmark beat rate.
- Maximum drawdown rejects sleeves with a deeper peak-to-trough loss.

Changing any performance threshold executes a read-only snapshot query. It does
not recalculate returns or refresh market prices.

Generated prices and calculations are refreshed only through:

```powershell
python -m investor_screening.cli refresh-performance --window-years 5
python -m investor_screening.cli refresh-performance --minimum-size-billions 10 --as-of 2026-08-29
python -m investor_screening.cli refresh-performance --force-prices
python -m investor_screening.cli performance-status
```

The omitted size option means all managers in the current immutable screening
snapshot; it does not mean a hard-coded manager count. API and screening reads
never invoke EdgarTools or OpenBB. This methodology description does not claim
that generated live prices currently exist.

### Research mode: reported long sleeve

Hold quarter-end reported positions until the next observation. This mode is
not investable at the assumed quarter-end rebalance date and must be labeled as
a research estimate.

### Track-record requirements

| Metric | Minimum history |
|---|---:|
| Leaderboard inclusion | 12 filings and 36 months |
| Sharpe, Sortino, information ratio | 36 monthly observations |
| Descriptive factor alpha | 36 months |
| Alpha badge | 20 filings and 60 months |
| Persistence assessment | At least 5 years |

### Benchmark policy

1. Use a documented mandate benchmark when available.
2. Otherwise use SPY/S&P 500 Total Return only when the portfolio broadly fits
   a U.S. large-cap mandate.
3. Use QQQ/Nasdaq-100 Total Return only when that mandate is appropriate.
4. Show size, style, sector, and Fama-French five-factor plus momentum
   sensitivity.
5. Lock the benchmark before evaluating the full performance history.

Never select the benchmark that produces the highest historical alpha.

Performance output must be labeled:

> Hypothetical disclosure-lagged reported 13F long-sleeve estimate.
>
> Not a fund or account return.

## Planned liquidity and crowding controls

Default liquidity stress assumptions:

- 10% of average daily volume.
- Ten trading days.
- At least 90% of direct-stock value liquidatable within the horizon.

Controls:

- ADV participation rate.
- Liquidation horizon.
- Free-float ownership.
- Aggregate institutional ownership.
- Number and concentration of co-owners.
- Common-owner overlap.
- N-PORT liquidity and securities-lending fields.

These are stress assumptions, not universal market-impact thresholds.

## Cross-filing enrichment

The foundation ingests these filing families for future enrichment, but they
do not currently enter `/api/screening` eligibility or ranking:

- Forms 3/4/5: insider open-market purchases, sales, grants, and ownership.
- Schedule 13D/G: initial large-holder stakes, activist intent, conversions,
  and material amendments.
- Form 144: intended restricted/control-security sales; not executed sales.
- N-PORT: fund-level positions, derivatives, liquidity, counterparties, and
  securities lending.
- N-CEN: fund structure, advisers, custodians, and operational relationships.
- N-CSR/N-CSRS: expenses, performance disclosures, turnover, and shareholder
  reporting.
- N-PX: proxy-voting and governance behavior.

## UI presets

### Relaxed Scale

- Four-quarter median reported value >=$1B.
- At least 12 filings.
- Direct-company-stock percentage >=60%.
- Top-10 concentration >=30%.
- Concentration threshold met in at least 4 of 8 quarters.
- At least one stock held above 2% for 6 months.

### Mega High-Conviction

Uses the defaults specified in this document.

### Patient Tilt

- Four-quarter median reported value >=$1B.
- At least 12 filings.
- Direct-company-stock percentage >=90%.
- Top-10 concentration >=50%.
- At least two stocks held above 3% for 24 months.
- Manager concentration persistent in all 8 measured quarters.

### Strict Best-Bet

- Four-quarter median reported value >=$10B.
- At least 5 current direct stocks.
- Direct-company-stock percentage >=80%.
- Top-10 concentration >=40% in all 8 measured quarters.
- At least 3 stocks held above 5% of total reported non-option 13F value for
  12 months.
- Full fetched performance history available.
- Estimated CAGR above both SPY and QQQ.

Presets populate controls; they do not lock them.

### Persistent Best-Bet

- Four-quarter median reported value >=$500M.
- At least 3 current direct stocks.
- Direct-company-stock percentage >=50%.
- Top-10 concentration >=50% in all 8 measured quarters.
- At least 3 stocks observed at or above 3% of total reported non-option 13F
  value in each of 5 quarterly snapshots.
- Full fetched performance history selected.
- Estimated CAGR above both SPY and QQQ.

## Initial calibration

The historical Mega High-Conviction calibration was tested against the latest
complete reporting period, March 31, 2026. Before applying the size threshold,
1,726 managers passed the direct-stock, concentration, persistence, and
long-term-best-bet rules.

Size sensitivity:

| Minimum four-quarter median reported value | Managers | Current roster overlap |
|---|---:|---:|
| $500M | 838 | 22 of 26 |
| $1B | 569 | 20 of 26 |
| $3.5B | 223 | 15 of 26 |
| **$10B — default** | **71** | **9 of 26** |
| $50B | 13 | 2 of 26 |

The nine configured managers passing the $10 billion default were Berkshire
Hathaway, TCI Fund Management, Baupost, Pershing Square, Durable Capital,
Fundsmith, Polen Capital, Coatue, and Lone Pine.

The long-term-best-bet calibration uses a 3% overall non-option 13F weight, five
consecutive quarterly snapshots, and at least one qualifying current stock.
Changing weight, duration, or count executes dynamic SQL over the same compact
position-quarter cube.

The direct-company-stock calibration used provisional issuer-name and
security-title heuristics to identify ETFs and pooled products. Production
counts may change after the maintained security-type reference replaces those
heuristics.

## Target UI disclosures

The following remain target disclosures unless they are already returned by
the current compact snapshot and visible in the UI:

- Holdings period.
- Filing publication or acceptance date.
- Reporting lag.
- Original/amended/confidential-release status.
- Manager or fund/series aggregation level.
- Included instrument denominator.
- Security and price mapping coverage.
- Metric formulas and current thresholds.
- Whether the result is reported, inferred, estimated, or unavailable.

## Research basis

The model is grounded primarily in:

- SEC Form 13F rules, instructions, and filing guidance.
- Cohen, Polk, and Silli, *Best Ideas*.
- Kacperczyk, Sialm, and Zheng, *On the Industry Concentration of Actively
  Managed Equity Mutual Funds*, *Journal of Finance* (2005).
- Cremers and Petajisto, *How Active Is Your Fund Manager?*, *Review of
  Financial Studies* (2009).
- Cremers and Pareek, *Patient Capital Outperformance*, *Journal of Financial
  Economics* (2016).
- Puckett and Yan, *The Interim Trading Skills of Institutional Investors*,
  *Journal of Finance* (2011).
- Carhart, *On Persistence in Mutual Fund Performance*, *Journal of Finance*
  (1997).
- Wermers, *Mutual Fund Performance: An Empirical Decomposition*,
  *Journal of Finance* (2000).
- Fama and French, *A Five-Factor Asset Pricing Model*, *Journal of Financial
  Economics* (2015).
- Barras, Scaillet, and Wermers, *False Discoveries in Mutual Fund
  Performance*, *Journal of Finance* (2010).
- Lo, *The Statistics of Sharpe Ratios*, *Financial Analysts Journal* (2002).
- Sensoy, *Performance Evaluation and Self-Designated Benchmark Indexes*,
  *Journal of Financial Economics* (2009).

Exact thresholds are documented operational defaults with varying confidence.
The research supports the direction of differentiation, patience, persistence,
and proper benchmark selection more strongly than any universal numerical
cutoff.
