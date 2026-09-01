# Current-Roster Top-10 Holdings Study

> Historical Research v1 report. The measurements below describe run
> `eb0e09f716a66c832ad4` and must not be treated as the current Research v2
> universe or formula. The current lineage baseline is documented in
> [`ALPHA_WHALE_FORWARD_INDEX.md`](ALPHA_WHALE_FORWARD_INDEX.md) and
> [`../docs/AWFI_DATA_LINEAGE.md`](../docs/AWFI_DATA_LINEAGE.md).

## Universe

Run `eb0e09f716a66c832ad4` freezes each canonical current roster manager's
latest direct-stock holdings and ranks them by summed reported portfolio
weight.

Rules:

- Deduplicate a manager's filing rows by CUSIP before ranking.
- Rank by reported portfolio weight, then reported value.
- Retain at most 10 direct common stocks per manager.
- Managers with fewer than 10 qualifying stocks retain every qualifying stock.
- Accept common stock, ordinary shares, ADRs, and ADSs.
- Exclude ETFs, ETNs, exchange-traded funds, mutual funds, options, preferred
  stock, notes, debt, warrants, rights, and units.
- Freeze the resulting CUSIP union for every historical test period.

The frozen universe contains:

```text
29 managers
281 manager/top-holding rows
142 unique CUSIPs and mapped market symbols
```

Keywise Capital has eight qualifying current stocks, Voyager Global has eight,
and Sosin has five. Their portfolios were not padded with lower-quality
instruments.

## Price backfill

All 142 stock symbols plus SPY were refreshed through the existing
split-and-dividend-adjusted OpenBB/yfinance cache.

```text
requested start: 2010-01-01
requested end:   2026-08-31
READY symbols:   143 of 143
```

Newer securities retain their true IPO/listing start dates. The backfill does
not invent prices before listing, fill missing terminal outcomes, or move an
old signal to the first available market date.

## Research participation

This study retains indicative Alpha observations with at least one meaningful
manager. It does not require five current holders or the production
three-manager publication floor. Small samples are retained and reported as
warnings rather than silently removed.

The resulting label coverage is:

| Horizon | Stock-quarter labels | Unique CUSIPs | Filing quarters |
|---|---:|---:|---:|
| 6 months | 2,975 | 134 | 50 |
| 12 months | 2,759 | 126 | 48 |
| 18 months | 2,557 | 124 | 46 |
| 24 months | 2,342 | 119 | 44 |

## Walk-forward results

### Decomposed institutional score

| Horizon | Accuracy | Balanced accuracy | BUY precision | SELL precision | OOS actions | Bootstrap lower edge |
|---|---:|---:|---:|---:|---:|---:|
| 6 months | 64.0% | 60.6% | 83.7% | 31.9% | 247 | +1.8 pp |
| 12 months | 70.8% | 58.0% | 78.1% | 41.0% | 308 | +1.3 pp |
| 18 months | 53.4% | 55.0% | 74.6% | 33.8% | 131 | -2.6 pp |
| 24 months | 68.0% | 57.3% | 85.3% | 25.0% | 153 | -6.5 pp |

The decomposed score improves balanced accuracy over its matched baseline with
a positive quarter-bootstrap lower bound at six and twelve months. The
12-month rank IC is positive but its confidence interval marginally crosses
zero. The 18- and 24-month decomposed results do not show a robust baseline
edge.

### Separate technical-confirmation experiment

| Horizon | Accuracy | Balanced accuracy | BUY precision | SELL precision | OOS actions | Bootstrap lower edge |
|---|---:|---:|---:|---:|---:|---:|
| 6 months | 70.6% | 56.9% | 77.6% | 39.7% | 340 | +2.7 pp |
| 12 months | 72.8% | 55.5% | 78.3% | 38.5% | 279 | -1.4 pp |
| 18 months | 76.5% | 57.5% | 83.3% | 35.7% | 196 | -0.8 pp |
| 24 months | 80.5% | 56.6% | 87.3% | 28.6% | 179 | -0.4 pp |

Raw long-horizon accuracy is high because most selected stocks rose. Balanced
accuracy and SELL precision show that the model still struggles to identify
absolute declines.

## Parameter behavior

The most stable decomposed selections were:

- Six months: `ACTION_HEAVY`, purchase-led actions, 15% technical weight,
  usually `+75/-75`.
- Twelve months: `BALANCED`, usually 15% technical weight, with thresholds
  varying between `+50/-50` and `+75/-100`.
- Eighteen months: `BALANCED`, usually `+75/-75`.
- Twenty-four months: `BALANCED`, usually `+75/-75` or `+75/-100`.

Purchase-led action weighting dominated:

```text
NEW         +1.00
INCREASED   +0.75
DECREASED   -0.25
CLOSED      -0.25
```

## Warnings

- The universe is today's top holdings applied retrospectively and therefore
  contains selection hindsight.
- Indicative one-manager observations are not equivalent to the published
  three-manager Alpha index.
- Current CUSIP mapping is not an effective-dated historical security master.
- Several security-level and long-horizon slices remain small.
- High raw accuracy must be compared with the high always-positive base rate.
- SELL precision remains inadequate at every horizon.

These warnings block deployment but do not remove observations from the
research output. All deployable `decision_signal` values remain `HOLD`.
