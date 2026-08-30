# Investor Performance Methodology Comparison

**Status:** Research and implementation baseline
**Last updated:** August 29, 2026

## Executive conclusion

Form 13F cannot reveal an investment manager's actual fund return. It can
support several different estimates, each answering a different question.

AlphaWhales uses this primary method:

> **Hypothetical disclosure-lagged reported 13F long-sleeve estimate. Not a
> fund or account return.**

The portfolio changes only after a filing becomes public. It enters at the
next regular SPY trading-session close, uses split-and-dividend-adjusted
OpenBB/yfinance prices, and compares the same dates with SPY and QQQ.

This is the most appropriate headline estimate for an investor asking whether
publicly disclosed holdings remained useful after disclosure. It is not a
complete measure of manager skill, and it should be supplemented with
quarter-end research, top-conviction clones, factor attribution, and exact
reported fund returns when those become available.

## Why 13F cannot produce actual fund performance

Form 13F generally omits:

- Cash and short positions.
- Private securities.
- Most bonds, loans, swaps, and commodities.
- Written options and complete derivative economics.
- Leverage and financing.
- Fees, expenses, taxes, and investor-level cash flows.
- Intra-quarter purchases and sales.
- Allocation among the legal funds and accounts aggregated by the filer.

The SEC permits filings up to 45 days after quarter-end. A manager may have
changed a position before an outside investor sees it.

All 13F-derived returns must therefore remain visibly separated from
regulatory-reported, NAV-based, audited, or GIPS performance.

## Method comparison

| Method | Question answered | Timing | Portfolio | Main advantage | Main limitation |
|---|---|---|---|---|---|
| Quarter-end holdings return | How did the disclosed sleeve perform after quarter-end? | Quarter-end | Usually full reported sleeve | Useful stock-selection research | Non-investable look-ahead |
| Forward quarter-end portfolio | Did one quarter's disclosed holdings predict the next quarter? | Quarter-end through next quarter | Full sleeve | Comparable with academic holdings studies | Filing was not yet public |
| Disclosure-lagged clone | What could an outsider earn after disclosure? | Filing publication plus execution lag | Full or selected sleeve | Most investor-actionable 13F estimate | Holdings are stale and incomplete |
| Top-N or best-ideas clone | Did the most-convicted disclosed positions outperform? | Quarter-end or disclosure-lagged | Top 1-50 | Concentrates the likely signal | Selection and weighting choices create model risk |
| Consensus/VIP clone | Did common high-conviction names across managers outperform? | Scheduled post-filing rebalance | Cross-manager consensus | Diversifies manager-specific noise | Measures a consensus product, not a manager |
| DGTW characteristic-adjusted return | Did holdings beat similar size/value/momentum stocks? | Usually quarter-end research | Security-level holdings | Better stock-selection attribution | Not an investable clone unless disclosure lag is added |
| Factor alpha | Did the synthetic sleeve outperform explained factor exposures? | Monthly synthetic returns | Any return series | Controls broad systematic exposures | Model-dependent; not actual fund alpha |
| Return gap | Did the actual fund outperform its previously disclosed holdings? | Actual fund return minus holdings return | Exact fund and disclosed portfolio | Captures trading and unreported activity | Requires correctly mapped actual fund returns |
| Regulatory/NAV performance | What did the exact registered fund return? | Official reporting/NAV dates | Exact series/share class | Closest public actual performance | Often unavailable for hedge funds and aggregated 13F managers |

## Current AlphaWhales method

### Filing chronology

For each manager and report period:

1. An original filing establishes the public portfolio.
2. A `RESTATEMENT` replaces the same-period portfolio and removes earlier
   additive amendments.
3. A `NEW HOLDINGS` amendment adds newly disclosed positions to the latest
   same-period base.
4. An amendment to an older period never rolls the active clone backward after
   a newer report period is public.
5. Events sharing one execution date collapse to the final state known for
   that date.

