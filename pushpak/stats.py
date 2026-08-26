"""Derived statistics on top of the index (component 3 extension).

The index itself answers "what is the price level". These answer the questions
a statistician asks next: how fast is it moving, how noisy is it, what is
driving it, and how broad-based is the movement.

Everything here is a pure function of the frame :func:`pushpak.index.compute_index`
already produces. Nothing re-reads the database, so each statistic can be
unit-tested against a hand-built series.

A note on annualisation
-----------------------
This prototype holds 30 days of data. Annualising a 30-day change is a
projection, not a measurement, so every annualised figure is returned beside
its raw counterpart and the API labels it ``projection``. Year-on-year change
is simply unavailable until the series is a year old, and the code returns
``None`` for it rather than substituting something that looks similar.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import config

TRADING_DAYS = 365.0     # airfares are quoted every day, not on a market calendar


# ---------------------------------------------------------------------------
# Rates of change
# ---------------------------------------------------------------------------
def changes(daily: pd.DataFrame) -> pd.DataFrame:
    """Per-period change columns on the daily index."""
    if not len(daily):
        return daily.copy()
    d = daily.sort_values("period").copy()
    d["change_abs"] = d["index_value"].diff()
    d["change_pct"] = d["index_value"].pct_change() * 100
    d["log_return"] = np.log(d["index_value"]).diff()
    d["change_pct_7d"] = d["index_value"].pct_change(7) * 100
    d["change_pct_30d"] = d["index_value"].pct_change(30) * 100
    d["index_7d_ma"] = d["index_value"].rolling(7, min_periods=2).mean()
    return d


def inflation_summary(daily: pd.DataFrame) -> dict:
    """Headline rates of change, with unavailable ones returned as None."""
    if len(daily) < 2:
        return {"available": False, "reason": "fewer than 2 periods"}

    d = changes(daily)
    first, last = d["index_value"].iloc[0], d["index_value"].iloc[-1]
    span_days = max(
        (pd.Timestamp(d["period"].iloc[-1]) - pd.Timestamp(d["period"].iloc[0])).days, 1
    )
    total_pct = (last / first - 1) * 100

    def at(n: int) -> Optional[float]:
        if len(d) <= n:
            return None
        v = d["index_value"].iloc[-1] / d["index_value"].iloc[-1 - n] - 1
        return round(float(v * 100), 4)

    # Compound the observed daily rate out to a year. A projection, not a rate.
    daily_rate = (last / first) ** (1 / span_days) - 1
    annualised = ((1 + daily_rate) ** 365.25 - 1) * 100

    return {
        "available": True,
        "latest_period": d["period"].iloc[-1],
        "latest_index": round(float(last), 4),
        "day_on_day_pct": at(1),
        "week_on_week_pct": at(7),
        "month_on_month_pct": at(30),
        "year_on_year_pct": None,
        "year_on_year_note": (
            f"Unavailable: the series is {span_days} days long. Year-on-year "
            "change requires at least 366 days and is not approximated here."
        ),
        "since_base_pct": round(float(total_pct), 4),
        "span_days": span_days,
        "annualised_pct": round(float(annualised), 4),
        "annualised_is_projection": True,
        "annualised_note": (
            f"Compounds the observed {span_days}-day rate to a year. A "
            "projection from a short window, not a measured annual rate."
        ),
    }


# ---------------------------------------------------------------------------
# Dispersion and volatility
# ---------------------------------------------------------------------------
def volatility(daily: pd.DataFrame, window: int = 7) -> dict:
    """Realised volatility of the index, from daily log returns."""
    if len(daily) < 3:
        return {"available": False, "reason": "fewer than 3 periods"}
    d = changes(daily)
    r = d["log_return"].dropna()
    if not len(r):
        return {"available": False, "reason": "no returns"}

    sd = float(r.std(ddof=1))
    roll = d["log_return"].rolling(window, min_periods=3).std(ddof=1) * 100

    return {
        "available": True,
        "daily_stdev_pct": round(sd * 100, 4),
        "annualised_stdev_pct": round(float(sd * np.sqrt(TRADING_DAYS) * 100), 4),
        "annualised_is_projection": True,
        "mean_abs_daily_change_pct": round(float(d["change_pct"].abs().mean()), 4),
        "max_daily_rise_pct": round(float(d["change_pct"].max()), 4),
        "max_daily_fall_pct": round(float(d["change_pct"].min()), 4),
        "rolling_window_days": window,
        "rolling": [
            {"period": p, "stdev_pct": None if pd.isna(v) else round(float(v), 4)}
            for p, v in zip(d["period"], roll)
        ],
    }


def fare_dispersion(cells: pd.DataFrame) -> dict:
    """How widely fares are spread on the latest day, per advance-purchase window.

    Dispersion is the thing a monthly average hides. A T+1 window with a wide
    p90/p10 ratio means the headline mean is a poor summary of what a traveller
    actually pays, which is an argument for collecting at this granularity.
    """
    if not len(cells):
        return {"available": False, "reason": "no cells"}
    obs = cells[cells["cell_price"].notna()]
    if not len(obs):
        return {"available": False, "reason": "no priced cells"}

    latest = obs["observation_date"].max()
    cur = obs[obs["observation_date"] == latest]

    out = []
    for ap, g in cur.groupby("advance_purchase_days"):
        v = g["cell_price"].to_numpy(float)
        p10, p90 = np.percentile(v, [10, 90])
        mean = float(v.mean())
        out.append({
            "advance_purchase_days": int(ap),
            "n_cells": int(len(v)),
            "mean_fare": round(mean, 2),
            "median_fare": round(float(np.median(v)), 2),
            "stdev": round(float(v.std(ddof=1)) if len(v) > 1 else 0.0, 2),
            "coefficient_of_variation": round(
                float(v.std(ddof=1) / mean) if len(v) > 1 and mean else 0.0, 4),
            "p10": round(float(p10), 2),
            "p90": round(float(p90), 2),
            "p90_p10_ratio": round(float(p90 / p10), 4) if p10 else None,
        })

    return {
        "available": True,
        "period": latest,
        "note": (
            "Dispersion across routes within each advance-purchase window. "
            "A wide p90/p10 ratio means the mean is a poor summary of what a "
            "traveller pays, which a monthly average would conceal."
        ),
        "by_window": sorted(out, key=lambda x: x["advance_purchase_days"]),
    }


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------
def route_contributions(
    cells: pd.DataFrame, period: str, previous: Optional[str] = None
) -> dict:
    """Decompose the index change between two days into per-route contributions.

    Because the index is a weighted sum of cell relatives, the change decomposes
    exactly: each route's contribution is its weight times the change in its
    cells' relatives, and the contributions add back to the total change.
    """
    if not len(cells):
        return {"available": False, "reason": "no cells"}

    usable = cells[cells["relative"].notna() & (cells["cell_weight"] > 0)]
    days = sorted(usable["observation_date"].unique())
    if period not in days:
        return {"available": False, "reason": f"no index for {period}"}

    if previous is None:
        i = days.index(period)
        if i == 0:
            return {"available": False, "reason": "no previous period"}
        previous = days[i - 1]

    def contrib(day: str) -> pd.Series:
        g = usable[usable["observation_date"] == day]
        w = g["cell_weight"] / g["cell_weight"].sum()
        return (w * g["relative"] * config.BASE_INDEX_VALUE).groupby(g["route_id"]).sum()

    now, before = contrib(period), contrib(previous)
    routes = sorted(set(now.index) | set(before.index))
    delta = {r: float(now.get(r, 0.0) - before.get(r, 0.0)) for r in routes}
    total = sum(delta.values())

    rows = [
        {
            "route_id": r,
            "contribution_points": round(delta[r], 4),
            "share_of_change": (
                round(delta[r] / total, 4) if abs(total) > 1e-12 else None
            ),
            "index_now": round(float(now.get(r, np.nan)), 4),
            "index_before": round(float(before.get(r, np.nan)), 4),
        }
        for r in routes
    ]
    rows.sort(key=lambda x: -abs(x["contribution_points"]))

    return {
        "available": True,
        "period": period,
        "previous_period": previous,
        "total_change_points": round(total, 4),
        "note": (
            "Contributions sum to the total index change exactly, because the "
            "index is linear in the cell relatives."
        ),
        "routes": rows,
    }


def diffusion(cells: pd.DataFrame, period: str) -> dict:
    """Share of cells that rose. Distinguishes broad moves from one-route spikes.

    An index can rise 2% because every route rose a little, or because one route
    spiked. Diffusion tells them apart, and a headline index alone cannot.
    """
    if not len(cells):
        return {"available": False, "reason": "no cells"}
    usable = cells[cells["relative"].notna()]
    days = sorted(usable["observation_date"].unique())
    if period not in days or days.index(period) == 0:
        return {"available": False, "reason": "no previous period"}
    prev = days[days.index(period) - 1]

    a = usable[usable["observation_date"] == period].set_index(
        ["route_id", "advance_purchase_days"])["relative"]
    b = usable[usable["observation_date"] == prev].set_index(
        ["route_id", "advance_purchase_days"])["relative"]
    common = a.index.intersection(b.index)
    if not len(common):
        return {"available": False, "reason": "no overlapping cells"}

    d = (a.loc[common] - b.loc[common]).to_numpy(float)
    up = int((d > 0).sum())
    down = int((d < 0).sum())
    flat = int((d == 0).sum())
    n = len(d)

    return {
        "available": True,
        "period": period,
        "previous_period": prev,
        "n_cells": n,
        "cells_up": up,
        "cells_down": down,
        "cells_flat": flat,
        # 50 means evenly split; above 50 means the rise is broad-based.
        "diffusion_index": round((up + 0.5 * flat) / n * 100, 2),
        "interpretation": (
            "Above 50 means the move is broad-based across the basket; near or "
            "below 50 with a rising index means one or two routes are driving it."
        ),
    }


# ---------------------------------------------------------------------------
def summary(daily: pd.DataFrame, cells: pd.DataFrame) -> dict:
    """Everything above, for the /api/v1/stats endpoint and the dashboard."""
    period = daily["period"].iloc[-1] if len(daily) else None
    return {
        "period": period,
        "inflation": inflation_summary(daily),
        "volatility": volatility(daily),
        "dispersion": fare_dispersion(cells),
        "attribution": route_contributions(cells, period) if period else
                       {"available": False, "reason": "no periods"},
        "diffusion": diffusion(cells, period) if period else
                     {"available": False, "reason": "no periods"},
    }
