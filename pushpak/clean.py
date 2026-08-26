"""Component 2 — normalisation, validation and CPI-standard imputation.

This module and pushpak/index.py are the two a reviewer will read most closely,
so both are written to be legible rather than clever, and both are covered by
tests/test_clean.py and tests/test_index.py.

The pipeline, in order:

  1. **Normalise**   — canonical route ids, upper-cased codes, typed columns.
  2. **Validate**    — schema invariants; violations are quarantined, not dropped
                       silently, and counted in the returned report.
  3. **Screen**      — implausible fares (outliers) are re-flagged
                       OUTLIER_REJECTED rather than deleted, so the audit trail
                       shows what was excluded and why.
  4. **Aggregate**   — quotes collapse to elementary-aggregate *cells*
                       (route x advance-purchase window x observation date).
  5. **Impute**      — missing cells are filled by an explicit, documented rule
                       and every filled value is flagged.

Why sold-out is not "missing"
-----------------------------
A SOLD_OUT quote is information: the cheapest advertised fare was unobtainable.
Dropping those rows biases an airfare index *downward*, because inventory sells
out precisely when demand and price are high. They are therefore carried into
the cell as unpriced observations, counted in ``soldout_share``, and — if a
whole cell is sold out — imputed by the same rules as any other gap, with the
reason recorded.

The imputation ladder (mirrors standard CPI compilation practice)
-----------------------------------------------------------------
For a missing cell price on day *t*, in strict order:

  1. ``CARRY_FORWARD``  — last observed price for that cell, provided it is no
     more than ``MAX_CARRY_FORWARD_DAYS`` old. This is the price-statistics
     convention of carrying the last valid quote forward for a temporarily
     unavailable item.
  2. ``CLASS_MEAN``     — impute the movement, not the level. Take the cell's
     own base price and move it by the average relative change of the *other*
     observed cells on the same route that day:
         P̂(c,t) = P(c,0) x mean over observed cells c' of the route of P(c',t)/P(c',0)
     This is the CPI "class-mean / overall-mean imputation" rule. It is
     preferred to a raw cross-sectional mean because cells differ in level
     (a T+1 fare is structurally higher than a T+30 fare), so imputing the
     *level* would introduce a jump; imputing the *movement* does not.
  3. ``BASE_CARRY``     — if the route has no observed cell at all that day,
     hold the cell at its base value (relative = 1.0). Deliberately last
     resort: it damps the index toward no-change, so it is counted separately
     and surfaced in the quality flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import config
from .schema import (
    COLUMNS, ImputationMethod, PRICED_STATUSES, QuoteStatus,
    canonical_route_id,
)


@dataclass
class CleaningReport:
    """Everything a reviewer needs to know about what cleaning did."""
    rows_in: int = 0
    rows_out: int = 0
    quarantined: int = 0
    quarantine_reasons: dict[str, int] = field(default_factory=dict)
    outliers_rejected: int = 0
    priced: int = 0
    soldout: int = 0
    unavailable: int = 0          # NO_SERVICE / CANCELLED
    blocked: int = 0              # BLOCKED_BY_ROBOTS
    failed: int = 0               # FETCH_FAILED / PARSE_FAILED
    synthetic: int = 0
    live: int = 0

    def as_dict(self) -> dict:
        return {
            "rows_in": self.rows_in, "rows_out": self.rows_out,
            "quarantined": self.quarantined,
            "quarantine_reasons": self.quarantine_reasons,
            "outliers_rejected": self.outliers_rejected,
            "priced": self.priced, "soldout": self.soldout,
            "unavailable": self.unavailable, "blocked": self.blocked,
            "failed": self.failed,
            "synthetic": self.synthetic, "live": self.live,
            "synthetic_share": (self.synthetic / self.rows_out) if self.rows_out else 0.0,
        }


# ---------------------------------------------------------------------------
# 1-3. normalise, validate, screen
# ---------------------------------------------------------------------------
def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Type and canonicalise a raw quote frame. Pure; returns a new frame."""
    if not len(df):
        return df.copy()
    out = df.copy()

    for col in ("origin", "destination", "carrier", "fare_class"):
        if col in out:
            out[col] = out[col].fillna("").astype(str).str.upper().str.strip()

    out["route_id"] = [
        canonical_route_id(o, d) for o, d in zip(out["origin"], out["destination"])
    ]

    for col in ("base_fare", "taxes_fees", "total_fare"):
        out[col] = pd.to_numeric(out.get(col), errors="coerce")

    out["advance_purchase_days"] = pd.to_numeric(
        out["advance_purchase_days"], errors="coerce"
    ).astype("Int64")

    for col in ("observation_date", "travel_date"):
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d")

    out["is_imputed"] = out.get("is_imputed", False).astype(bool)
    out["quote_status"] = out["quote_status"].astype(str)
    out["collection_mode"] = out["collection_mode"].astype(str)
    return out


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Split into (clean, quarantined, reason counts). Nothing is deleted."""
    if not len(df):
        return df.copy(), df.copy(), {}

    reasons = pd.Series("", index=df.index)

    def flag(mask: pd.Series, label: str) -> None:
        hit = mask & (reasons == "")
        reasons.loc[hit] = label

    priced = df["quote_status"].isin([s.value for s in PRICED_STATUSES])

    flag(priced & df["total_fare"].isna(), "priced_but_null_fare")
    flag(~priced & df["total_fare"].notna(), "unpriced_but_has_fare")
    flag(df["total_fare"].notna() & (df["total_fare"] <= 0), "non_positive_fare")
    flag(df["observation_date"].isna() | df["travel_date"].isna(), "bad_dates")
    flag(df["advance_purchase_days"].isna() | (df["advance_purchase_days"] < 0),
         "bad_ap_days")

    # base + taxes must reconcile to total, within a rupee of rounding
    both = df["base_fare"].notna() & df["taxes_fees"].notna() & df["total_fare"].notna()
    mismatch = both & (
        (df["base_fare"].fillna(0) + df["taxes_fees"].fillna(0)
         - df["total_fare"].fillna(0)).abs() > 1.0
    )
    flag(mismatch, "fare_components_do_not_sum")

    bad = reasons != ""
    counts = reasons[bad].value_counts().to_dict()
    return df[~bad].copy(), df[bad].assign(quarantine_reason=reasons[bad]).copy(), counts


def screen_outliers(
    df: pd.DataFrame, multiple: float = config.OUTLIER_MULTIPLE
) -> tuple[pd.DataFrame, int]:
    """Re-flag implausible fares as OUTLIER_REJECTED (kept, not deleted).

    A fare is implausible if it exceeds ``multiple`` x the median of its own
    cell, or falls below 1/``multiple`` of it. Screening within the cell (not
    globally) matters: a T+1 fare that is legitimately triple a T+30 fare must
    not be screened out as an outlier.
    """
    if not len(df):
        return df, 0
    out = df.copy()
    priced = out["quote_status"].isin([s.value for s in PRICED_STATUSES]) \
        & out["total_fare"].notna()
    if not priced.any():
        return out, 0

    key = ["observation_date", "route_id", "advance_purchase_days"]
    med = out.loc[priced].groupby(key)["total_fare"].transform("median")
    hi = out.loc[priced, "total_fare"] > med * multiple
    lo = out.loc[priced, "total_fare"] < med / multiple
    bad_idx = out.loc[priced].index[(hi | lo).fillna(False)]

    out.loc[bad_idx, "quote_status"] = QuoteStatus.OUTLIER_REJECTED.value
    out.loc[bad_idx, "notes"] = (
        out.loc[bad_idx, "notes"].fillna("").astype(str)
        + f" | screened: outside {multiple}x cell median"
    )
    out.loc[bad_idx, ["total_fare", "base_fare", "taxes_fees"]] = np.nan
    return out, len(bad_idx)


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, CleaningReport]:
    """Full clean. Returns (clean_quotes, quarantined, report)."""
    rep = CleaningReport(rows_in=len(df))
    if not len(df):
        return df.copy(), df.copy(), rep

    norm = normalise(df)
    ok, quarantined, reasons = validate(norm)
    ok, n_out = screen_outliers(ok)

    rep.rows_out = len(ok)
    rep.quarantined = len(quarantined)
    rep.quarantine_reasons = reasons
    rep.outliers_rejected = n_out
    st = ok["quote_status"]
    rep.priced = int((st == QuoteStatus.OK.value).sum())
    rep.soldout = int((st == QuoteStatus.SOLD_OUT.value).sum())
    rep.unavailable = int(st.isin([QuoteStatus.NO_SERVICE.value,
                                   QuoteStatus.CANCELLED.value]).sum())
    rep.blocked = int((st == QuoteStatus.BLOCKED_BY_ROBOTS.value).sum())
    rep.failed = int(st.isin([QuoteStatus.FETCH_FAILED.value,
                              QuoteStatus.PARSE_FAILED.value]).sum())
    rep.synthetic = int((ok["collection_mode"] == "SYNTHETIC").sum())
    rep.live = int((ok["collection_mode"] == "LIVE_SCRAPE").sum())
    return ok, quarantined, rep


# ---------------------------------------------------------------------------
# 4. aggregate to elementary cells
# ---------------------------------------------------------------------------
def build_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse quotes to one row per (observation_date, route, AP window).

    The cell price is the **arithmetic mean of the priced quotes in the cell**.
    A ratio of such means across periods is a Dutot elementary index — the
    simplest defensible elementary aggregate, and the one a reviewer can
    reproduce by hand from the raw table, which the brief explicitly requires.
    """
    if not len(df):
        return pd.DataFrame(columns=[
            "observation_date", "route_id", "advance_purchase_days", "cell_price",
            "n_quotes", "n_priced", "n_soldout", "n_synthetic", "n_live",
            "synthetic_share", "soldout_share", "carriers", "sources",
        ])

    d = df.copy()
    d["_priced"] = d["quote_status"] == QuoteStatus.OK.value
    d["_soldout"] = d["quote_status"] == QuoteStatus.SOLD_OUT.value
    d["_syn"] = d["collection_mode"] == "SYNTHETIC"
    d["_live"] = d["collection_mode"] == "LIVE_SCRAPE"
    d["_fare"] = d["total_fare"].where(d["_priced"])

    key = ["observation_date", "route_id", "advance_purchase_days"]
    g = d.groupby(key, dropna=False)
    cells = g.agg(
        cell_price=("_fare", "mean"),
        n_quotes=("quote_id", "count"),
        n_priced=("_priced", "sum"),
        n_soldout=("_soldout", "sum"),
        n_synthetic=("_syn", "sum"),
        n_live=("_live", "sum"),
        carriers=("carrier", lambda s: len(set(x for x in s if x))),
        sources=("source", lambda s: len(set(s))),
    ).reset_index()

    cells["synthetic_share"] = cells["n_synthetic"] / cells["n_quotes"].where(cells["n_quotes"] > 0)
    cells["soldout_share"] = cells["n_soldout"] / cells["n_quotes"].where(cells["n_quotes"] > 0)
    cells["is_thin"] = cells["n_priced"] < config.MIN_QUOTES_PER_CELL
    # A cell with zero priced quotes has no price: mean of an empty set is NaN.
    cells.loc[cells["n_priced"] == 0, "cell_price"] = np.nan
    cells["advance_purchase_days"] = cells["advance_purchase_days"].astype(int)
    return cells.sort_values(["observation_date", "route_id", "advance_purchase_days"])


