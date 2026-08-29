# Investor Screening Model

**Status:** Implemented baseline, version 1

**Last updated:** August 28, 2026

**Primary use:** Dynamic institutional-investor and high-conviction holding
screening from point-in-time SEC data

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

## Default preset: Established High-Conviction

| Criterion | Default | UI range or alternatives |
|---|---:|---|
| Reporting period | Latest broadly complete quarter | Historical as-of selector |
| Minimum reported size | Four-quarter median reported 13F value >= $10B | $100M-$5T; presets at $500M, $1B, $3.5B, $10B, $50B |
| Filing history | 12 consecutive quarterly filings | 4-40 quarters |
| Direct-company-stock percentage | >=80% of the non-option 13F sleeve | 60%, 70%, 80%, 90%, custom |
| Latest top-10 concentration | >=40% of the direct-stock sleeve | 20%-100% |
| Concentration persistence | Top-10 concentration >=40% in at least 6 of the last 8 quarters | Configurable X-of-Y |
| Estimated annualized turnover | <=100% after routine changes are removed | 25%, 50%, 75%, 100%, 150%, 200% |
| Conviction position | >=3% of direct-stock sleeve and top 10 | 1%-15%; top 1/3/5/10/20 |
| Benchmark-aware conviction | >=2% absolute weight and >=+2 percentage points active weight, top 10 | Optional when benchmark confidence is high |
| Observed holding duration | 5 consecutive snapshots spanning at least 12 months | 3, 6, 12, 18, 24+ months |
| Position persistence | Conviction threshold in at least 3 of the last 4 filings, including latest | Configurable X-of-Y |
| Performance filter | Disabled | Optional estimated-performance filter |

### Manager eligibility versus position results

Manager eligibility and position eligibility are separate:

- A manager may pass without currently having a 12-month conviction position.
- A position may appear in shorter-duration discovery results without changing
  the manager's long-term classification.
- Position duration and conviction are result columns and optional filters, not
  mandatory manager-quality judgments.

## Required evidence-quality gates

These are the appropriate hard exclusions:

1. Manager identity is confirmed or mapped with high confidence.
2. Latest expected filing is available.
3. There is no unexplained filing gap.
4. The point-in-time filing version is available for the selected as-of date.
5. At least 95% of included reported value is mapped to a security and price.
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

## Conviction

### Without a reliable benchmark

A default conviction position must:

- Represent at least 3% of the direct-stock sleeve.
- Rank among the manager's top 10 positions.

### With a reliable benchmark

Prefer active weight:

```text
active weight = manager portfolio weight - benchmark weight
```

Require:

- Absolute weight >=2%.
- Active weight >=+2 percentage points.
- Top-10 portfolio rank.

Broad ETFs and options are excluded from the default conviction denominator.

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

Portfolio actions are classified using split-adjusted shares and
price-drift-adjusted portfolio weights.

```text
drift-adjusted previous weight =
    previous shares * current-period price
    / drifted previous portfolio value

weight change =
    current portfolio weight - drift-adjusted previous weight
```

### Default action bands

| Classification | Default rule |
|---|---|
| Unchanged | Split-adjusted shares unchanged |
| Routine rebalance | Absolute weight change <0.25 percentage points **or** absolute share change <10% |
| Meaningful change | Absolute weight change >=0.25 pp **and** absolute share change >=10% |
| Major conviction change | Absolute weight change >=1.0 pp **and** absolute share change >=20% |
| New or closed | Always recorded; material for conviction analytics when position weight is at least 0.5% |

Corporate actions and identifier changes must be normalized before action
classification. Small routine changes:

- Contribute zero to meaningful-turnover calculations.
- Do not reset holding duration.
- Do not create a conviction-change alert.

The four materiality thresholds are advanced UI controls.

## Estimated turnover

Calculate one-way quarterly turnover from material drift-adjusted weight
changes:

```text
quarterly turnover =
    0.5 * sum(abs(material weight changes))

annualized turnover =
    average quarterly turnover * 4
```

The factor of 0.5 prevents a sale and corresponding purchase from being
double-counted.

The Established High-Conviction preset allows annualized turnover up to 100%.
Alternative strategy defaults:

| Strategy preset | Turnover ceiling |
|---|---:|
| Patient/value | 50% |
| Established active | 100% |
| Growth/active | 150% |
| Quantitative/systematic | No universal ceiling; assess factor stability instead |

Turnover is a patience and replicability measure, not a universal manager-skill
measure. A high-turnover manager may be skilled but difficult to follow through
a filing that can arrive 45 days after quarter-end.

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

## Investor style

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

The UI must show:

- Style confidence.
- Stated versus inferred style.
- Raw versus sector- and size-neutral style.
- Rolling style drift.
- Manager-level versus fund/series-level classification.

## Hypothetical performance

Performance is not a default hard eligibility filter.

### Default mode: disclosure-lagged clone

- Use point-in-time filing versions.
- Enter at the next regular-session close after SEC acceptance.
- Value-weight positions.
- Use total returns with dividends.
- Rebalance at each eligible filing date.
- Apply 0, 10, 25, and 50-basis-point turnover-cost scenarios.

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

> Estimated performance of the manager's reported 13F long sleeve. This is not
> the return of any fund or account.

## Liquidity and crowding

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

Cross-form signals supplement the 13F screen:

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
- Turnover <=150%.
- Durable position not required.

### Established High-Conviction

Uses the defaults specified in this document.

### Patient Tilt

- Four-quarter median reported value >=$1B.
- At least 12 filings.
- Direct-company-stock percentage >=90%.
- Top-10 concentration >=50%.
- Turnover <=50%.
- Position conviction >=3% and top 10.
- Observed duration >=12 months.
- Manager concentration persistent in all 8 measured quarters.

Presets populate controls; they do not lock them.

## Initial calibration

The revised Established High-Conviction rules were tested against the latest
complete reporting period, March 31, 2026. Before applying the size threshold,
748 managers passed the direct-stock, concentration, persistence, and turnover
rules.

Size sensitivity:

| Minimum four-quarter median reported value | Managers | Current roster overlap |
|---|---:|---:|
| $500M | 748 | 21 of 26 |
| $1B | 515 | 19 of 26 |
| $3.5B | 206 | 14 of 26 |
| **$10B — default** | **70** | **9 of 26** |
| $50B | 13 | 2 of 26 |

The nine configured managers passing the $10 billion default were Berkshire
Hathaway, TCI Fund Management, Baupost, Pershing Square, Durable Capital,
Fundsmith, Polen Capital, Coatue, and Lone Pine.

The calibration turnover values apply the agreed 0.25-percentage-point and 10%
share-change routine-operation deadbands. Full historical price-drift
adjustment remains pending, so the counts are still calibration results rather
than frozen production claims.

The direct-company-stock calibration used provisional issuer-name and
security-title heuristics to identify ETFs and pooled products. Production
counts may change after the maintained security-type reference replaces those
heuristics.

## Required UI disclosures

Every result page must display:

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
