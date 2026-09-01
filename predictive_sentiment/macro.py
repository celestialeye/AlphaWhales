from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import statsmodels.api as sm


DEFAULT_MACRO_DIR = Path(
    "data/investor_screening/predictive_sentiment/macro"
)
FRED_SERIES = ("DGS10", "DGS2", "DFII10")
DXY_SYMBOL = "DX-Y.NYB"
SECTOR_ETFS = (
    "XLB",
    "XLC",
    "XLE",
    "XLF",
    "XLI",
    "XLK",
    "XLP",
    "XLRE",
    "XLU",
    "XLV",
    "XLY",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_parquet(frame: pd.DataFrame, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "row_count": len(frame),
        "min_date": frame["date"].min().isoformat(),
        "max_date": frame["date"].max().isoformat(),
    }


def _normalize_value_series(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    return (
        result.dropna(subset=["date", "value"])
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def prepare_macro_artifacts(
    *,
    fred_source_dir: str | Path,
    output_dir: str | Path = DEFAULT_MACRO_DIR,
    start_date: date = date(2010, 1, 1),
    end_date: date,
) -> dict:
    """Create AlphaWhales-owned macro artifacts without runtime repo coupling."""
    source_dir = Path(fred_source_dir).resolve()
    target_dir = Path(output_dir).resolve()
    artifacts = {}
    for series_id in FRED_SERIES:
        source = source_dir / f"{series_id}.parquet"
        if not source.is_file():
            raise FileNotFoundError(
                f"FRED source artifact is missing: {source}"
            )
        frame = _normalize_value_series(pd.read_parquet(source))
        frame = frame[
            (frame["date"] >= start_date)
            & (frame["date"] <= end_date)
        ].copy()
        if frame.empty:
            raise ValueError(f"{series_id} has no rows in requested range")
        artifacts[series_id] = _write_parquet(
            frame,
            target_dir / f"{series_id.lower()}.parquet",
        )

    import yfinance as yf

    dxy = yf.download(
        DXY_SYMBOL,
        start=start_date,
        end=pd.Timestamp(end_date) + pd.Timedelta(days=1),
        auto_adjust=False,
        progress=False,
        actions=False,
    )
    if dxy is None or dxy.empty:
        raise ValueError("DXY provider returned no historical rows")
    if isinstance(dxy.columns, pd.MultiIndex):
        dxy.columns = dxy.columns.get_level_values(0)
    dxy = dxy.reset_index()
    dxy.columns = [str(column).lower() for column in dxy.columns]
    if "date" not in dxy and "index" in dxy:
        dxy = dxy.rename(columns={"index": "date"})
    dxy = dxy.rename(columns={"close": "value"})
    dxy = _normalize_value_series(dxy[["date", "value"]])
    artifacts["DXY"] = _write_parquet(
        dxy,
        target_dir / "dxy.parquet",
    )

    manifest = {
        "schema_version": "awfi-macro-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_start": start_date.isoformat(),
        "requested_end": end_date.isoformat(),
        "fred_source_dir": str(source_dir),
        "dxy_symbol": DXY_SYMBOL,
        "artifacts": artifacts,
    }
    manifest_path = target_dir / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return manifest


def load_macro_bundle(
    path: str | Path = DEFAULT_MACRO_DIR,
) -> tuple[dict[str, pd.Series], str]:
    directory = Path(path).resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Macro manifest is missing: {manifest_path}"
        )
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    series = {}
    for name, metadata in manifest["artifacts"].items():
        artifact = Path(metadata["path"])
        if not artifact.is_file():
            raise FileNotFoundError(f"Macro artifact is missing: {artifact}")
        if _sha256(artifact) != metadata["sha256"]:
            raise ValueError(f"Macro artifact hash mismatch: {name}")
        frame = _normalize_value_series(pd.read_parquet(artifact))
        series[name] = pd.Series(
            frame["value"].to_numpy(dtype=float),
            index=pd.Index(frame["date"], name="date"),
            name=name,
        )
    return series, hashlib.sha256(manifest_bytes).hexdigest()


def _robust_trailing_score(
    values: pd.Series,
    as_of: date,
    *,
    window: int = 1260,
    minimum: int = 756,
) -> float | None:
    history = values[values.index <= as_of].dropna().tail(window)
    if len(history) < minimum:
        return None
    median = float(history.median())
    mad = float((history - median).abs().median())
    scale = 1.4826 * mad
    if scale <= 0:
        quantiles = history.quantile([0.25, 0.75])
        scale = float(quantiles.loc[0.75] - quantiles.loc[0.25]) / 1.349
    if scale <= 0:
        return None
    z_score = (float(history.iloc[-1]) - median) / scale
    return max(-100.0, min(100.0, z_score / 3.0 * 100.0))


def macro_features_at(
    bundle: dict[str, pd.Series],
    spy: pd.Series,
    *,
    feature_date: date,
) -> dict[str, float | None]:
    dgs10 = bundle["DGS10"].sort_index()
    dgs2 = bundle["DGS2"].sort_index()
    real10 = bundle["DFII10"].sort_index()
    dxy = bundle["DXY"].sort_index()
    components = {
        "yield_level_score": _robust_trailing_score(dgs10, feature_date),
        "yield_3m_change_score": _robust_trailing_score(
            dgs10.diff(63), feature_date
        ),
        "yield_6m_change_score": _robust_trailing_score(
            dgs10.diff(126), feature_date
        ),
        "real_yield_score": _robust_trailing_score(real10, feature_date),
        "curve_10y2y_score": _robust_trailing_score(
            dgs10.subtract(dgs2, fill_value=math.nan),
            feature_date,
        ),
        "dxy_3m_score": _robust_trailing_score(
            dxy.pct_change(63, fill_method=None), feature_date
        ),
        "dxy_6m_score": _robust_trailing_score(
            dxy.pct_change(126, fill_method=None), feature_date
        ),
        "spy_6m_score": _robust_trailing_score(
            spy.pct_change(126, fill_method=None), feature_date
        ),
        "spy_12m1m_score": _robust_trailing_score(
            spy.shift(21).divide(spy.shift(252)).subtract(1.0),
            feature_date,
        ),
        "spy_trend_score": _robust_trailing_score(
            spy.divide(spy.rolling(200).mean()).map(math.log),
            feature_date,
        ),
    }
    required = (
        "yield_level_score",
        "yield_3m_change_score",
        "yield_6m_change_score",
        "dxy_3m_score",
        "dxy_6m_score",
        "spy_6m_score",
        "spy_12m1m_score",
        "spy_trend_score",
    )
    if any(components[name] is None for name in required):
        return {**components, "rates_score": None, "usd_score": None, "market_score": None, "macro_score": None}
    rates_score = (
        -0.20 * components["yield_level_score"]
        - 0.35 * components["yield_3m_change_score"]
        - 0.45 * components["yield_6m_change_score"]
    )
    usd_score = (
        -0.40 * components["dxy_3m_score"]
        - 0.60 * components["dxy_6m_score"]
    )
    market_score = (
        0.55 * components["spy_12m1m_score"]
        + 0.25 * components["spy_6m_score"]
        + 0.20 * components["spy_trend_score"]
    )
    return {
        **components,
        "rates_score": max(-100.0, min(100.0, rates_score)),
        "usd_score": max(-100.0, min(100.0, usd_score)),
        "market_score": max(-100.0, min(100.0, market_score)),
        "macro_score": max(
            -100.0,
            min(
                100.0,
                0.40 * rates_score
                + 0.25 * usd_score
                + 0.35 * market_score,
            ),
        ),
    }


def sector_proxy_features_at(
    stock: pd.Series,
    sector_prices: dict[str, pd.Series],
    spy: pd.Series,
    *,
    feature_date: date,
) -> dict[str, float | str | None]:
    spy_dates = [item for item in spy.index if item <= feature_date]
    if len(spy_dates) < 526:
        return {
            "sector_proxy": None,
            "relative_12m1m": None,
            "relative_6m": None,
            "sector_vs_spy_6m": None,
        }
    assignment_end = spy_dates[-22]
    assignment_dates = spy_dates[-526:-21]
    stock_returns = (
        stock.reindex(assignment_dates)
        .pct_change(fill_method=None)
        .dropna()
    )
    best_symbol = None
    best_correlation = -math.inf
    for symbol, prices in sector_prices.items():
        sector_returns = prices.reindex(assignment_dates).pct_change(
            fill_method=None
        )
        paired = pd.concat(
            [stock_returns, sector_returns],
            axis=1,
            join="inner",
        ).dropna()
        if len(paired) < 378:
            continue
        correlation = float(paired.iloc[:, 0].corr(paired.iloc[:, 1]))
        if (
            math.isfinite(correlation)
            and (
                correlation > best_correlation
                or (
                    correlation == best_correlation
                    and (best_symbol is None or symbol < best_symbol)
                )
            )
        ):
            best_symbol = symbol
            best_correlation = correlation
    if best_symbol is None:
        return {
            "sector_proxy": None,
            "relative_12m1m": None,
            "relative_6m": None,
            "sector_vs_spy_6m": None,
        }
    dates = spy_dates
    if len(dates) < 253:
        return {
            "sector_proxy": best_symbol,
            "relative_12m1m": None,
            "relative_6m": None,
            "sector_vs_spy_6m": None,
        }
    sector = sector_prices[best_symbol]
    current_date = dates[-1]
    date_21 = dates[-22]
    date_126 = dates[-127]
    date_252 = dates[-253]
    required = (
        current_date in stock.index,
        date_21 in stock.index,
        date_126 in stock.index,
        date_252 in stock.index,
        current_date in sector.index,
        date_21 in sector.index,
        date_126 in sector.index,
        date_252 in sector.index,
        current_date in spy.index,
        date_126 in spy.index,
    )
    if not all(required):
        return {
            "sector_proxy": best_symbol,
            "relative_12m1m": None,
            "relative_6m": None,
            "sector_vs_spy_6m": None,
        }
    stock_12m1m = (
        float(stock.loc[date_21]) / float(stock.loc[date_252]) - 1.0
    )
    sector_12m1m = (
        float(sector.loc[date_21]) / float(sector.loc[date_252]) - 1.0
    )
    stock_6m = float(stock.loc[current_date]) / float(stock.loc[date_126]) - 1.0
    sector_6m = (
        float(sector.loc[current_date]) / float(sector.loc[date_126]) - 1.0
    )
    spy_6m = float(spy.loc[current_date]) / float(spy.loc[date_126]) - 1.0
    return {
        "sector_proxy": best_symbol,
        "sector_proxy_correlation": best_correlation,
        "sector_assignment_end": assignment_end,
        "relative_12m1m": stock_12m1m - sector_12m1m,
        "relative_6m": stock_6m - sector_6m,
        "sector_vs_spy_6m": sector_6m - spy_6m,
    }


def stock_macro_sensitivity_at(
    stock: pd.Series,
    sector: pd.Series,
    spy: pd.Series,
    dgs10: pd.Series,
    dxy: pd.Series,
    *,
    feature_date: date,
    market_score: float,
    yield_6m_score: float,
    dxy_6m_score: float,
) -> dict[str, float | None]:
    dates = [item for item in spy.index if item <= feature_date][-505:]
    if len(dates) < 379:
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    levels = pd.DataFrame(
        {
            "stock": stock.reindex(dates),
            "sector": sector.reindex(dates),
            "spy": spy.reindex(dates),
            "yield": dgs10.reindex(dates, method="ffill", limit=2),
            "dxy": dxy.reindex(dates, method="ffill", limit=2),
        }
    ).dropna()
    if len(levels) < 379:
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    regressors = pd.DataFrame(
        {
            "market": (levels["spy"] / levels["spy"].shift(1)).map(math.log),
            "sector_excess": (
                levels["sector"] / levels["sector"].shift(1)
            ).map(math.log)
            - (levels["spy"] / levels["spy"].shift(1)).map(math.log),
            "yield_change": levels["yield"].diff(),
            "dxy_return": (
                levels["dxy"] / levels["dxy"].shift(1)
            ).map(math.log),
        }
    )
    target = (levels["stock"] / levels["stock"].shift(1)).map(math.log)
    data = pd.concat([target.rename("target"), regressors], axis=1).dropna()
    if len(data) < 378:
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    for column in data.columns:
        lower, upper = data[column].quantile([0.01, 0.99])
        data[column] = data[column].clip(lower, upper)
    x = data.drop(columns=["target"])
    means = x.mean()
    deviations = x.std(ddof=1)
    if (deviations <= 0).any():
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    standardized = (x - means) / deviations
    design = sm.add_constant(standardized)
    if design.shape[1] > len(data):
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    condition_number = float(np.linalg.cond(design.to_numpy()))
    if not math.isfinite(condition_number) or condition_number > 30:
        return {
            "market_sensitivity_raw": None,
            "rate_sensitivity_raw": None,
            "dxy_sensitivity_raw": None,
        }
    fitted = sm.OLS(data["target"], design).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": 5},
    )
    reliability_sample = len(data) / (len(data) + 126.0)

    def reliable(column: str) -> float:
        coefficient = float(fitted.params[column])
        t_value = abs(float(fitted.tvalues[column]))
        reliability = reliability_sample * max(
            0.0,
            min(1.0, (t_value - 1.0) / 2.0),
        )
        return coefficient * reliability

    market_beta = float(
        data["target"].cov(data["market"])
        / data["market"].var(ddof=1)
    )
    market_beta = 1.0 + reliability_sample * (market_beta - 1.0)
    return {
        "market_sensitivity_raw": (
            (max(0.0, min(2.0, market_beta)) - 1.0)
            * market_score
            / 100.0
        ),
        "rate_sensitivity_raw": (
            reliable("yield_change") * yield_6m_score / 100.0
        ),
        "dxy_sensitivity_raw": (
            reliable("dxy_return") * dxy_6m_score / 100.0
        ),
        "sensitivity_condition_number": condition_number,
        "sensitivity_observations": len(data),
    }
