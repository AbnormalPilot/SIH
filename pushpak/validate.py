"""Component 6 — back-test Pushpak Index against an external average-fare benchmark.

What this module does
---------------------
Rebases an external per-route average-fare series onto the same base period as
Pushpak Index, then compares the two series: correlation, mean absolute error in index
points, MAPE, tracking error, and direction agreement (how often both series
move the same way day over day).

An honest caveat, enforced in code
----------------------------------
The benchmark file shipped in data/dgca_fare_benchmark.csv is a **labelled
placeholder**, not an official DGCA extract. We were unable to obtain a
machine-readable DGCA Tariff Monitoring Unit fare release for this build.

Rather than quietly comparing against made-up numbers and calling the result
"validated", :func:`backtest` reads the ``is_official`` column and refuses to
issue a validation verdict when the benchmark is not official — it returns
``verdict="MACHINERY_ONLY"`` and says so in ``caveat``. The API and dashboard
propagate that wording verbatim.

To run a real back-test, replace the CSV (same columns) with an official
extract and set ``is_official=1``. Nothing else changes.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .index import IndexResult


BENCHMARK_COLUMNS = {"period", "route_id", "avg_fare_inr", "provenance", "is_official"}


def load_benchmark(path=None) -> pd.DataFrame:
    path = path or config.DGCA_FARE_BENCHMARK_CSV
    if not path.exists():
        raise FileNotFoundError(f"benchmark file missing: {path}")
    df = pd.read_csv(path)
    missing = BENCHMARK_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"benchmark file missing columns: {sorted(missing)}")
    df["period"] = pd.to_datetime(df["period"]).dt.strftime("%Y-%m-%d")
    df["avg_fare_inr"] = pd.to_numeric(df["avg_fare_inr"], errors="coerce")
    return df


def _rebase(series: pd.Series, base_periods: list[str]) -> pd.Series:
    """Rescale so the mean over ``base_periods`` equals 100."""
    base = series.loc[series.index.isin(base_periods)]
    base = base.dropna()
    if not len(base) or base.mean() == 0:
        return pd.Series(dtype=float)
    return series / base.mean() * config.BASE_INDEX_VALUE


def build_benchmark_index(
    bench: pd.DataFrame,
    route_weights: pd.DataFrame,
    base_periods: list[str],
) -> pd.DataFrame:
    """Aggregate benchmark route fares into a comparable weighted index.

    Uses the *same* DGCA route weights and the *same* base period as Pushpak Index, so
    any divergence reflects the fare data, not a methodology difference.
    """
    w = route_weights.set_index("route_id")["weight"]
    b = bench[bench["route_id"].isin(w.index)].copy()
    if not len(b):
        return pd.DataFrame(columns=["period", "benchmark_index"])

    wide = b.pivot_table(index="period", columns="route_id",
                         values="avg_fare_inr", aggfunc="mean")
    rebased = wide.apply(lambda col: _rebase(col, base_periods))
    rebased = rebased.dropna(axis=1, how="all")
    if rebased.empty:
        return pd.DataFrame(columns=["period", "benchmark_index"])

    ww = w.reindex(rebased.columns).fillna(0.0)
    ww = ww / ww.sum()
    out = (rebased * ww).sum(axis=1, min_count=1).rename("benchmark_index")
    return out.reset_index()


def backtest(
    result: IndexResult,
    benchmark: Optional[pd.DataFrame] = None,
    route_weights: Optional[pd.DataFrame] = None,
) -> dict:
    """Compare Pushpak Index to the benchmark. Returns a JSON-serialisable report."""
    from .index import load_route_weights

    bench = benchmark if benchmark is not None else load_benchmark()
    rw = route_weights if route_weights is not None else load_route_weights()

    is_official = bool(pd.to_numeric(bench["is_official"], errors="coerce").fillna(0).max())
    provenance = sorted(set(bench["provenance"].astype(str)))

    bidx = build_benchmark_index(bench, rw, result.base_dates)
    pushpak = result.daily[["period", "index_value"]].rename(
        columns={"index_value": "pushpak_index"}
    )
    merged = pushpak.merge(bidx, on="period", how="inner").dropna()

    if len(merged) < 2:
        return {
            "verdict": "INSUFFICIENT_OVERLAP",
            "caveat": "Fewer than 2 overlapping periods; no comparison possible.",
            "n_periods": len(merged),
            "benchmark_is_official": is_official,
            "benchmark_provenance": provenance,
            "series": merged.to_dict("records"),
        }

    a = merged["pushpak_index"].to_numpy(float)
    b = merged["benchmark_index"].to_numpy(float)
    diff = a - b

    corr = float(np.corrcoef(a, b)[0, 1]) if len(a) > 1 and a.std() and b.std() else float("nan")
    mae = float(np.mean(np.abs(diff)))
    mape = float(np.mean(np.abs(diff / b)) * 100)
    tracking_error = float(np.std(diff, ddof=1))
    bias = float(np.mean(diff))

    da = np.sign(np.diff(a))
    db = np.sign(np.diff(b))
    direction_agreement = float(np.mean(da == db)) if len(da) else float("nan")

    metrics = {
        "n_periods": int(len(merged)),
        "period_start": merged["period"].iloc[0],
        "period_end": merged["period"].iloc[-1],
        "correlation": None if np.isnan(corr) else round(corr, 4),
        "mean_absolute_error_index_points": round(mae, 4),
        "mape_percent": round(mape, 4),
        "tracking_error_index_points": round(tracking_error, 4),
        "mean_bias_index_points": round(bias, 4),
        "direction_agreement": None if np.isnan(direction_agreement)
        else round(direction_agreement, 4),
    }

    if not is_official:
        verdict = "MACHINERY_ONLY"
        caveat = (
            "NOT A VALIDATION. The benchmark series is a labelled placeholder "
            f"({', '.join(provenance)}), not an official DGCA fare release, so "
            "these metrics demonstrate that the back-test runs — they say "
            "nothing about whether Pushpak Index tracks real fares. Replace "
            "data/dgca_fare_benchmark.csv with an official DGCA Tariff "
            "Monitoring Unit extract and set is_official=1 to obtain a real "
            "verdict."
        )
    else:
        # Thresholds are prototype-scale judgements, stated so they can be argued with.
        if (metrics["correlation"] or 0) >= 0.7 and mape <= 5.0:
            verdict = "TRACKS_BENCHMARK"
        elif (metrics["correlation"] or 0) >= 0.4 and mape <= 10.0:
            verdict = "PARTIALLY_TRACKS"
        else:
            verdict = "DIVERGES"
        caveat = (
            f"Comparison window is {metrics['n_periods']} days, which is short. "
            "Treat as indicative, not conclusive."
        )

    return {
        "verdict": verdict,
        "caveat": caveat,
        "benchmark_is_official": is_official,
        "benchmark_provenance": provenance,
        "pushpak_synthetic_share": float(result.daily["synthetic_share"].mean())
        if len(result.daily) else None,
        "metrics": metrics,
        "series": [
            {
                "period": r.period,
                "pushpak_index": round(float(r.pushpak_index), 4),
                "benchmark_index": round(float(r.benchmark_index), 4),
                "difference": round(float(r.pushpak_index - r.benchmark_index), 4),
            }
            for r in merged.itertuples()
        ],
    }