Configured historical CIKs are consolidated into the current manager identity.

### Execution timing

```text
execution date =
    first SPY trading session strictly after the SEC filing date
```

This is more precise than applying one day-46 or day-47 date to every manager.
Acceptance timestamps would be more exact and remain a planned improvement.

### Sleeve

The default sleeve:

- Includes direct common/ordinary shares and ADR-like equity.
- Excludes puts, calls, ETFs, pooled products, debt, preferred securities,
  warrants, rights, and units.
- Aggregates positions by CUSIP.
- Uses reported quarter-end values as the initial weights.

### Coverage gates

For each filing interval:

```text
mapping coverage =
    mapped eligible reported value / eligible reported value

priced coverage =
    eligible reported value with a usable adjusted price path
    / eligible reported value
```

Both must be at least 95%. Missing securities are not assigned zero returns.
After the gates pass, fully priced positions are renormalized.

### Prices and returns

- CUSIP mapping: EdgarTools packaged CUSIP-to-ticker reference.
- Price provider: OpenBB with the yfinance provider.
- Adjustment: `splits_and_dividends`.
- Entry: next session after filing date.
- Holding behavior: buy and hold until the next executable filing event.
- Benchmarks: SPY and QQQ on the same sessions.
- Default transaction cost: 0 basis points, explicitly disclosed.

Outputs:

- 3Y, 5Y, and full-fetched-window CAGR with the actual start date displayed.
- Excess CAGR versus SPY and QQQ.
- Maximum drawdown.
- Monthly Sharpe ratio assuming a 0% risk-free rate.
- Information ratios versus SPY and QQQ.
- Quarterly benchmark beat rates.
- Mapping and priced-value coverage.
- Explicit unavailable reasons.

The UI defaults to 3Y because it is the minimum supported horizon and has
broader coverage. Five years is the preferred higher-confidence comparison.

## Strengths of the current method

### Point-in-time public timing

Quarter-end backtests assume knowledge that investors did not have. Using each
manager's actual filing date avoids this direct look-ahead.

WhaleWisdom documents a default day-46 rebalance, while published practitioner
research commonly uses day 47. Solactive GURU waits seven business days after
the official filing deadline. AlphaWhales' manager-specific timing is more
precise for a manager-level clone.

### Amendment handling

The method distinguishes replacement restatements from additive confidential
holdings releases. Many simplified backtests use only one final filing version
and can expose later information too early.

### Conservative missing-data policy

A manager interval is unavailable below the 95% mapping or pricing threshold.
This is preferable to silently removing failed securities and presenting the
survivors as the complete portfolio.

### Full-sleeve fidelity

Reported-value weighting answers a clear descriptive question: how did the
publicly disclosed direct-stock sleeve perform after publication?

### Comparable benchmark dates

SPY and QQQ use the same execution and ending sessions as the synthetic sleeve.
Returns are total-return adjusted rather than mixing price and total returns.

## Weaknesses and likely bias

### Current-cohort selection

The first implementation evaluates managers passing today's screening rules
over their earlier history. This can introduce survivorship and selection
bias. It is appropriate for comparing today's candidate list, but not for
claiming that the screening strategy itself would have identified those
managers historically.

**Planned correction:** create point-in-time quarterly manager cohorts and
evaluate the screen only with information available at each formation date.

### Quarter-end weights at disclosure

Reported values are measured at quarter-end but used as target weights weeks
later. Large disclosure-lag price moves can make these weights stale.

**Planned comparator:** multiply disclosed shares by execution-date prices and
reweight the sleeve at public entry. Show both reported-value and
execution-value results.

### Delistings and corporate actions

The current price cache refuses to carry a stale final quote forward. That is
conservative, but an interval can become unavailable instead of realizing
bankruptcy, cash merger proceeds, stock consideration, or another terminal
outcome.

**Planned correction:** maintain a point-in-time security master with CUSIP
history, delisting returns, and merger/spinoff treatment.