# ---------------------------------------------------------------------------
# 5. imputation
# ---------------------------------------------------------------------------
def impute_cells(
    cells: pd.DataFrame,
    base_prices: pd.Series,
    max_carry_days: int = config.MAX_CARRY_FORWARD_DAYS,
) -> pd.DataFrame:
    """Fill missing cell prices using the documented CPI imputation ladder.

    ``base_prices`` is indexed by (route_id, advance_purchase_days) and holds
    the base-period price for each cell — needed by the CLASS_MEAN and
    BASE_CARRY rules, which impute a *movement* applied to the base level.

    Returns ``cells`` plus:
      ``cell_price_final``, ``is_imputed``, ``imputation_method``, ``imputed_from``
    """
    if not len(cells):
        out = cells.copy()
        for c, v in (("cell_price_final", np.nan), ("is_imputed", False),
                     ("imputation_method", ImputationMethod.NONE.value),
                     ("imputed_from", None)):
            out[c] = v
        return out

    out = cells.sort_values(
        ["route_id", "advance_purchase_days", "observation_date"]
    ).copy()
    out["cell_price_final"] = out["cell_price"]
    out["is_imputed"] = False
    out["imputation_method"] = ImputationMethod.NONE.value
    out["imputed_from"] = None

    dates = sorted(out["observation_date"].unique())
    # last observed (price, date) per cell, for CARRY_FORWARD
    last_seen: dict[tuple[str, int], tuple[float, str]] = {}

    for day in dates:
        day_mask = out["observation_date"] == day
        day_rows = out.loc[day_mask]

        # --- route-level average relative movement, from OBSERVED cells only.
        # Computed before any imputation on this day so imputed values never
        # feed the imputation of other cells on the same day.
        route_rel: dict[str, float] = {}
        for route, grp in day_rows.groupby("route_id"):
            rels = []
            for r in grp.itertuples():
                if pd.notna(r.cell_price):
                    b = base_prices.get((r.route_id, r.advance_purchase_days))
                    if b and b > 0:
                        rels.append(r.cell_price / b)
            if rels:
                route_rel[route] = float(np.mean(rels))

        for idx, r in day_rows.iterrows():
            cell_key = (r.route_id, int(r.advance_purchase_days))

            if pd.notna(r.cell_price):
                last_seen[cell_key] = (float(r.cell_price), day)
                continue

            # --- rule 1: carry forward -----------------------------------
            prev = last_seen.get(cell_key)
            if prev is not None:
                age = (pd.Timestamp(day) - pd.Timestamp(prev[1])).days
                if age <= max_carry_days:
                    out.at[idx, "cell_price_final"] = prev[0]
                    out.at[idx, "is_imputed"] = True
                    out.at[idx, "imputation_method"] = ImputationMethod.CARRY_FORWARD.value
                    out.at[idx, "imputed_from"] = f"last observed {prev[1]} ({age}d old)"
                    continue

            base = base_prices.get(cell_key)

            # --- rule 2: class-mean (impute the movement) ----------------
            rel = route_rel.get(r.route_id)
            if base and base > 0 and rel is not None:
                out.at[idx, "cell_price_final"] = base * rel
                out.at[idx, "is_imputed"] = True
                out.at[idx, "imputation_method"] = ImputationMethod.CLASS_MEAN.value
                out.at[idx, "imputed_from"] = (
                    f"base x mean relative of observed {r.route_id} cells "
                    f"on {day} (x{rel:.4f})"
                )
                continue

            # --- rule 3: hold at base ------------------------------------
            if base and base > 0:
                out.at[idx, "cell_price_final"] = base
                out.at[idx, "is_imputed"] = True
                out.at[idx, "imputation_method"] = ImputationMethod.BASE_CARRY.value
                out.at[idx, "imputed_from"] = "held at base period (relative = 1.0)"
            # else: cell has no base either -> stays NaN and is excluded,
            # with its absence reported through n_cells vs expected cells.

    return out.sort_values(["observation_date", "route_id", "advance_purchase_days"])
