# Performance & Benchmarking Filter Plan

**Status:** Research-backed criteria draft; no backend implementation or data
rebuild is authorized yet.

## Objective

Screen for managers whose publicly disclosed 13F holdings could have produced a
followable strategy that outperformed SPY, QQQ, or a more appropriate
style-matched benchmark after the information became public.

The screen evaluates a hypothetical reported-holdings strategy. It does not
estimate the manager's actual fund return because Form 13F omits cash, shorts,
many derivatives, private investments, non-reportable securities, leverage,
fees, and intra-quarter trading.

## Current controls

The production UI currently provides:

1. Performance window: 3 years, 5 years, or full fetched history.
2. Benchmark requirement: show all, beat SPY, beat QQQ, or beat both.
3. Available-estimate requirement: enough continuous history with at least 95%
   identifier-mapping and priced-value coverage.

The current result table also displays estimated CAGR, excess CAGR, maximum
drawdown, and coverage. The data layer already calculates monthly Sharpe,
SPY/QQQ information ratios, and quarterly benchmark beat rates, but those
metrics are not yet user-facing filters.

## Research conclusions

### Public timing must be respected

The SEC permits Form 13F filing up to 45 days after quarter end. A followable
backtest must trade after EDGAR publication, not at the reported quarter end.
AlphaWhales already uses the next SPY trading session after the actual filing
date and processes amendments in public chronology.

### Best ideas contain more signal than the entire disclosed portfolio

Cohen, Polk, and Silli find that managers' highest-conviction positions
outperform their other holdings. Practitioner products similarly use top
positions rather than attempting to reproduce every disclosed holding:

- Solactive GURU generally selects one qualifying top holding per manager.
- AlphaClone historically selected the top five holdings from selected
  managers.
- WhaleWisdom's documented default uses the top ten.
- Goldman Sachs GVIP builds a consensus portfolio from managers' top-ten
  holdings.

This supports a future **portfolio construction** selector for full sleeve,
top five, top ten, or consensus best bets.

### Delayed copycat portfolios can remain followable

Frank, Poterba, Shackelford, and Shoven and later Verbeek and Wang find that
portfolios copied from delayed public holdings can approximate the source
funds, particularly for representative, slower-moving portfolios. This
supports filing-date execution while reinforcing the need for delayed-entry
stress tests, costs, and liquidity controls.

### Endpoint CAGR is not sufficient

A manager can beat a benchmark because of one short period, higher market beta,
a sector cycle, or substantially greater drawdown. The literature recommends
examining persistence, downside risk, and risk-adjusted active return rather
than declaring skill from one start-to-end CAGR.

### Concentration must be benchmark-aware

Active Share and concentration research supports focusing on differentiated
positions, but later evidence shows that raw Active Share can reflect benchmark
or style differences. Managers should ultimately be compared within an
appropriate mandate or style bucket rather than against SPY and QQQ alone.

### Multiple testing can manufacture winners

When thousands of managers and many parameter combinations are tested, some
will appear exceptional by chance. Bootstrap, false-discovery-rate, elevated
t-statistic, and out-of-sample methods are appropriate research controls. They
should become evidence-confidence diagnostics rather than casual slider
thresholds.

## Recommended core filters

These are the highest-value additions without turning the sidebar into a
statistical workstation:

| Filter | Purpose | Possible choices |
|---|---|---|
| Minimum excess CAGR | Require a meaningful annualized margin over the selected benchmark, not merely 0.01 percentage points | 0, 2, 5, or custom percentage points |
| Minimum benchmark beat consistency | Require outperformance across repeated periods rather than only at the endpoints | 50%, 60%, 70% of measured quarters |
| Maximum drawdown | Reject strategies whose return came with unacceptable peak-to-trough loss | 15%, 20%, 30%, 40%, or no limit |
| Multi-window confirmation | Require the result to hold over both 3-year and 5-year views | Off; beat selected benchmark in both |

The benchmark-beat consistency filter can initially use the already-computed
quarterly beat rate. A later version should prefer rolling 12- and 36-month
periods because isolated calendar quarters are noisy.

## Recommended advanced filters

Place these behind an **Advanced performance evidence** disclosure rather than
in the default sidebar:

| Filter | Why it matters |
|---|---|
| Minimum information ratio | Measures average active return relative to tracking error against SPY or QQQ |
| Minimum priced coverage | Lets users demand 95%, 98%, or 100% priced-value coverage |
| Minimum rebalance intervals | Prevents thin histories from ranking beside managers with many observable filing cycles |
| Maximum drawdown versus benchmark | Distinguishes general market crashes from unusually poor manager-sleeve downside |
| Minimum Sharpe or Calmar ratio | Tests whether return compensated for volatility or drawdown |
| Style-matched benchmark requirement | Avoids treating small-cap, growth, value, or sector exposure as manager skill |