### Zero-cost headline

Costs are represented in storage but default to zero. A realistic clone should
apply costs to actual rebalance turnover rather than subtracting one fixed
amount per event.

**Planned scenarios:** gross, 10 bps, 25 bps, and 50 bps applied to one-way
turnover, plus liquidity/ADV constraints.

### SPY and QQQ are diagnostic, not always appropriate

SPY is useful for U.S. large-cap exposure. QQQ is appropriate only for a
Nasdaq-100-like mandate and is not a universal technology benchmark.

**Planned correction:** assign an ex-ante mandate/style benchmark and add
Fama-French five-factor plus momentum attribution. Keep SPY and QQQ visible as
familiar investable comparators.

### Renormalization hides missing-weight outcomes

The 95% gate limits the problem, but renormalizing the remaining 95%-100% still
assumes the missing slice performs like cash removed from the portfolio.

**Planned sensitivity:** report renormalized results alongside missing weight
as cash and conservative missing-security bounds.

## Commercial and practitioner conventions

### WhaleWisdom

Documented defaults include day 46 after quarter-end, quarterly rebalancing,
top-ten selection, and equal weighting, with configurable lag and position
counts. It uses total-return price data.

Useful convention:

- Configurable top-N and rebalance delay.

Limitation:

- Top-N equal weighting measures a clone strategy, not the complete reported
  sleeve.

Source: https://whalewisdom.com/whitepapers/backtesting

### AlphaClone / ALFA

The published product selected five holdings from each of ten scored managers,
used equal weighting and a 5% constituent cap, and reconstituted quarterly.
The live ETF experienced substantial turnover, illustrating the difference
between a frictionless index and investor experience.

Useful convention:

- Explicit portfolio caps and product-level expense disclosure.

Limitation:

- Proprietary manager selection and methodology-history changes complicate
  replication and long-horizon comparison.

Source:
https://www.sec.gov/Archives/edgar/data/1540305/000089418921004898/alphaclonealfasummaryprosp.htm

### Solactive GURU / Global X GURU

The index uses a point-in-time manager pool, waits seven business days after
the official deadline, selects concentrated low-turnover managers, and
generally takes each manager's largest eligible holding. It uses equal
weighting and liquidity/free-float screens.

Useful conventions:

- Point-in-time pool construction.
- Rank buffers to reduce unnecessary turnover.
- Liquidity and free-float eligibility.

Limitation:

- One-stock-per-manager results answer a best-idea product question, not
  manager sleeve performance.

Sources:

- https://www.solactive.com/downloads/Guideline-Solactive-GURU.pdf
- https://assets.globalxetfs.com/funds/documents/guru/Index-Methodology-Summary.pdf

### Goldman Sachs Hedge Fund VIP

The consensus approach selects stocks appearing frequently among hedge-fund
top-ten holdings and equal weights the resulting portfolio.

Useful convention:

- Cross-manager consensus can reduce dependence on one manager.

Limitation:

- It measures a consensus strategy, not an individual manager.

Source:
https://www.sec.gov/Archives/edgar/data/1479026/000119312516753065/d268482d497k.htm

## Recommended AlphaWhales performance views

### 1. Disclosure-Lagged Sleeve

Primary investor view:

- Actual filing date.
- Next-session execution.
- Full direct-stock sleeve.
- Reported-value weighting.
- SPY, QQQ, and assigned benchmark comparisons.

### 2. Execution-Revalued Sleeve

Economic-realism comparator:

- Same timing and constituents.
- Filed share counts valued at execution prices.
- Corporate-action-adjusted share treatment.

### 3. Best Ideas

Clone-strategy comparator:

- Top 5 or top 10.
- Equal weight and execution-value weight variants.
- Rank buffer to reduce turnover.

### 4. Reported-Sleeve Research

Non-investable manager-research view:

