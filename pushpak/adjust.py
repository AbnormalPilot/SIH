"""Component 3, extended — chain-linking and seasonal adjustment.

The headline Pushpak Index is a **fixed-base** Laspeyres: every day's cell
price is divided by that cell's base-period price, and the weights never move.
That is the right default for a prototype because it is reconstructable by
hand. It is not, however, what a national statistical office publishes once a
series has to survive years of sample rotation, and it says nothing about how
much of a day's movement is simply "it is a Friday".

This module adds the two standard treatments, kept deliberately separate from
:mod:`pushpak.index` so that the headline number is never silently adjusted.
Both return new series alongside the original; neither overwrites it.

1. Chain-linking
----------------
A chained Laspeyres multiplies successive *short-term* relatives rather than
dividing by a fixed base:

    I(0) = 100
    I(t) = I(t-1) x  SUM over cells c present on BOTH days [ w(c) x P(c,t)/P(c,t-1) ]

with ``w(c)`` renormalised over exactly the cells common to the two days.

Why it is not cosmetic: the fixed-base form compares day *t* against a base
period that a cell may not have been observed in, and it silently drops cells
that arrive mid-series. The chained form only ever compares a cell against
itself one day earlier, so a cell that enters the sample on day 12 contributes
its movement from day 12 onward instead of being excluded or imputed back to
the base. That is precisely the sample-rotation problem chain-linking exists
to solve.

The cost is **chain drift**: because the two forms weight the path differently,
a series that ends where it started can still leave the chained index away from
100. Drift is not an error to be hidden, it is the diagnostic — a large gap
between the two series means the index is sensitive to sample composition, and
:func:`chain_drift` reports it as a first-class number.

2. Seasonal adjustment
----------------------
Only **day-of-week** seasonality is attempted, by classical ratio-to-moving-
average:

    ratio(t)  = I(t) / centred 7-day moving average of I
    factor(d) = mean of ratio(t) over days t with weekday d, normalised so the
                seven factors multiply to 1
    SA(t)     = I(t) / factor(weekday(t))

A centred 7-day window is used because the cycle being removed is 7 days long:
the moving average then contains exactly one of each weekday and so carries no
day-of-week signal of its own.

**Annual seasonality is deliberately NOT estimated.** Airfares have a large
festival and holiday cycle, but identifying it needs several years of history;
fitting it to 30 days would produce a confident-looking number with no
information in it. :func:`seasonal_summary` states this rather than leaving the
reader to assume the adjustment covers more than it does.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import config

MIN_DAYS_FOR_DOW = 14          # two full weeks: two observations per weekday
MIN_OBS_PER_WEEKDAY = 2

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

ANNUAL_SEASONALITY_NOTE = (
    "Annual and festival seasonality is NOT removed. Estimating it needs at "
    "least 12 months, preferably 3 years, of history; this series is far "
    "shorter. Only the day-of-week cycle is adjusted for."
)


# ---------------------------------------------------------------------------
# 1. Chain-linking
# ---------------------------------------------------------------------------
LINK_FREQUENCIES = ("daily", "weekly", "monthly")
LINK_FORMS = ("arithmetic", "geometric")


def _link_period(dates: pd.Series, frequency: str) -> pd.Series:
    if frequency == "daily":
        return dates.astype(str)
    ts = pd.to_datetime(dates)
    if frequency == "weekly":
        return ts.dt.strftime("%G-W%V")
    if frequency == "monthly":
        return ts.dt.strftime("%Y-%m")
    raise ValueError(f"unknown link frequency {frequency!r}; expected {LINK_FREQUENCIES}")


def chain_link(
    cells: pd.DataFrame,
    link_frequency: str = "daily",
    link_form: str = "arithmetic",
    base_value: float = config.BASE_INDEX_VALUE,
) -> pd.DataFrame:
    """Build a chained index from imputed cells.

    ``cells`` is :attr:`pushpak.index.IndexResult.cells`; it must carry
    ``cell_price_final`` and ``cell_weight``.

    ``link_frequency`` sets how often the index re-bases. This is the single
    most consequential choice here, because linking more often means chaining
    more short-term relatives together and therefore accumulating more drift
    (see :func:`chain_report`). National statistical offices link annually;
    ``daily`` is offered so the effect can be measured rather than asserted.

    ``link_form`` picks how the cells inside one link are combined:

      * ``arithmetic`` -- ``SUM w(c) x R(c)``. This is a chained Laspeyres, and
        it is the form that drifts: by the arithmetic-geometric mean
        inequality a weighted arithmetic mean of relatives exceeds 1 whenever
        the relatives disagree, even if every price ends where it began.
      * ``geometric``  -- ``PROD R(c)^w(c)``. A chained Jevons. It has no such
        bias, so a series that returns to its starting prices returns to its
        starting index value.

    Returns one row per link period with the chained level, that period's link
    factor, and how many cells the link was computed over.
    """
    if link_frequency not in LINK_FREQUENCIES:
        raise ValueError(
            f"unknown link frequency {link_frequency!r}; expected {LINK_FREQUENCIES}"
        )
    if link_form not in LINK_FORMS:
        raise ValueError(f"unknown link form {link_form!r}; expected {LINK_FORMS}")

    empty = pd.DataFrame(
        columns=["period", "chained_index", "link_factor", "n_linked_cells"]
    )
    if not len(cells):
        return empty

    usable = cells[
        cells["cell_price_final"].notna() & (cells["cell_weight"] > 0)
    ].copy()
    if not len(usable):
        return empty

    usable["_cell"] = list(
        zip(usable["route_id"], usable["advance_purchase_days"].astype(int))
    )
    usable["_period"] = _link_period(usable["observation_date"], link_frequency)

    # Within a link period a cell is represented by its mean price over that
    # period. For link_frequency="daily" this is a no-op; for weekly/monthly it
    # is the same period-averaging the headline index uses in resample().
    agg = (
        usable.groupby(["_period", "_cell"])
        .agg(price=("cell_price_final", "mean"), weight=("cell_weight", "mean"))
        .reset_index()
    )

    periods = sorted(agg["_period"].unique())
    prev: dict[tuple[str, int], float] = {}
    level = base_value
    rows = []

    for i, period in enumerate(periods):
        grp = agg[agg["_period"] == period]
        today = {
            c: (float(p), float(w))
            for c, p, w in zip(grp["_cell"], grp["price"], grp["weight"])
        }

        if i == 0:
            rows.append({"period": period, "chained_index": float(level),
                         "link_factor": 1.0, "n_linked_cells": 0})
            prev = {c: p for c, (p, _w) in today.items()}
            continue

        common = [c for c in today if c in prev and prev[c] > 0 and today[c][0] > 0]
        wsum = sum(today[c][1] for c in common)
        if common and wsum > 0:
            if link_form == "arithmetic":
                factor = sum(
                    today[c][1] / wsum * (today[c][0] / prev[c]) for c in common
                )
            else:
                factor = float(np.exp(sum(
                    today[c][1] / wsum * np.log(today[c][0] / prev[c])
                    for c in common
                )))
        else:
            # No overlap at all: the only honest link is "no measured change".
            factor = 1.0

        level *= factor
        rows.append({"period": period, "chained_index": float(level),
                     "link_factor": float(factor), "n_linked_cells": len(common)})
        # Carry every cell seen this period forward, including ones absent last
        # period, so a returning cell links from its own last observation.
        prev.update({c: p for c, (p, _w) in today.items()})

    return pd.DataFrame(rows)


def chain_drift(fixed_base: pd.DataFrame, chained: pd.DataFrame) -> dict:
    """Compare a fixed-base series to a chained one, on matching periods.

    Both frames must be at the same frequency: pass ``result.daily`` with a
    daily chain, or ``result.resample("weekly")`` with a weekly one.
    """
    if not len(fixed_base) or not len(chained):
        return {"available": False, "reason": "no index series"}

    m = fixed_base[["period", "index_value"]].merge(chained, on="period", how="inner")
    if len(m) < 2:
        return {"available": False, "reason": "fewer than 2 overlapping periods"}

    diff = m["chained_index"] - m["index_value"]
    final = float(diff.iloc[-1])
    links = m["link_factor"].iloc[1:]
    mean_link = float(links.mean()) if len(links) else 1.0

    return {
        "available": True,
        "n_periods": int(len(m)),
        "n_links": int(len(links)),
        "fixed_base_final": round(float(m["index_value"].iloc[-1]), 4),
        "chained_final": round(float(m["chained_index"].iloc[-1]), 4),
        "final_drift_index_points": round(final, 4),
        "final_drift_pct": round(final / float(m["index_value"].iloc[-1]) * 100, 4)
        if m["index_value"].iloc[-1] else None,
        "max_abs_drift_index_points": round(float(diff.abs().max()), 4),
        "mean_abs_drift_index_points": round(float(diff.abs().mean()), 4),
        "mean_link_factor": round(mean_link, 8),
        "mean_link_excess_per_period_pct": round((mean_link - 1.0) * 100, 6),
        "series": [
            {
                "period": r.period,
                "fixed_base": round(float(r.index_value), 4),
                "chained": round(float(r.chained_index), 4),
                "drift": round(float(r.chained_index - r.index_value), 4),
                "link_factor": round(float(r.link_factor), 6),
                "n_linked_cells": int(r.n_linked_cells),
            }
            for r in m.itertuples()
        ],
    }


#: What the chain-drift diagnostic is for. Stated in the payload so an API
#: consumer reads the caveat with the number instead of next to it.
CHAIN_DRIFT_NOTE = (
    "Chain drift is not a bug and not noise. A weighted ARITHMETIC mean of "
    "price relatives is >= 1 whenever the relatives disagree, so chaining one "
    "every day compounds a small upward bias even across prices that end where "
    "they started. It is why national statistical offices link annually rather "
    "than at collection frequency, and it is the reason the headline Pushpak "
    "Index is fixed-base. Compare the arithmetic and geometric rows below: the "
    "geometric (Jevons) link has no such bias, and the drift shrinks as the "
    "link frequency falls."
)


def chain_report(result, frequencies=LINK_FREQUENCIES) -> dict:
    """Run every link frequency and both link forms, and report the drift.

    Takes a :class:`pushpak.index.IndexResult`. This is the artefact worth
    showing a reviewer: it measures how much the published number depends on a
    methodological choice instead of claiming the choice does not matter.
    """
    variants = []
    for freq in frequencies:
        fixed = result.daily if freq == "daily" else result.resample(freq)
        if len(fixed) < 2:
            variants.append({
                "link_frequency": freq, "available": False,
                "reason": f"fewer than 2 {freq} periods in the series",
            })
            continue
        for form in LINK_FORMS:
            chained = chain_link(result.cells, link_frequency=freq, link_form=form)
            d = chain_drift(fixed, chained)
            d.update({"link_frequency": freq, "link_form": form})
            variants.append(d)

    usable = [v for v in variants if v.get("available")]
    recommended = None
    if usable:
        # The variant whose chained level sits closest to the fixed-base level
        # is the one whose linking assumption costs least.
        best = min(usable, key=lambda v: abs(v["final_drift_index_points"]))
        recommended = {
            "link_frequency": best["link_frequency"],
            "link_form": best["link_form"],
            "final_drift_index_points": best["final_drift_index_points"],
        }

    return {
        "headline_is_chained": False,
        "headline_form": "fixed-base Laspeyres, unchained",
        "note": CHAIN_DRIFT_NOTE,
        "variants": variants,
        "least_drift": recommended,
    }


# ---------------------------------------------------------------------------
# 2. Day-of-week seasonal adjustment
# ---------------------------------------------------------------------------
def dow_factors(daily: pd.DataFrame, value_col: str = "index_value") -> dict:
    """Estimate multiplicative day-of-week factors by ratio-to-moving-average.

    Returns a dict with ``available`` False and a stated reason when the series
    is too short, rather than returning seven meaningless numbers.
    """
    if not len(daily):
        return {"available": False, "reason": "no index series"}
    if len(daily) < MIN_DAYS_FOR_DOW:
        return {
            "available": False,
            "reason": (
                f"series is {len(daily)} days; day-of-week factors need at "
                f"least {MIN_DAYS_FOR_DOW} so every weekday is seen twice"
            ),
            "n_days": int(len(daily)),
        }

    d = daily[["period", value_col]].dropna().sort_values("period").copy()
    ts = pd.to_datetime(d["period"])
    d["weekday"] = ts.dt.weekday

    # Centred 7-day moving average: contains exactly one of each weekday, so it
    # carries no day-of-week signal of its own.
    ma = d[value_col].rolling(window=7, center=True, min_periods=7).mean()
    d["ratio"] = d[value_col] / ma
    obs = d.dropna(subset=["ratio"])

    counts = obs["weekday"].value_counts().to_dict()
    covered = [wd for wd in range(7) if counts.get(wd, 0) >= MIN_OBS_PER_WEEKDAY]
    if len(covered) < 7:
        missing = [WEEKDAY_NAMES[wd] for wd in range(7) if wd not in covered]
        return {
            "available": False,
            "reason": (
                "after the centred 7-day window is applied, these weekdays have "
                f"fewer than {MIN_OBS_PER_WEEKDAY} usable observations: "
                + ", ".join(missing)
            ),
            "n_days": int(len(d)),
            "n_usable_ratios": int(len(obs)),
        }

    raw = obs.groupby("weekday")["ratio"].mean()
    # Normalise so the seven factors multiply to exactly 1: adjustment then
    # redistributes the level between weekdays without shifting the mean.
    norm = float(np.exp(np.log(raw).mean()))
    factors = (raw / norm).to_dict()

    return {
        "available": True,
        "method": "classical ratio-to-centred-7-day-moving-average, multiplicative",
        "n_days": int(len(d)),
        "n_usable_ratios": int(len(obs)),
        "normalisation": "seven factors constrained to a geometric mean of 1.0",
        "factors": {
            WEEKDAY_NAMES[wd]: round(float(f), 6) for wd, f in sorted(factors.items())
        },
        "observations_per_weekday": {
            WEEKDAY_NAMES[wd]: int(counts.get(wd, 0)) for wd in range(7)
        },
        "annual_seasonality": ANNUAL_SEASONALITY_NOTE,
    }


def seasonally_adjust(
    daily: pd.DataFrame, value_col: str = "index_value"
) -> pd.DataFrame:
    """Return ``daily`` with ``index_sa`` and the factor applied to each day.

    When the factors cannot be estimated, ``index_sa`` equals the unadjusted
    value and ``dow_factor`` is 1.0 — never a silent partial adjustment.
    """
    out = daily.copy()
    if not len(out):
        out["dow_factor"] = pd.Series(dtype=float)
        out["index_sa"] = pd.Series(dtype=float)
        out["sa_applied"] = pd.Series(dtype=bool)
        return out

    est = dow_factors(daily, value_col)
    if not est.get("available"):
        out["dow_factor"] = 1.0
        out["index_sa"] = out[value_col]
        out["sa_applied"] = False
        return out

    by_index = {WEEKDAY_NAMES.index(k): v for k, v in est["factors"].items()}
    wd = pd.to_datetime(out["period"]).dt.weekday
    out["dow_factor"] = wd.map(by_index).astype(float)
    out["index_sa"] = out[value_col] / out["dow_factor"]
    out["sa_applied"] = True
    return out


def seasonal_summary(daily: pd.DataFrame) -> dict:
    """Everything the API and dashboard need about the seasonal treatment."""
    est = dow_factors(daily)
    sa = seasonally_adjust(daily)
    applied = bool(len(sa)) and bool(sa["sa_applied"].iloc[0])

    out = {
        "estimation": est,
        "applied": applied,
        "annual_seasonality": ANNUAL_SEASONALITY_NOTE,
        "series": [],
    }
    if applied:
        raw = sa["index_value"].astype(float)
        adj = sa["index_sa"].astype(float)
        out["volatility_reduction"] = {
            "stdev_raw": round(float(raw.std(ddof=1)), 6) if len(raw) > 1 else None,
            "stdev_seasonally_adjusted":
                round(float(adj.std(ddof=1)), 6) if len(adj) > 1 else None,
            "note": (
                "If adjustment does not reduce the standard deviation, the "
                "day-of-week cycle is not a material part of this series' "
                "variance and the raw index should be read directly."
            ),
        }
        out["series"] = [
            {
                "period": r.period,
                "index_value": round(float(r.index_value), 4),
                "index_sa": round(float(r.index_sa), 4),
                "dow_factor": round(float(r.dow_factor), 6),
            }
            for r in sa.itertuples()
        ]
    return out
