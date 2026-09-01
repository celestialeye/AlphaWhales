# Support-Signal Research Plan

## Expanded threshold study

Run `532413ca5876b9d6b6f7` tests the unchanged `alpha_v1_n3` score on
high-conviction direct stocks over 126, 252, 378, and 504 trading sessions.
BUY and SELL thresholds are selected independently from:

```text
25, 50, 75, 100
```

The walk-forward evaluator runs two separate experiment families:

1. `SENTIMENT_ONLY`
2. `TECHNICAL_COMBINED`

Every outer quarter selects thresholds using only earlier labels whose exit
precedes the test entry and embargo. HOLD observations remain outside action
accuracy and are included in coverage.

| Horizon | Experiment | OOS actions | Accuracy | Balanced accuracy | BUY precision | SELL precision | Majority baseline |
|---|---|---:|---:|---:|---:|---:|---:|
| 6 months | Sentiment only | 378 | 51.1% | 50.4% | 67.0% | 33.7% | 66.7% |
| 6 months | Technical combined | 239 | 59.8% | 53.3% | 69.9% | 37.0% | 67.8% |
| 12 months | Sentiment only | 305 | 45.9% | 56.5% | 81.9% | 29.9% | 73.8% |
| 12 months | Technical combined | 149 | 61.1% | 55.9% | 71.0% | 40.8% | 67.1% |
| 18 months | Sentiment only | 200 | 53.0% | 57.3% | 79.6% | 32.1% | 73.0% |
| 18 months | Technical combined | 32 | 59.4% | 57.3% | 65.0% | 50.0% | 59.4% |
| 24 months | Sentiment only | 54 | 48.1% | 55.4% | 81.0% | 27.3% | 75.9% |
| 24 months | Technical combined | 0 | n/a | n/a | n/a | n/a | n/a |

The 18- and 24-month technical samples are not mature enough for inference.
Only one 18-month technical outer quarter selected a candidate, and no
24-month technical quarter had enough training support.

### Threshold conclusion

There is no stable accuracy-maximizing asymmetric threshold.

- Six-month sentiment-only folds selected several incompatible pairs, led by
  `+50/-50` and `+25/-75`.
- Twelve-month sentiment-only folds often selected `+100/-25`. This increased
  BUY precision by issuing very few BUY signals but left the weak SELL side
  active, reducing overall accuracy.
- Technical-combined folds overwhelmingly returned to `+25/-25`.
- The production research candidate selected from properly matured history is
  `+25/-25` with technical confirmation at every horizon.

Higher thresholds can manufacture attractive precision on a small subset, but
they did not produce stable out-of-sample accuracy. Threshold selection should
therefore remain an experiment, not a production optimization.

## Evidence-backed additional signals

### Priority 0: next retrospective experiments

| Signal family | Hypothesis | Point-in-time construction | Role |
|---|---|---|---|
| Value | Cheaper profitable companies have higher long-horizon expected returns. | Sector/date percentiles of earnings yield, FCF yield, and EV/EBITDA using statements filed before the signal date and prior-session price. | Continuous support score, not a universal P/E gate. |
| Profitability and quality | High gross profitability, ROIC, cash conversion, and improving margins support 12-24 month returns. | SEC XBRL facts selected by filing date; sector-normalized gross profit/assets, ROIC, FCF/assets, accruals, and margin trend. | Continuous support score. |
| Balance-sheet safety | Severe leverage and distress make bullish institutional signals less reliable. | Net debt/assets, interest coverage, cash/assets, equity/assets, and working-capital trend from filed statements. | Continuous feature plus a severe-distress BUY veto. |
| Momentum and 52-week-high location | Medium-term winners and stocks near their 52-week high exhibit return continuation. | Six-month return, 12-minus-1-month return, price/SMA200, SMA50/SMA200, and distance from the trailing 52-week high, all ending before entry. | Technical score; replace reliance on distance above the 52-week low. |
| Market and sector regime | Stock momentum and absolute direction depend on broad-market and sector state. | SPY 12-minus-1 momentum and SMA200 state; sector ETF six-month return relative to SPY; stock return relative to sector. | Interaction feature rather than a hard market exclusion. |
| Institutional structure | Within-manager concentration can be informative while extreme cross-manager crowding can create fragility. | Manager position rank and weight; holder count; held-value HHI; top-holder share; breadth acceleration. | Separate conviction and crowding features. |
| Manager skill | Prior-only manager skill can weight contributions without treating every manager equally. | Shrunk trailing information ratio, quarterly beat rate, and best-idea hit rate using only outcomes completed before the signal date. Cap multipliers at 0.75-1.25. | Alpha contribution weight. |
| Action asymmetry | Purchases may contain more information than reductions and closures. | Preserve `NEW`, `INCREASED`, `DECREASED`, and `CLOSED` separately. First fixed hypothesis: `+1.00`, `+0.75`, `-0.25`, `-0.25`. | Last-resort Alpha-definition experiment. |
| Acceleration and persistence | Fresh acceleration may help at 6-12 months, while prolonged crowded accumulation can reverse at 18-24 months. | Change in weighted bullish breadth, score acceleration, new-holder count, streak length, and crowding percentile. | Horizon-specific institutional feature. |

Supporting research:

- Value: [Fama and French (1992)](https://doi.org/10.1111/j.1540-6261.1992.tb04398.x)
- Profitability: [Novy-Marx (2013)](https://doi.org/10.1016/j.jfineco.2013.01.003)
- Profitability and investment: [Fama and French (2015)](https://doi.org/10.1016/j.jfineco.2014.10.010)
- Accrual quality: [Sloan (1996)](https://www.jstor.org/stable/248290)
- Financial strength: [Piotroski (2000)](https://www.jstor.org/stable/2672906)
- Distress: [Campbell, Hilscher, and Szilagyi (2008)](https://doi.org/10.1111/j.1540-6261.2008.01416.x)
- Momentum: [Jegadeesh and Titman (1993)](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x)
- 52-week-high location: [George and Hwang (2004)](https://doi.org/10.1111/j.1540-6261.2004.00695.x)
- Market-state interaction: [Cooper, Gutierrez, and Hameed (2004)](https://doi.org/10.1111/j.1540-6261.2004.00665.x)
- Institutional purchases: [Chen, Jegadeesh, and Wermers (2000)](https://doi.org/10.2307/2676208)
- Herding and six-month returns: [Wermers (1999)](https://doi.org/10.1111/0022-1082.00126)
- Long-run institutional reversal: [Dasgupta, Prat, and Verardo (2011)](https://doi.org/10.1111/j.1540-6261.2010.01644.x)
- Manager concentration: [Kacperczyk, Sialm, and Zheng (2005)](https://doi.org/10.1111/j.1540-6261.2005.00785.x)
- Crowding fragility: [Greenwood and Thesmar (2011)](https://doi.org/10.1016/j.jfineco.2011.06.003)

### Priority 1: prospective collection

- Analyst EPS revision breadth over 30 and 90 days.
- Clustered Form 4 open-market insider purchases over 30 and 90 days.
- Idiosyncratic and downside volatility.

These signals are promising, but current historical snapshots are insufficient
for an honest long-horizon retrospective test. Start immutable daily/event
collection now and evaluate only after adequate forward history accumulates.

### Priority 2: defer

- Short-interest changes: evidence is strongest at approximately one-to-three
  month horizons, and current refresh infrastructure needs replacement.
- News or social sentiment: historical provenance and reproducibility are weak.
- Current OpenBB ratios: useful for live inference, but not historical research
  unless the original snapshot date is preserved.

## Three preregistered combined experiments

### 1. Value-quality-safety

```text
fundamental_block =
    0.35 * value
  + 0.40 * profitability_quality
  + 0.25 * balance_sheet_safety

combined_score =
    0.75 * existing_alpha
  + 0.25 * fundamental_block
```

Primary endpoint: 12-month balanced accuracy. Secondary endpoints: 6, 18, and
24 months.

### 2. Momentum-location-regime

```text
technical_block =
    0.35 * rank(12m_minus_1m_return)
  + 0.25 * rank(6m_return)
  + 0.25 * rank(price_to_52w_high)
  + 0.15 * rank(stock_6m_minus_sector_6m)

combined_score =
    0.70 * existing_alpha
  + 0.20 * technical_block
  + 0.10 * market_regime
```

This is a score interaction, not a hard bear-market filter.

### 3. Institutional structure

Use one fixed action-asymmetry hypothesis, prior-only shrunk manager weights,
breadth acceleration, and separate crowding. At 18 and 24 months only, test one
fixed penalty for a positive streak of at least three quarters combined with
top-decile crowding.

## Research controls

- Treat 12-month balanced accuracy as the primary endpoint.
- Apply Holm correction across the three primary experiment families.
- Interpret 6-, 18-, and 24-month results only as prespecified secondary
  endpoints.
- Freeze feature definitions, missingness rules, sector normalization,
  winsorization, and block weights before viewing outer-fold results.
- Keep a permanent registry of attempted and failed variants.
- Require at least eight eligible outer quarters, adequate class counts,
  quarter-block confidence above baseline, and no more than a ten-point coverage
  decline before promotion.
- Retain the current dated-roster and effective-security-mapping trust blockers.

The strongest practical next step is the point-in-time
**value-quality-safety** experiment, followed by
**momentum-52-week-high-sector regime**. Formula changes should remain behind
those two experiments.

## Decomposed institutional sweep

Final run `06d77d4c2333b6ab91b8` decomposes the existing signal into:

- original Alpha breadth/conviction;
- `NEW`, `INCREASED`, `DECREASED`, and `CLOSED` conviction strength;
- median and maximum manager portfolio weight;
- current holder breadth;
- held-value HHI and top-holder crowding;
- quarter-over-quarter Alpha acceleration;
- directional persistence;
- six-month momentum;
- 12-minus-1-month momentum;
- 52-week-high proximity;
- price versus the 200-session average;
- technical trend regime.

The registry contains 240 possible decomposed combinations, but the final
selection process does not search them all against the same labels. Each outer
fold uses two disjoint inner stages:

```text
Stage A:
    screen 11 non-technical institutional recipes at +25/-25
    using earlier mature quarters

Stage B:
    lock the winning institutional recipe
    test 2 technical weights x 16 BUY/SELL threshold pairs
    using the later four mature inner quarters

Maximum attempts per outer fold = 11 + 32 = 43
```

All attempted, rejected, and selected candidates are stored in
`candidate_trials`. Production selection repeats the same two-stage procedure
and stores its attempts in `production_candidate_trials`.

### Final leakage-safe result

| Horizon | Experiment | Accuracy | Balanced accuracy | BUY precision | SELL precision | OOS actions | Baseline-edge bootstrap lower |
|---|---|---:|---:|---:|---:|---:|---:|
| 6 months | Sentiment only | 45.0% | 50.2% | 67.6% | 32.7% | 411 | -5.9 pp |
| 6 months | Technical gate | 64.9% | 55.3% | 69.3% | 46.2% | 205 | -5.1 pp |
| 6 months | Decomposed sweep | 57.5% | 54.7% | 70.0% | 38.7% | 299 | -1.0 pp |
| 12 months | Sentiment only | 47.0% | 55.5% | 79.6% | 30.0% | 300 | -2.2 pp |
| 12 months | Technical gate | 60.9% | 55.3% | 70.1% | 40.8% | 156 | -4.3 pp |
| 12 months | Decomposed sweep | 46.3% | 51.7% | 72.0% | 31.0% | 67 | -12.6 pp |
| 18 months | Sentiment only | 64.9% | 58.5% | 79.0% | 35.8% | 205 | +0.3 pp |
| 18 months | Technical gate | 59.4% | 57.3% | 65.0% | 50.0% | 32 | unavailable |
| 18 months | Decomposed sweep | No selectable model | — | — | — | 0 | unavailable |
| 24 months | Sentiment only | 48.1% | 55.4% | 81.0% | 27.3% | 54 | unavailable |
| 24 months | Technical/decomposed | No selectable model | — | — | — | 0 | unavailable |

The 18-month sentiment-only result is the only row whose available
quarter-block lower bound edges above the balanced baseline, by approximately
0.3 percentage points. It still has only five selected outer quarters and a
rank-IC interval crossing zero, so it is not promotable.

### Selected parameter behavior

- Six-month decomposed folds remained unstable: five distinct candidates were
  selected across eight eligible folds.
- Twelve-month decomposition selected only two folds and produced 67 actions.
- Eighteen- and 24-month decomposition had no selectable outer fold under the
  two-stage maturity requirements.

The current six-month production research candidate selected from prior mature
history is directionally sensible:

```text
combined =
    0.34 Alpha
  + 0.34 action structure
  + 0.17 portfolio weight/breadth
  + 0.15 technical score

action coefficients =
    NEW         +1.00
    INCREASED   +0.75
    DECREASED   -0.25
    CLOSED      -0.25

BUY threshold  = +50
SELL threshold = -25
```

However, the six-month lower confidence bound for balanced-accuracy
improvement over the matched baseline remains negative by approximately one
percentage point. The 12-month primary result is materially worse. The pattern
is a research hypothesis, not a deployable formula. All current
`decision_signal` values therefore remain `HOLD`, while unpromoted candidates
are retained as `research_signal`.