- Quarter-end entry.
- Forward holdings return.
- DGTW and factor-adjusted results.

### 5. Reported Fund Performance

Separate evidence module:

- Exact N-PORT series/class returns.
- N-CSR/N-1A performance tables.
- Official NAV and distributions.
- Audited or GIPS evidence with assurance scope.

Never blend reported fund returns with 13F estimates.

## Research basis

- SEC Form 13F FAQ:
  https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f
- Cohen, Polk, and Silli, *Best Ideas*:
  https://doi.org/10.2139/ssrn.1364827
- Wermers, *Mutual Fund Performance: An Empirical Decomposition*,
  *Journal of Finance* (2000).
- Daniel, Grinblatt, Titman, and Wermers, *Measuring Mutual Fund Performance
  with Characteristic-Based Benchmarks*, *Journal of Finance* (1997).
- Kacperczyk, Sialm, and Zheng, *On the Industry Concentration of Actively
  Managed Equity Mutual Funds*, *Journal of Finance* (2005).
- Kacperczyk, Sialm, and Zheng, *Unobserved Actions of Mutual Funds*,
  *Review of Financial Studies* (2008).
- Puckett and Yan, *The Interim Trading Skills of Institutional Investors*,
  *Journal of Finance* (2011).
- Carhart, *On Persistence in Mutual Fund Performance*, *Journal of Finance*
  (1997).
- Fama and French, *A Five-Factor Asset Pricing Model*, *Journal of Financial
  Economics* (2015).
- Lo, *The Statistics of Sharpe Ratios*, *Financial Analysts Journal* (2002).
- Barras, Scaillet, and Wermers, *False Discoveries in Mutual Fund
  Performance*, *Journal of Finance* (2010).
- CFA Institute GIPS Standards:
  https://rpc.cfainstitute.org/gips-standards/standards

## Product decision

Retain AlphaWhales' disclosure-lagged full direct-stock sleeve as the primary
descriptive estimate. It is more faithful to the publicly observable manager
book than quarter-end backtests and most top-N commercial products.

Do not use it alone to declare manager skill. Add point-in-time manager cohorts,
execution-date reweighting, turnover-proportional costs, terminal
corporate-action returns, ex-ante style benchmarks, and factor/DGTW attribution
as separate, versioned methods.

The research-backed user-facing filter roadmap is maintained separately in
[`PERFORMANCE_BENCHMARKING_FILTER_PLAN.md`](PERFORMANCE_BENCHMARKING_FILTER_PLAN.md).

## All-filer expansion readiness

The current embedded performance facts cover 70 managers from the original
production cohort. A relaxed structural screen can return substantially more
managers, but missing performance rows must not be interpreted as failures to
beat a benchmark.

The current CAGR and drawdown are defensible descriptive estimates of the
fully priced, disclosure-lagged reported long-equity sleeve. They are not
manager-skill estimates. Before expanding computation to all filers or ranking
managers by performance, address:

1. Historical security identifiers that retain delisted and acquired issuers
   and prevent ticker-reuse errors.
2. Terminal returns for bankruptcies, cash mergers, stock mergers, and
   spin-offs rather than dropping unavailable terminal paths.
3. An execution-revalued variant using reported shares multiplied by
   execution-date prices instead of quarter-end reported-value weights.
4. Complete-calendar-period and rolling-period statistics rather than partial
   boundary months or quarters.
5. Point-in-time manager cohorts that include managers which later closed,
   shrank, or stopped filing.
6. Turnover- and liquidity-sensitive transaction costs.
7. Style-appropriate benchmarks and factor/characteristic attribution.
8. Multiple-testing controls before identifying top managers across thousands
   of filers.

Engineering safeguards now verify that the selected source database matches
the screening snapshot fingerprint, mark ordinary refresh failures as FAILED,
and group filing events by manager rather than repeatedly scanning the complete
event list. Full-universe execution should still be incremental and resumable
per manager rather than one monolithic run.
