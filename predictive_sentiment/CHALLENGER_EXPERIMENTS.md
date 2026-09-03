# AWFI Challenger Experiments

> Historical challenger report. Its decision to retain AWFI Research v1 applied
> to the frozen experiment recorded here. Research v2 later corrected the
> duplicate 18M/24M score profile without promoting the rejected macro,
> sensitivity, or fundamental challengers.

## Status

```text
Final challenger run: 567c17990edbd453952d
Frozen control: AWFI Research v1
Universe: current-roster top-10 direct stocks
Optimization horizons: 126, 252, 378, and 504 sessions only
Historical decision: retain the v1 control; do not add the macro-sector challenger
```

This report records `AWFI-MSR`: Treasury, DXY, market regime, sector-relative
momentum, and rolling stock sensitivity. It was not allowed to alter AWFI v1
before testing and used disjoint two-stage inner selection with untouched outer
quarters.

## AWFI-MSR: Treasury, DXY, and sector

### Frozen data

The AlphaWhales-controlled macro artifact contains:

| Series | Coverage |
|---|---|
| FRED DGS10 | 2010-01-04 through 2026-07-21 |
| FRED DGS2 | 2010-01-04 through 2026-07-21 |
| FRED DFII10 | 2010-01-04 through 2026-07-21 |
| Yahoo DXY `DX-Y.NYB` | 2010-01-04 through 2026-08-31 |

All source artifacts are frozen with SHA-256 hashes. Sector proxies use the 11
SPDR sector ETFs with adjusted histories backfilled to 2010 or their actual
inception date.

### Macro block

Treasury support uses robust trailing scores for:

- 10-year yield level;
- three-month 10-year-yield change;
- six-month 10-year-yield change.

DXY support uses three- and six-month dollar momentum. Market state uses SPY
12-minus-1-month momentum, six-month momentum, and price versus SMA200.

```text
Rates =
    -0.20 * 10Y level
    -0.35 * 10Y three-month change
    -0.45 * 10Y six-month change

USD =
    -0.40 * DXY three-month momentum
    -0.60 * DXY six-month momentum

Market =
    0.55 * SPY 12-minus-1 momentum
    +0.25 * SPY six-month momentum
    +0.20 * SPY trend

Macro =
    0.40 * Rates
    +0.25 * USD
    +0.35 * Market
```

### Sector-relative block

Historical GICS classifications were unavailable. Each stock therefore receives
a point-in-time statistical sector proxy:

1. End the assignment window 21 sessions before the feature date.
2. Compare the stock with every available sector ETF over the previous 504
   sessions.
3. Require at least 378 paired returns.
4. Select the highest-correlation sector ETF.

```text
Sector =
    0.45 * stock-minus-sector 12-minus-1 momentum rank
    +0.30 * stock-minus-sector six-month momentum rank
    +0.25 * sector-minus-SPY six-month rank
```

### Exposure-aware sensitivity

The optional sensitivity profile estimates trailing stock exposure to:

- SPY;
- sector excess return;
- daily 10-year-yield changes;
- DXY returns.

It uses 504 sessions, at least 378 complete observations, within-window
winsorization, standardized regressors, HAC errors, coefficient reliability
shrinkage, and a condition-number limit.

The sensitivity profile was selected in **zero outer folds**.

### Selection result

The macro/sector selector chose the unchanged AWFI control in:

- every 6-month fold;
- every 12-month fold;
- every 18-month fold;
- four of six 24-month folds.

Only two 24-month folds selected a macro-heavy profile. The aggregate
24-month macro result did not have a positive robustness edge.

| Horizon | Macro challenger accuracy | Balanced accuracy | BUY precision | SELL precision | Bootstrap lower edge |
|---|---:|---:|---:|---:|---:|
| 6 months | 63.8% | 59.1% | 83.6% | 29.4% | +1.7 pp |
| 12 months | 67.0% | 58.0% | 78.4% | 36.9% | +0.7 pp |
| 18 months | 69.6% | 57.7% | 81.2% | 33.3% | +3.4 pp |
| 24 months | 66.0% | 55.8% | 83.5% | 25.0% | -6.1 pp |

