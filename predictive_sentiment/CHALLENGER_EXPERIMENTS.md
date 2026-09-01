# AWFI Challenger Experiments

## Status

```text
Final challenger run: 567c17990edbd453952d
Frozen control: AWFI Research v1
Universe: current-roster top-10 direct stocks
Optimization horizons: 126, 252, 378, and 504 sessions only
Decision: retain AWFI v1; do not add the macro-sector challenger
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
6. Retain AWFI Research v1 as the frozen paper-trading control.

No challenger in this report is promoted into AWFI v1.