## Filters requiring new performance methods

Do not expose these until the underlying calculations exist:

1. **Portfolio construction**
   - Full reported direct-stock sleeve.
   - Top five best bets.
   - Top ten best bets.
   - Cross-manager consensus best bets.
   - Reported-value and equal-weight variants.

2. **Execution-lag robustness**
   - Next session.
   - One trading week after filing.
   - Additional delayed-entry stress cases.

3. **Net-of-cost outperformance**
   - Costs scaled by turnover, spread, price, and average daily dollar volume.
   - Base and stressed-cost results.

4. **Liquidity and capacity**
   - Minimum free-float market capitalization.
   - Minimum average daily dollar volume.
   - Maximum assumed participation in daily volume.
   - Maximum days required to establish or exit the copied position.

5. **Rolling and out-of-sample persistence**
   - Percentage of rolling 12- and 36-month periods beating the benchmark.
   - Worst rolling excess return.
   - Rank managers in one period and evaluate them in a later period.

6. **Factor and style attribution**
   - Market beta.
   - Size, value, momentum, quality, and sector exposures.
   - DGTW or index-based characteristic-adjusted return.

7. **Statistical evidence**
   - Bootstrap confidence intervals.
   - False-discovery-rate correction across managers.
   - Multiple-testing-aware significance thresholds.

## Recommended UI structure

### Performance & Benchmarking

Keep the default group concise:

1. Performance window.
2. Benchmark.
3. Minimum excess CAGR.
4. Minimum benchmark beat consistency.
5. Maximum drawdown.
6. Require an available estimate.

### Advanced performance evidence

Use a collapsed secondary group for information ratio, coverage, minimum
intervals, multi-window confirmation, and future style-matched benchmarks.

No additional performance filter should be implemented until its exact
definition, default, and treatment of unavailable data are approved.

The Investor Screening page currently includes a disabled UI preview of these
core and advanced controls. The preview is for criteria review only and does
not submit values to the screening API.

## Key sources

- SEC, Form 13F instructions:
  https://www.sec.gov/files/form13f.pdf
- SEC, Form 13F FAQ:
  https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/frequently-asked-questions-about-form-13f
- Cohen, Polk, and Silli, *Best Ideas*:
  https://personal.lse.ac.uk/polk/research/bestideas.pdf
- Cremers and Petajisto, *How Active Is Your Fund Manager?*:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=891719
- Cremers and Pareek, *Patient Capital Outperformance*:
  https://doi.org/10.1016/j.jfineco.2016.08.003
- Frazzini, Friedman, and Pomorski, *Deactivating Active Share*:
  https://www.aqr.com/Insights/Research/Journal-Article/Deactivating-Active-Share
- Frank, Poterba, Shackelford, and Shoven, *Copycat Funds*:
  https://www.nber.org/papers/w8653
- Verbeek and Wang, *Better than the Original? The Relative Success of Copycat
  Funds*:
  https://rpc.cfainstitute.org/research/cfa-digest/2013/11/better-than-the-original-the-relative-success-of-copycat-funds-digest-summary
- Kacperczyk, Sialm, and Zheng, *On the Industry Concentration of Actively
  Managed Equity Mutual Funds*:
  https://www.nber.org/papers/w10770
- Daniel, Grinblatt, Titman, and Wermers, *Measuring Mutual Fund Performance
  with Characteristic-Based Benchmarks*:
  https://terpconnect.umd.edu/~wermers/dgtw.pdf
- Carhart, *On Persistence in Mutual Fund Performance*:
  https://www.jstor.org/stable/2329556
- Fama and French, *Luck versus Skill in the Cross-Section of Mutual Fund
  Returns*:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1356021
- Barras, Scaillet, and Wermers, *False Discoveries in Mutual Fund
  Performance*:
  https://terpconnect.umd.edu/~wermers/FDR_published.pdf
- Solactive GURU Index guideline:
  https://www.solactive.com/downloads/Guideline-Solactive-GURU.pdf
- Goldman Sachs Hedge Fund VIP Index methodology:
  https://www.gsam.com/content/dam/gsam/pdfs/us/en/ETF/Goldman-Sachs-Hedge-Fund-VIP-Index-Methodology.pdf
- WhaleWisdom backtesting methodology:
  http://cloudfront.whalewisdomcdn.com/whitepaper/WhaleWisdom_Backtesting.pdf
- 2020 GIPS Standards:
  https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf
