# Operations Guide

## Requirements

- Python 3.11 or newer.
- Internet access for SEC EDGAR, OpenBB/yfinance, FRED, and TradingView.
- An SEC-compliant identity string in `config.py`.

## Install and run

```powershell
python -m pip install -r requirements.txt
python run.py
```

Direct Uvicorn:

```powershell
python -m uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

## Cache warming

Latest filings and market insights:

```powershell
python prefetch.py
```

All 19 historical snapshots:

```powershell
python prefetch.py --history-only
```

Popular ticker market, valuation, timing, and pair caches:

```powershell
python prefetch.py --ticker-intelligence-only
```

Latest refresh plus both optional warm-ups:

```powershell
python prefetch.py --history --ticker-intelligence
```

## Loading behavior

### QoQ period selector

- Latest period: process cache.
- Previously selected period: memory cache.
- Persisted period: disk cache.
- Missing period: live SEC fetch in groups of five managers.

The loader reports cache source and completed fund count.

### Ticker detail

Ticker loading has three visible stages:

1. Current 13F holdings.
2. OpenBB market, valuation, timing, and 20-quarter history.
3. Pair analysis.

Market and pair results use independent six-hour caches.

## Cache maintenance

Generated cache data is excluded from Git.

| Cache | Maintenance |
|---|---|
| Latest manager snapshot | Run `python prefetch.py` |
| Historical periods | Run `python prefetch.py --history-only` |
| Popular ticker intelligence | Run `python prefetch.py --ticker-intelligence-only` |
| One ticker market cache | Delete `cache/ticker_market/<ticker>.json` |
| One ticker pair cache | Delete `cache/pair_signals/<ticker>.json` |

Do not edit generated JSON values manually. Change transformation logic and
regenerate the affected cache.

## Smoke checks

```powershell
python -m compileall -q config.py data_service.py main.py pair_service.py prefetch.py run.py
python -c "import main; print(type(main.app).__name__, len(main.data_service.cache))"
```

The repository currently has no automated test suite, linter, formatter, or
frontend build step. Use the workspace Playwright MCP server for browser tests.

## Troubleshooting

### SEC period is stale

Confirm the manager's reporting CIK. Add former identifiers to
`historical_ciks` and refresh the affected snapshot.

### Historical period takes time

Run `python prefetch.py --history-only`.

### Ticker view takes time

The first visit downloads OpenBB fundamentals and pair candidate prices. The
three-stage loader remains visible. Warm popular tickers with the ticker
intelligence prefetch command.

### Action and value direction disagree

Action is based on share-count change. Reported value can move in the opposite
direction because the security price changed.

### Pair signal says no valid pair

Correlation alone is insufficient. A pair can fail corrected cointegration,
out-of-sample persistence, stability, hedge-ratio, or half-life gates.
