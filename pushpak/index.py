"""Component 3 — the Pushpak Index.

A fixed-basket, Laspeyres-style price index, deliberately kept simple enough
that a reviewer can reconstruct any published number by hand from the raw
quote table. No chain-linking, no seasonal adjustment: the brief asks for a
legible prototype, and both would obscure the arithmetic.

The formula
-----------
Two levels of aggregation, exactly as CPI compilation does it.

**Level 1 — elementary aggregate (one cell).**
A cell is one (route x advance-purchase window). Its price on day *t* is the
arithmetic mean of the priced quotes in it (see clean.build_cells). Its
*price relative* is

    R(c,t) = P(c,t) / P(c,0)

where P(c,0) is the base-period price: the mean of that cell's daily prices
over the first ``BASE_PERIOD_DAYS`` days. Using a short window rather than a
single day stops one unusual base day from re-scaling the whole series.

**Level 2 — weighted aggregation.**

    Pushpak(t) = 100 x  SUM over routes r  [ w(r) x SUM over windows a [ v(a) x R(r,a,t) ] ]

  * ``w(r)`` — route weight, the route's share of basket passengers, derived
    from the DGCA city-pair traffic release (data/route_weights.csv). Fixed
    across the whole series: that is what makes it a *fixed-basket Laspeyres*
    index rather than a Paasche or a chained one.
  * ``v(a)`` — advance-purchase window weight (config.AP_WINDOW_WEIGHTS),
    approximating the booking curve. Flagged ASSUMED_PRIOR, not an official
    statistic.

Because the outer form is linear in the cell relatives, the whole index is one
weighted sum: multiply each cell's relative by ``w(r) x v(a)``, add them up,
multiply by 100. ``explain()`` prints exactly that table, term by term, so the
"reconstruct it by hand" requirement is a function call rather than a promise.

Why arithmetic (Dutot/Laspeyres) and not geometric (Jevons)
-----------------------------------------------------------
A geometric elementary aggregate is arguably better behaved for airfares.
It is not used here because the brief asks for hand-reconstructability, and a
weighted arithmetic mean of relatives is the form a reader can check on paper.
The trade-off is noted in the README's limitations.

Provenance is a first-class output
----------------------------------
Every index value carries the share of its underlying quotes that were
synthetic, imputed and sold-out, plus a green/amber/red quality flag. A
consumer polling the API can gate on those fields without reading the docs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .clean import build_cells, clean, impute_cells
from .schema import ImputationMethod, utc_now_iso


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
def load_route_weights(path=None) -> pd.DataFrame:
    """Load DGCA-derived route weights. Never hand-typed — see scripts/."""
    path = path or config.ROUTE_WEIGHTS_CSV
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing. Run: python3 scripts/build_weights.py"
        )
    w = pd.read_csv(path)
    total = w["weight"].sum()
    if not np.isclose(total, 1.0, atol=1e-6):
        raise ValueError(f"route weights must sum to 1.0, got {total}")
    return w


def ap_weights(windows=config.ADVANCE_PURCHASE_WINDOWS) -> dict[int, float]:
    """AP-window weights restricted to ``windows`` and renormalised to 1.0."""
    raw = {a: config.AP_WINDOW_WEIGHTS[a] for a in windows
           if a in config.AP_WINDOW_WEIGHTS}
    if not raw:
        raise ValueError(f"no AP weights configured for windows {windows}")
    total = sum(raw.values())
    return {a: v / total for a, v in raw.items()}


def cell_weights(
    route_weights: pd.DataFrame,
    windows=config.ADVANCE_PURCHASE_WINDOWS,
) -> pd.Series:
    """The product weight w(r) x v(a) for every cell. Sums to exactly 1.0."""
    apw = ap_weights(windows)
    idx, vals = [], []
    for r in route_weights.itertuples():
        for a, v in apw.items():
            idx.append((r.route_id, a))
            vals.append(r.weight * v)
    s = pd.Series(
        vals,
        index=pd.MultiIndex.from_tuples(idx, names=["route_id", "advance_purchase_days"]),
        name="cell_weight",
    )
    return s / s.sum()


# ---------------------------------------------------------------------------
# Base period
# ---------------------------------------------------------------------------
def compute_base_prices(
    cells: pd.DataFrame, base_days: int = config.BASE_PERIOD_DAYS
) -> tuple[pd.Series, list[str]]:
    """Base price P(c,0) per cell = mean cell price over the first N days.

    Returns (series indexed by (route_id, ap_days), the base dates used).
    """
    if not len(cells):
        return pd.Series(dtype=float), []
    dates = sorted(cells["observation_date"].unique())[:base_days]
    base = cells[cells["observation_date"].isin(dates)]
    s = (
        base.groupby(["route_id", "advance_purchase_days"])["cell_price"]
        .mean()
        .dropna()
    )
    return s, list(dates)


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------
def quality_flag(synthetic_share: float, imputed_share: float) -> str:
    g = config.QUALITY_THRESHOLDS["green"]
    a = config.QUALITY_THRESHOLDS["amber"]
    if (imputed_share <= g["max_imputed_share"]
            and synthetic_share <= g["max_synthetic_share"]):
        return "green"
    if (imputed_share <= a["max_imputed_share"]
            and synthetic_share <= a["max_synthetic_share"]):
        return "amber"
    return "red"


# ---------------------------------------------------------------------------
@dataclass
class IndexResult:
    daily: pd.DataFrame
    route_daily: pd.DataFrame
    cells: pd.DataFrame                 # cells with imputation applied
    base_prices: pd.Series
    base_dates: list[str]
    weights: pd.Series
    report: dict = field(default_factory=dict)

    def resample(self, frequency: str) -> pd.DataFrame:
        """Aggregate the daily index to weekly or monthly.

        The period value is the *mean of the daily index values* in the period
        — the standard way a monthly CPI relates to higher-frequency
        collection, and it keeps the base at 100 by construction.
        """
        if frequency == "daily" or not len(self.daily):
            return self.daily.copy()
        d = self.daily.copy()
        ts = pd.to_datetime(d["period"])
        if frequency == "weekly":
            d["_p"] = ts.dt.strftime("%G-W%V")
        elif frequency == "monthly":
            d["_p"] = ts.dt.strftime("%Y-%m")
        else:
            raise ValueError(f"unknown frequency {frequency!r}")

        agg = d.groupby("_p").apply(
            lambda g: pd.Series({
                "index_value": g["index_value"].mean(),
                "n_quotes": int(g["n_quotes"].sum()),
                "n_cells": int(g["n_cells"].sum()),
                "n_cells_imputed": int(g["n_cells_imputed"].sum()),
                # shares are quote-weighted, not a mean of daily shares
                "synthetic_share": float(
                    np.average(g["synthetic_share"], weights=g["n_quotes"])
                ) if g["n_quotes"].sum() else 0.0,
                "imputed_share": float(g["n_cells_imputed"].sum() / g["n_cells"].sum())
                if g["n_cells"].sum() else 0.0,
                "soldout_share": float(
                    np.average(g["soldout_share"], weights=g["n_quotes"])
                ) if g["n_quotes"].sum() else 0.0,
                "n_days": len(g),
            }),
            include_groups=False,
        ).reset_index().rename(columns={"_p": "period"})

        for c in ("n_quotes", "n_cells", "n_cells_imputed", "n_days"):
            agg[c] = agg[c].astype(int)
        agg["frequency"] = frequency
        agg["quality_flag"] = [
            quality_flag(r.synthetic_share, r.imputed_share) for r in agg.itertuples()
        ]
        agg["computed_at"] = utc_now_iso()
        agg["detail_json"] = None
        return agg


# ---------------------------------------------------------------------------
def compute_index(
    quotes: pd.DataFrame,
    route_weights: Optional[pd.DataFrame] = None,
    windows=config.ADVANCE_PURCHASE_WINDOWS,
    base_days: int = config.BASE_PERIOD_DAYS,
    already_clean: bool = False,
) -> IndexResult:
    """Full pipeline: quotes -> cleaned cells -> imputed cells -> Pushpak Index series."""
    rw = route_weights if route_weights is not None else load_route_weights()
    w = cell_weights(rw, windows)

    if already_clean:
        cleaned, report = quotes, {}
    else:
        cleaned, _quarantined, rep = clean(quotes)
        report = rep.as_dict()

    basket_routes = set(rw["route_id"])
    cleaned = cleaned[
        cleaned["route_id"].isin(basket_routes)
        & cleaned["advance_purchase_days"].isin(list(windows))
    ]

    cells = build_cells(cleaned)
    base_prices, base_dates = compute_base_prices(cells, base_days)
    cells = impute_cells(cells, base_prices)

    # ---- relatives ------------------------------------------------------
    cells["base_price"] = [
        base_prices.get((r.route_id, int(r.advance_purchase_days)), np.nan)
        for r in cells.itertuples()
    ]
    cells["relative"] = cells["cell_price_final"] / cells["base_price"]
    cells["cell_weight"] = [
        w.get((r.route_id, int(r.advance_purchase_days)), 0.0)
        for r in cells.itertuples()
    ]

    usable = cells[cells["relative"].notna() & (cells["cell_weight"] > 0)].copy()

    daily_rows, route_rows = [], []
    for day, grp in usable.groupby("observation_date"):
        # Renormalise across the cells actually present, so a missing cell
        # does not silently drag the index toward zero.
        wsum = grp["cell_weight"].sum()
        if wsum <= 0:
            continue
        contrib = grp["cell_weight"] / wsum * grp["relative"]
        value = config.BASE_INDEX_VALUE * contrib.sum()

        n_quotes = int(grp["n_quotes"].sum())
        n_cells = len(grp)
        n_imp = int(grp["is_imputed"].sum())
        syn = float(
            (grp["n_synthetic"].sum() / n_quotes) if n_quotes else 0.0
        )
        sold = float((grp["n_soldout"].sum() / n_quotes) if n_quotes else 0.0)
        imp_share = n_imp / n_cells if n_cells else 0.0

        daily_rows.append({
            "period": day, "frequency": "daily", "index_value": float(value),
            "n_quotes": n_quotes, "n_cells": n_cells, "n_cells_imputed": n_imp,
            "synthetic_share": syn, "imputed_share": imp_share,
            "soldout_share": sold,
            "quality_flag": quality_flag(syn, imp_share),
            "weight_coverage": float(wsum),
            "computed_at": utc_now_iso(),
            "detail_json": json.dumps({
                "base_dates": base_dates,
                "route_weight_provenance": config.ROUTE_WEIGHT_PROVENANCE,
                "ap_weight_provenance": config.AP_WINDOW_WEIGHT_PROVENANCE,
            }),
        })

        # ---- per-route sub-index (same formula, AP weights only) --------
        for route, rg in grp.groupby("route_id"):
            rwsum = rg["cell_weight"].sum()
            if rwsum <= 0:
                continue
            rvalue = config.BASE_INDEX_VALUE * (
                rg["cell_weight"] / rwsum * rg["relative"]
            ).sum()
            rq = int(rg["n_quotes"].sum())
            route_rows.append({
                "period": day, "frequency": "daily", "route_id": route,
                "index_value": float(rvalue),
                "mean_fare": float(rg["cell_price_final"].mean()),
                "weight": float(rw.loc[rw["route_id"] == route, "weight"].iloc[0]),
                "n_quotes": rq,
                "imputed_share": float(rg["is_imputed"].sum() / len(rg)),
            })

    daily = pd.DataFrame(daily_rows).sort_values("period").reset_index(drop=True)
    route_daily = pd.DataFrame(route_rows).sort_values(["period", "route_id"]).reset_index(drop=True)

    report.update({
        "base_dates": base_dates,
        "base_index_value": config.BASE_INDEX_VALUE,
        "n_days": len(daily),
        "n_cells_total": len(cells),
        "n_cells_usable": len(usable),
        "route_weight_provenance": config.ROUTE_WEIGHT_PROVENANCE,
        "ap_weight_provenance": config.AP_WINDOW_WEIGHT_PROVENANCE,
    })

    return IndexResult(
        daily=daily, route_daily=route_daily, cells=cells,
        base_prices=base_prices, base_dates=base_dates, weights=w, report=report,
    )


# ---------------------------------------------------------------------------
def explain(result: IndexResult, period: str) -> dict:
    """Reconstruct one published index number, term by term.

    This is the auditability requirement made executable: the returned table
    has one row per cell with its base price, current price, relative, weight
    and contribution, and the contributions sum to the published value.
    Rendered by `python3 -m pushpak.cli explain --date YYYY-MM-DD` and by the
    /api/v1/index/explain endpoint.
    """
    cells = result.cells
    grp = cells[
        (cells["observation_date"] == period)
        & cells["relative"].notna()
        & (cells["cell_weight"] > 0)
    ].copy()
    if not len(grp):
        raise ValueError(f"no index computed for {period}")

    wsum = grp["cell_weight"].sum()
    grp["normalised_weight"] = grp["cell_weight"] / wsum
    grp["contribution"] = grp["normalised_weight"] * grp["relative"] * config.BASE_INDEX_VALUE

    terms = [
        {
            "cell": f"{r.route_id} @ T+{int(r.advance_purchase_days)}",
            "route_id": r.route_id,
            "advance_purchase_days": int(r.advance_purchase_days),
            "base_price": round(float(r.base_price), 2),
            "price": round(float(r.cell_price_final), 2),
            "relative": round(float(r.relative), 6),
            "weight": round(float(r.normalised_weight), 6),
            "contribution": round(float(r.contribution), 4),
            "is_imputed": bool(r.is_imputed),
            "imputation_method": r.imputation_method,
            "imputed_from": r.imputed_from,
            "n_quotes": int(r.n_quotes),
            "n_priced": int(r.n_priced),
            "n_soldout": int(r.n_soldout),
            "synthetic_share": round(float(r.synthetic_share), 4),
        }
        for r in grp.itertuples()
    ]
    # Sum the UNROUNDED contributions. Summing the display-rounded terms
    # accumulates ~1e-4 of error across 20 cells and makes the reconciliation
    # check fail against a number that is in fact exact.
    total = float(grp["contribution"].sum())
    published = float(
        result.daily.loc[result.daily["period"] == period, "index_value"].iloc[0]
    )
    return {
        "period": period,
        "formula": "Pushpak(t) = 100 x SUM_c [ w(c) x P(c,t)/P(c,0) ]   where w(c) = w_route x v_ap, renormalised over present cells",
        "base_dates": result.base_dates,
        "base_index_value": config.BASE_INDEX_VALUE,
        "terms": sorted(terms, key=lambda t: -t["contribution"]),
        "sum_of_contributions": round(total, 6),
        "published_index_value": round(published, 6),
        "reconciles": bool(abs(total - published) < 1e-9),
        "residual": float(total - published),
        "note": (
            "Per-term figures are rounded for display; the reconciliation "
            "compares unrounded sums. Adding the displayed contributions by "
            "hand will agree to about 4 decimal places."
        ),
        "weight_provenance": {
            "route_weights": config.ROUTE_WEIGHT_PROVENANCE,
            "ap_window_weights": config.AP_WINDOW_WEIGHT_PROVENANCE,
        },
    }