These rows largely describe threshold-selected AWFI control performance because
the support profiles rarely won. They are not evidence that Treasury or DXY
improved AWFI.

## AWFI-F: invalidated retrospective attempt

The initial valuation/quality experiment is excluded from valid results.

The available source parquet had already deduplicated later XBRL amendments and
comparative filings over earlier filing vintages. Filtering on
`reported_date <= feature_date` could prevent direct future-value use, but it
could not reconstruct an original fact that had already been discarded.

The initial implementation also used split-and-dividend-adjusted price levels
with contemporaneous share counts. Adjusted closes are valid for returns but
not for historical market capitalization, P/E, or FCF yield.

Therefore:

- no retrospective AWFI-F metrics are accepted;
- no fundamental challenger is active in the research pipeline;
- forward P/E, EPS revisions, and recommendation changes require immutable
  prospective snapshots;
- a valid historical AWFI-F requires accession-level XBRL vintages, aligned
  fiscal periods, explicit units, and unadjusted historical market prices.

## Research interpretation

Treasury yields and DXY are economically relevant but were not reliable
stand-alone additions to this individual-stock index. A common macro value is
identical for every stock in a quarter and primarily shifts thresholds rather
than improving within-quarter ranking.

Academic evidence also cautions against strong universal signs:

- Traditional valuation and rate predictors often weaken materially out of
  sample: [Goyal and Welch (2008)](https://doi.org/10.1093/rfs/hhm014).
- Rate surprises affect equities, but growth information and discount-rate
  shocks can produce different signs:
  [Bernanke and Kuttner (2005)](https://doi.org/10.1111/j.1540-6261.2005.00760.x).
- Foreign-exchange exposure is heterogeneous by firm:
  [Jorion (1990)](https://doi.org/10.1086/296510) and
  [Dominguez and Tesar (2006)](https://doi.org/10.1016/j.jinteco.2005.01.002).
- The yield curve has stronger evidence for forecasting economic activity than
  direct stock returns:
  [Estrella and Hardouvelis (1991)](https://doi.org/10.1111/j.1540-6261.1991.tb02674.x).

## What remains useful

The experiments still produced useful constraints:

1. Keep Treasury and DXY as dashboard context, not AWFI v1 components.
2. Do not use current forward P/E or current analyst estimates in historical
   testing.
3. Start immutable prospective snapshots for forward P/E, EPS revisions, and
   geographic revenue exposure.
4. Rebuild accession-level fiscal earnings and FCF yields before any future
   retrospective ablation.
5. Build downside prediction separately; SELL precision remains the dominant
   weakness across every experiment.
6. Retain AWFI Research v1 as the frozen control for this historical experiment;
   use the separately versioned Research v2 specification for current scoring.

No challenger in this report is promoted into either the historical v1 control
or the current Research v2 score.

## AWFI Action Challenger v1

The standalone `awfi-action-challenger-v1` experiment evaluates the same
126-, 252-, 378-, and 504-session outcomes as AWFI Research v2, but maps scores
to portfolio-state-aware decisions:

```text
not held + sufficiently positive score       -> ENTER
held + strongly positive score               -> INCREASE
held + neutral score                         -> HOLD
held + moderately negative score             -> DECREASE
held + strongly negative score               -> EXIT
```

Pre-signal state uses prior-quarter disclosed holdings rather than the newly
reported quarter's ending holdings. Flat state is accepted only when at least
80% of manager snapshot pairs are valid. `SKIP` remains in entry-opportunity
coverage but is not treated as a transaction.

Each outer quarter uses only earlier outcomes whose exit precedes the test
entry by the five-session embargo. The inner sweep first screens the existing
institutional and technical profiles, then tunes separate enter, increase,
decrease, and exit thresholds. A 25-basis-point one-way cost applies to every
transaction; holding has no transaction cost.

The price/institutional run, `ffa92228cc33f00ebacc`, used parent run
`ebc243d9decb46624a69`. Twelve-month significance uses quarter-balanced hit
rates, three-quarter Newey-West/HAC errors with a conservative finite-sample
t-reference, Holm correction across the five actions, and a four-quarter
moving-block bootstrap. These small-sample statistics are research
sensitivities rather than definitive inference.

| Action | Assigned observations | Eligible quarters | Quarter-balanced hit rate | HAC t-stat | Holm result | Block-bootstrap lower edge |
|---|---:|---:|---:|---:|---|---:|
| ENTER | 18 | 8 | 81.7% | 3.69 | Pass | +16.5 pp |
| INCREASE | 303 | 10 | 60.9% | 2.86 | Pass | +3.5 pp |
| HOLD | 330 | 10 | 52.5% | 0.86 | Fail | -3.3 pp |
| DECREASE | 80 | 10 | 37.3% | -1.36 | Fail | -30.2 pp |
| EXIT | 82 | 10 | 62.5% | 2.28 | Fail | +4.6 pp |

`ENTER` clears the prespecified near-`t > 3` hurdle, but it remains a sparse
18-observation result and is not sufficient to promote the full action policy.
`INCREASE` and `EXIT` are directionally useful but do not clear the t-statistic
hurdle. `DECREASE` is actively counterproductive in this run. The complete
five-action policy is therefore `NOT_PROMOTABLE`.

### SEC-only fundamental tranche

The official SEC Financial Statement Data Sets were imported for all 56
quarters from 2012 through 2025. The lossless bronze foundation retains four
source tables per archive, exact accession and acceptance timing, original
units and contexts, source row numbers, ZIP hashes, and reconciled Parquet row
counts.

Run `8e2732fddc466c643ab8` tested three point-in-time annual blocks:

- quality/profitability;
- conservative investment through negative asset growth; and
- balance-sheet safety/distress.

The cross-sectional factor diagnostics were:

| Factor | 6M IC / t | 12M IC / t | 18M IC / t | 24M IC / t |
|---|---:|---:|---:|---:|
| Quality | +0.018 / 0.53 | +0.061 / 1.59 | +0.086 / 2.08 | +0.105 / 2.43 |
| Conservative investment | -0.010 / -0.33 | -0.010 / -0.34 | -0.038 / -1.06 | -0.044 / -1.03 |
| Safety | +0.025 / 0.79 | +0.035 / 0.78 | +0.039 / 0.67 | +0.052 / 0.86 |
| Frozen fundamental blend | +0.022 / 0.68 | +0.047 / 1.46 | +0.050 / 1.62 | +0.059 / 2.04 |

No fundamental factor clears `t > 3`. Quality strengthens with the holding
horizon and is the only block worth retaining for another long-horizon
experiment. Conservative investment has the wrong sign in this universe and
should be rejected in its current form.

The nested action selector chose `INVESTMENT_20` in 10 of 12 six-month folds,
but the factor's negative rank IC shows that this was threshold/cohort
interaction rather than broad predictive evidence. `SAFETY_20` won all ten
12-month folds and four of eight 18-month folds. At 12 months it produced:

| Action | Assigned | Observation hit rate | Quarter-balanced hit rate | HAC t-stat |
|---|---:|---:|---:|---:|
| ENTER | 12 | 75.0% | 78.6% | 2.95 |
| INCREASE | 263 | 53.2% | 53.8% | 1.01 |
| HOLD | 319 | 56.1% | 55.6% | 2.03 |
| DECREASE | 56 | 50.0% | 48.0% | -0.35 |
| EXIT | 66 | 54.5% | 61.2% | 2.10 |

The SEC-only action result remains `NOT_PROMOTABLE`: entry is available in
only seven eligible quarters, no action clears the full multiple-testing and
`t > 3` requirements, and the current CUSIP/ticker-to-issuer bridge is not
effective-dated. The accession and accounting facts are point-in-time; the
security identity bridge remains a documented research blocker.

Endpoint returns can evaluate whether long exposure or avoided exposure was
helpful. They cannot establish an optimal position size, so `INCREASE` versus
`HOLD` and `DECREASE` versus `EXIT` remain distinct score/state cohorts rather
than proven sizing rules.

### Current AWFI v2 score against five actions

Run `3e6976c1c6e1f339fa92` freezes the exact AWFI Research v2 score formula and
removes every alternative institutional, technical, and fundamental profile.
It compares:

1. the production interpretation, where the fixed horizon threshold produces
   `BUY`, `HOLD`, or `SELL`; and
2. a leakage-safe sweep that uses the same frozen score but selects separate
   `ENTER`, `INCREASE`, `DECREASE`, and `EXIT` thresholds.

At the primary 12-month horizon:

| Action | Production observations / hit rate | Five-action observations / hit rate | Quarter-balanced rate | HAC t-stat |
|---|---:|---:|---:|---:|
| ENTER | 1 / 100.0% | 19 / 57.9% | 75.4% | 2.17 |
| INCREASE | 37 / 56.8% | 247 / 53.0% | 55.4% | 1.11 |
| HOLD | 707 / 54.9% | 384 / 55.7% | 54.1% | 2.97 |
| DECREASE | Not emitted | 78 / 41.0% | 40.3% | -1.15 |
| EXIT | 51 / 52.9% | 86 / 53.5% | 51.6% | 0.61 |

No five-action result clears the full primary `t > 3`, Holm, and block
robustness requirements. `HOLD` comes closest at `t = 2.97`; `ENTER` retains a
positive block-bootstrap lower edge but fails the corrected significance
test. `DECREASE` is again harmful.

The selected 12-month thresholds are unstable: eight distinct threshold sets
appear across ten outer folds. Secondary six- and 18-month entry cohorts look
stronger, but their smaller samples and non-primary status do not justify
changing the production interpretation. The current score is therefore useful
for ranking and selective entry, but does not yet support a stable five-action
execution policy.

### Tested stock universe

All action experiments use the frozen top-ten-per-manager universe from parent
AWFI Research v2 run `ebc243d9decb46624a69`. The universe is retrospective:
it is the union of the current roster's latest top-ten direct-stock holdings,
not a historically reconstituted membership list.

| Stage | Distinct securities |
|---|---:|
| Frozen parent universe | 135 CUSIPs |
| Usable mapped price histories with at least one mature label | 121 tickers |
| 2023-forward outer action-evaluation universe | 117 tickers |
| Current-AWFI sweep with at least one assigned action | 115 tickers |
| SEC-factor sweep with at least one assigned action | 113 tickers |

The exact 117-ticker outer evaluation universe was:

`AAPL`, `ABBV`, `ADI`, `AEIS`, `AER`, `ALAB`, `AMAT`, `AMD`, `AMZN`, `APH`,
`ARM`, `AVGO`, `AXON`, `AXP`, `BAC`, `BE`, `BILI`, `BKNG`, `BN`, `BRKB`,
`BX`, `CAT`, `CB`, `CDLX`, `COST`, `CRCL`, `CRM`, `CROX`, `CRWV`, `CVNA`,
`CVX`, `DASH`, `DHI`, `EDU`, `ELV`, `ENLT`, `ET`, `ETN`, `FCX`, `GABC`,
`GD`, `GE`, `GEV`, `GLW`, `GOOG`, `GOOGL`, `GS`, `HCA`, `HGV`, `HHH`,
`HOOD`, `HUM`, `ICL`, `INTC`, `ISRG`, `ITRN`, `JD`, `JNJ`, `JPM`, `KEYS`,
`KO`, `LITE`, `LLY`, `LRCX`, `LUV`, `LVS`, `MA`, `MCO`, `MDB`, `META`,
`MGA`, `MOD`, `MOH`, `MRK`, `MSFT`, `MU`, `NBIS`, `NEM`, `NG`, `NVDA`,
`NVMI`, `NYAX`, `ONTO`, `ORA`, `OXY`, `PANW`, `PDD`, `PLTR`, `PSKY`, `QSR`,
`RCL`, `RLX`, `RRC`, `SBSW`, `SCCO`, `SE`, `SHOP`, `SLB`, `SNOW`, `SPGI`,
`STX`, `TEAM`, `TEVA`, `TRV`, `TSEM`, `TSLA`, `TSM`, `UBER`, `V`, `VAL`,
`VEON`, `VRT`, `WM`, `WSM`, `WWD`, `XP`, and `YMM`.

`BZUN`, `DDL`, `GRMN`, and `TME` had usable labels somewhere in the historical
sample but no mature 2023-forward outer action observation. `CRCL` and `MOH`
were present as outer opportunities but received no assigned action in the
current-AWFI-only sweep.

Twelve parent-universe CUSIPs lacked a usable ticker/price mapping and did not
enter the return backtest:

`008474108`, `09061G101`, `27579R104`, `36118L106`, `48581R205`, `51819L107`,
`57164Y107`, `84615Q103`, `98955N207`, `G0378L100`, `G2R11M108`, and
`M2573A239`.

The SEC factor foundation produced at least one quality, investment, or safety
feature for 114 ticker/CUSIP identities over the full historical sample. In the
2023-forward factor-action run, `BN`, `CRCL`, and `MGA` lacked a usable SEC
factor block; `MOH` had factor coverage but no assigned action.

Individual stocks do not necessarily contribute to every horizon or quarter.
An observation enters only when ownership state is known, the exact entry and
terminal prices exist, and the horizon has matured without forward filling.

### Actual valuation-method backtest

The earlier SEC-factor tranche did not answer whether the ticker valuation
methods themselves improve AWFI. The corrected experiment reconstructs and
executes the actual formulas in `DataService._compute_valuation_analysis()` at
each historical feature date.

Inputs include:

- 217,396,565 accession-vintage SEC financial-statement rows;
- 454,930 raw, non-dividend-adjusted Yahoo price observations for 121 symbols;
- historical FRED `DAAA` and `DGS10` yields;
- reported shares, EPS, book equity, cash flow, debt, dividends, current
  assets, liabilities, goodwill, and intangible assets known by the feature
  date.

Absolute per-share methods are disabled for foreign issuers, ADRs, ADSs, and
other cases where the SEC statement-share basis cannot be reconciled with the
traded security. Point-in-time SIC classifications distinguish banks,
insurance, REITs, holding companies, utilities, resources, biotechnology,
technology, telecommunications, consumer companies, and other operating
companies before the recommended framework is selected.

The tested methods were:

1. stock-specific recommended valuation anchor;
2. scenario FCF DCF;
3. reverse DCF expectations gap;
4. residual income;
5. two-stage dividend discount;
6. normalized historical P/E;
7. Graham Number;
8. revised Graham growth;
9. conservative Graham growth;
10. NCAV; and
11. tangible book/asset value.

Each method's price-versus-value result is industry-normalized and added to the
unchanged AWFI Research v2 score at 10%, 20%, and 30% support weights. The
action thresholds are then selected through the same purged walk-forward
process. Ranking improvement is measured quarter by quarter against the
unchanged AWFI score on the same covered stocks.

#### Standard coverage gate

Run `579b13499b2c7ba5bebe` requires at least 50% feature availability during
selection. The recommended anchor supports all ten 12-month outer folds.
Normalized historical P/E supports only two folds at this gate.

| Candidate | Weight | Mean 12M rank-IC edge | HAC t-stat | Holm result | Action macro hit rate |
|---|---:|---:|---:|---|---:|
| Recommended anchor | 30% | +0.0246 | 1.49 | Fail | 54.3% |
| Recommended anchor | 20% | +0.0194 | 1.68 | Fail | 56.8% |
| Recommended anchor | 10% | +0.0092 | 1.54 | Fail | 57.2% |
| Normalized P/E | 10% | -0.0090 | -0.63 | Fail | 49.5% |
| Normalized P/E | 20% | -0.0078 | -0.35 | Fail | 51.1% |
| Normalized P/E | 30% | -0.0135 | -0.43 | Fail | 53.6% |

The recommended framework has a small positive ranking effect, but it does not
approach `t > 3`. Normalized P/E produces attractive threshold-selected action
rates in some lower-coverage runs while making the underlying return ranking
worse. This is evidence that action hit rate alone can select misleading
valuation additions.

#### Low-coverage exploration

Run `766cadf8d9670f31ce37` lowers the feature-availability gate to 20% so
method-specific formulas are not silently excluded. These results are
exploratory and ineligible for promotion.

| Candidate | Weight | 12M rank-IC edge | HAC t-stat | Eligible outer folds |
|---|---:|---:|---:|---:|
| Reverse DCF | 30% | +0.0490 | 1.23 | 6 |
| Scenario DCF | 30% | +0.0407 | 1.76 | 10 |
| Recommended anchor | 30% | +0.0246 | 1.49 | 10 |
| Graham Number | 20% | +0.0226 | 0.85 | 7 |
| Revised Graham growth | 30% | +0.0201 | 0.63 | 10 |
| Recommended anchor | 20% | +0.0194 | 1.68 | 10 |
| Dividend discount | 20% | +0.0181 | 0.61 | 10 |
| Conservative Graham growth | 20% | +0.0173 | 0.75 | 10 |
| Residual income | 20% | +0.0051 | 0.22 | 10 |
| Normalized P/E | 20% | -0.0078 | -0.35 | 10 |

Sparse NCAV and fit-qualified tangible-value observations have positive
available-row diagnostics, but neither supports a valid five-action
walk-forward selection under the standard coverage gate.

No valuation method or support weight survives the 12-month `t > 3` and Holm
multiple-testing requirements. Scenario DCF has the strongest complete
low-coverage rank improvement, while reverse DCF has the largest raw edge but
only six eligible folds. The stock-specific recommended anchor remains the
best standard-coverage candidate. The current AWFI formula is not changed.

Forward P/E, PEG, earnings revisions, SOTP, REIT NAV/AFFO, and real-options
values are not retrospectively tested. They require immutable analyst
snapshots or specialized segment, property, reserve, pipeline, and option
inputs that the historical foundation does not contain.

## Portfolio and execution-simulation boundary

AWFI research remains a pandas/DuckDB point-in-time and statistical-validation
pipeline. NautilusTrader is not used for the current experiments and is not a
replacement for cross-sectional feature research, walk-forward selection, or
multiple-testing controls.

If an AWFI candidate eventually passes the promotion gates, NautilusTrader may
consume its frozen action and target-weight artifact to evaluate cash
competition, position sizing, orders, fills, slippage, commissions, turnover,
drawdown, exposure, and portfolio-level return. Signal formulas must not be
recomputed or optimized inside the execution simulator.

See the
[NautilusTrader adoption boundary](../docs/ARCHITECTURE.md#nautilustrader-adoption-boundary)
for the complete responsibility split and adoption criteria.

## Potential AWFI Research v3 candidates from valuation research

The broader valuation review identified several return-prediction factors that
must remain separate from the ticker fair-value calculator. They are candidates
for a future, separately versioned AWFI Research v3 or challenger program:

1. Industry-neutralized value composite using forward E/P, FCF/EV, EBIT/EV,
   and sales/price rather than pure P/B.
2. Quality and profitability using gross profits/assets, ROIC or adjusted ROE,
   accrual quality, balance-sheet safety, and a point-in-time F-Score.
3. Conservative investment or asset-growth signals aligned with the CMA family.
4. Twelve-minus-one price momentum and immutable earnings-revision momentum.
5. Low-volatility and distress overlays rather than treating cheapness as a
   stand-alone buy signal.

These are not approved AWFI components. Any experiment must use accession-level
point-in-time fundamentals, unadjusted historical prices for valuation ratios,
immutable prospective analyst snapshots where required, industry
normalization, walk-forward evaluation, and a multiple-testing hurdle near
`t > 3`. Fair-value estimates belong in ticker decision support; predictive
factor evidence belongs in the versioned AWFI research pipeline.
