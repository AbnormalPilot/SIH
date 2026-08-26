"""Official CPI context (MoSPI Price Statistics Division dashboard extract).

What this module is for
-----------------------
Pushpak is meant to *feed into* the CPI process, not to replace or reproduce it.
This module loads the official MoSPI CPI dashboard workbook so the prototype
can show the two series side by side and make the frequency and granularity
gap concrete, which is the premise of the whole problem statement.

What the workbook actually contains
-----------------------------------
Only the **General Index (All Groups)**, All-India and by state. There is **no
transport sub-group and no airfare component in this file.**

That constrains what may honestly be said, and the constraint is enforced in
code rather than left to a footnote:

  * :func:`compare_to_pushpak` returns ``comparable_as_level = False`` and a
    ``caveat`` explaining why. Airfares are a small component of a headline
    index dominated by food and housing, so an airfare index and the general
    CPI are not expected to track each other and a correlation between them
    would not mean anything.
  * Nothing here is used by :mod:`pushpak.validate`. The back-test benchmark
    remains a separate, separately-labelled input.

What it *is* good for is the contrast the brief is built on: 19 monthly
observations since January 2025, against one Pushpak Index observation per day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import config

CPI_WORKBOOK = config.DATA_DIR / "official" / "cpi_dashboard_july2026.xlsx"
CPI_TIDY_CSV = config.DATA_DIR / "official" / "cpi_all_india.csv"
CPI_STATE_CSV = config.DATA_DIR / "official" / "cpi_statewise.csv"

SOURCE_NOTE = (
    "MoSPI Consumer Price Index dashboard extract, published 12 August 2026, "
    "covering data to July 2026."
)

#: The workbook itself does not state a base period. MoSPI's own release of
#: 12 February 2026 revised the CPI base from 2012=100 to 2024=100, and the
#: values here (101.67 in Jan 2025 rising to 107.94 in Jul 2026) are consistent
#: with that series. Recorded as an attribution to the published release rather
#: than as something read out of the file.
BASE_PERIOD_NOTE = (
    "2024=100, per the MoSPI CPI base revision released 12 February 2026. "
    "The workbook does not state this internally; it is attributed from the "
    "official release."
)

#: The workbook spells the same state differently across its sheets: casing
#: ("Andaman and" vs "Andaman And"), footnote markers ("Chandigarh*"), the old
#: pre-merger UT names, and "Delhi" vs "NCT of Delhi". Left alone, one state
#: appears as two rows and the state count comes out wrong. Same class of
#: problem as the DGCA Mumbai rename handled in config.DGCA_CITY_ALIASES.
STATE_ALIASES = {
    "delhi": "NCT of Delhi",
    "dadra and nagar haveli": "The Dadra And Nagar Haveli And Daman And Diu",
    "daman and diu": "The Dadra And Nagar Haveli And Daman And Diu",
    "the dadra and nagar haveli and daman and diu":
        "The Dadra And Nagar Haveli And Daman And Diu",
}

#: Canonical spelling, applied after case folding so the two sheets agree.
CANONICAL_STATE = {
    "andaman and nicobar islands": "Andaman And Nicobar Islands",
    "jammu and kashmir": "Jammu And Kashmir",
    "nct of delhi": "NCT of Delhi",
}


def normalise_state(name) -> str:
    """One spelling per state, across sheets."""
    if pd.isna(name):
        return name
    s = str(name).strip().rstrip("*").strip()
    key = s.lower()
    if key in STATE_ALIASES:
        return STATE_ALIASES[key]
    if key in CANONICAL_STATE:
        return CANONICAL_STATE[key]
    return s


MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

SERIES_SHEETS = {
    "rural": "CPI- Rural ",
    "urban": "CPI- Urban ",
    "combined": "CPI Combined ",
}


def _month_num(v) -> Optional[int]:
    if pd.isna(v):
        return None
    return MONTHS.get(str(v).strip().lower())


# ---------------------------------------------------------------------------
def parse_workbook(path: Optional[Path] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse the workbook into two tidy frames: (all_india, statewise).

    The sheets carry a title row above the real header and inconsistent
    trailing spaces in sheet names, so the parsing is explicit rather than
    relying on pandas defaults.
    """
    path = Path(path or CPI_WORKBOOK)
    if not path.exists():
        raise FileNotFoundError(
            f"CPI workbook missing: {path}. It ships in data/official/."
        )

    frames = []
    for series, sheet in SERIES_SHEETS.items():
        df = pd.read_excel(path, sheet_name=sheet, header=1)
        df.columns = [str(c).strip() for c in df.columns]
        value_col = next(
            (c for c in df.columns if c.lower() == series), df.columns[-1]
        )
        df = df[["Year", "Month", "State", "Description", value_col]].copy()
        df = df.rename(columns={value_col: "index_value"})
        df["series"] = series
        frames.append(df)

    ai = pd.concat(frames, ignore_index=True)
    ai = ai[ai["Year"].notna() & ai["index_value"].notna()].copy()
    ai["month_num"] = ai["Month"].map(_month_num)
    ai = ai[ai["month_num"].notna()]
    ai["Year"] = ai["Year"].astype(int)
    ai["month_num"] = ai["month_num"].astype(int)
    ai["period"] = (
        ai["Year"].astype(str) + "-" + ai["month_num"].astype(str).str.zfill(2)
    )
    ai["index_value"] = pd.to_numeric(ai["index_value"], errors="coerce")
    ai = ai.dropna(subset=["index_value"])
    ai = ai.rename(columns={"Description": "description", "State": "state"})
    ai = ai[["period", "Year", "month_num", "state", "description", "series",
             "index_value"]].sort_values(["series", "period"])

    # ---- state-wise sheet: two side-by-side blocks (inflation, then index) --
    sw_raw = pd.read_excel(path, sheet_name="StateWise-CPI Index")
    sw_raw.columns = [str(c).strip() for c in sw_raw.columns]

    left = sw_raw[["Year", "Month", "State", "Description",
                   "Rural", "Urban", "Combined"]].copy()
    left["measure"] = "inflation_pct"

    # The right-hand block repeats the columns with a ".1" suffix, EXCEPT the
    # state column, which pandas names "Unnamed: 10" because its header cell is
    # blank. Selecting only ".1" columns silently drops the state and the whole
    # block is then filtered away as stateless, so it has to be named explicitly.
    right_cols = [c for c in sw_raw.columns if c.endswith(".1")]
    state_col = next(
        (c for c in sw_raw.columns
         if c.startswith("Unnamed:") and sw_raw.columns.get_loc(c) > 7),
        None,
    )
    if state_col:
        right_cols = right_cols + [state_col]
    right = sw_raw[right_cols].copy()
    right.columns = [
        (c[:-2] if c.endswith(".1") else "State") for c in right.columns
    ]
    keep = [c for c in ("Year", "Month", "State", "Description",
                        "Rural", "Urban", "Combined") if c in right.columns]
    right = right[keep].copy()
    right["measure"] = "index_value"

    sw = pd.concat([left, right], ignore_index=True)
    sw["State"] = sw["State"].map(normalise_state)
    sw = sw[sw["Year"].notna() & sw["State"].notna()].copy()
    sw["month_num"] = sw["Month"].map(_month_num)
    sw = sw[sw["month_num"].notna()]
    sw["Year"] = sw["Year"].astype(int)
    sw["month_num"] = sw["month_num"].astype(int)
    sw["period"] = (
        sw["Year"].astype(str) + "-" + sw["month_num"].astype(str).str.zfill(2)
    )
    sw = sw.rename(columns={"State": "state", "Description": "description",
                            "Rural": "rural", "Urban": "urban",
                            "Combined": "combined"})
    sw = sw[["period", "Year", "month_num", "state", "description", "measure",
             "rural", "urban", "combined"]]

    # The state-wise index sheet stops at December 2025, but the workbook
    # carries a separate wide-format sheet with state inflation through July
    # 2026. Fold it in so the state view is not eight months stale.
    try:
        wide = pd.read_excel(path, sheet_name="StateWise-InflationRate(%)")
        wide.columns = [str(c).strip() for c in wide.columns]
        state_cols = [c for c in wide.columns if c not in ("Year", "Month")]
        long = wide.melt(
            id_vars=["Year", "Month"], value_vars=state_cols,
            var_name="state", value_name="combined",
        )
        long = long[long["Year"].notna() & long["combined"].notna()].copy()
        long["month_num"] = long["Month"].map(_month_num)
        long = long[long["month_num"].notna()]
        long["Year"] = long["Year"].astype(int)
        long["month_num"] = long["month_num"].astype(int)
        long["period"] = (
            long["Year"].astype(str) + "-" + long["month_num"].astype(str).str.zfill(2)
        )
        long["state"] = long["state"].map(normalise_state)
        long["description"] = "General Index (All Groups)"
        long["measure"] = "inflation_pct"
        long["rural"] = np.nan
        long["urban"] = np.nan
        long = long[["period", "Year", "month_num", "state", "description",
                     "measure", "rural", "urban", "combined"]]
        # Prefer the wide sheet where both cover the same state-month.
        sw = pd.concat([sw, long], ignore_index=True)
        sw = sw.drop_duplicates(subset=["measure", "period", "state"], keep="last")
    except (ValueError, KeyError):
        pass    # sheet absent in a future workbook revision; the rest still works

    sw = sw.sort_values(["measure", "period", "state"])
    return ai.reset_index(drop=True), sw.reset_index(drop=True)


def build_tidy_csvs(path: Optional[Path] = None) -> tuple[Path, Path]:
    """Write the tidy CSVs the API and dashboard read. Re-runnable."""
    ai, sw = parse_workbook(path)
    CPI_TIDY_CSV.parent.mkdir(parents=True, exist_ok=True)
    ai.to_csv(CPI_TIDY_CSV, index=False)
    sw.to_csv(CPI_STATE_CSV, index=False)
    return CPI_TIDY_CSV, CPI_STATE_CSV


def load_all_india(series: str = "combined") -> pd.DataFrame:
    if not CPI_TIDY_CSV.exists():
        build_tidy_csvs()
    df = pd.read_csv(CPI_TIDY_CSV)
    return df[df["series"] == series].sort_values("period").reset_index(drop=True)


def load_statewise(measure: str = "index_value") -> pd.DataFrame:
    if not CPI_STATE_CSV.exists():
        build_tidy_csvs()
    df = pd.read_csv(CPI_STATE_CSV)
    return df[df["measure"] == measure].reset_index(drop=True)


# ---------------------------------------------------------------------------
def cpi_inflation(series: str = "combined") -> pd.DataFrame:
    """Month-on-month and year-on-year change of the official index."""
    df = load_all_india(series).copy()
    df["mom_pct"] = df["index_value"].pct_change() * 100
    df["yoy_pct"] = df["index_value"].pct_change(12) * 100
    return df


@dataclass
class FrequencyGap:
    """The measurable version of the problem statement's premise."""
    cpi_observations: int
    cpi_span_days: int
    cpi_observations_per_year: float
    pushpak_observations: int
    pushpak_span_days: int
    pushpak_observations_per_year: float

    @property
    def frequency_multiple(self) -> float:
        if not self.cpi_observations_per_year:
            return float("nan")
        return self.pushpak_observations_per_year / self.cpi_observations_per_year

    def as_dict(self) -> dict:
        return {
            "cpi": {
                "observations": self.cpi_observations,
                "span_days": self.cpi_span_days,
                "observations_per_year": round(self.cpi_observations_per_year, 2),
                "granularity": "All-India general index, monthly",
            },
            "pushpak": {
                "observations": self.pushpak_observations,
                "span_days": self.pushpak_span_days,
                "observations_per_year": round(self.pushpak_observations_per_year, 2),
                "granularity": (
                    "5 city pairs x 4 advance-purchase windows, daily"
                ),
            },
            "frequency_multiple": round(self.frequency_multiple, 1),
            "statement": (
                f"Pushpak Index produces roughly {self.frequency_multiple:.0f} times as many "
                f"observations per year as the monthly CPI release, and resolves "
                f"them to individual routes and booking windows."
            ),
        }


def frequency_gap(pushpak_daily: pd.DataFrame, series: str = "combined") -> FrequencyGap:
    cpi = load_all_india(series)
    cpi_dates = pd.to_datetime(cpi["period"] + "-01")
    cpi_span = max((cpi_dates.max() - cpi_dates.min()).days, 1)

    ap_dates = pd.to_datetime(pushpak_daily["period"]) if len(pushpak_daily) else pd.Series(dtype="datetime64[ns]")
    ap_span = max((ap_dates.max() - ap_dates.min()).days, 1) if len(ap_dates) else 1

    return FrequencyGap(
        cpi_observations=len(cpi),
        cpi_span_days=cpi_span,
        cpi_observations_per_year=len(cpi) / cpi_span * 365.25,
        pushpak_observations=len(pushpak_daily),
        pushpak_span_days=ap_span,
        pushpak_observations_per_year=len(pushpak_daily) / ap_span * 365.25,
    )


def compare_to_pushpak(pushpak_daily: pd.DataFrame, series: str = "combined") -> dict:
    """Put Pushpak Index beside the official CPI, with the comparison's limits stated.

    Deliberately returns ``comparable_as_level = False``. The workbook has no
    transport sub-index, so the only official series available is a headline
    index dominated by food and housing. Correlating an airfare index against
    it would produce a number with no interpretation, so no correlation is
    computed here at all.
    """
    cpi = cpi_inflation(series)

    pushpak_monthly = pd.DataFrame(columns=["period", "pushpak_index", "n_days"])
    if len(pushpak_daily):
        d = pushpak_daily.copy()
        d["period"] = pd.to_datetime(d["period"]).dt.strftime("%Y-%m")
        pushpak_monthly = (
            d.groupby("period")
            .agg(pushpak_index=("index_value", "mean"), n_days=("index_value", "size"))
            .reset_index()
        )

    overlap = cpi.merge(pushpak_monthly, on="period", how="inner")

    return {
        "official_series": {
            "name": "CPI General Index (All Groups), All India",
            "variant": series,
            "source": SOURCE_NOTE,
            "base_period": BASE_PERIOD_NOTE,
            "n_observations": len(cpi),
            "period_start": cpi["period"].iloc[0] if len(cpi) else None,
            "period_end": cpi["period"].iloc[-1] if len(cpi) else None,
            "series": [
                {
                    "period": r.period,
                    "index_value": round(float(r.index_value), 2),
                    "mom_pct": None if pd.isna(r.mom_pct) else round(float(r.mom_pct), 3),
                    "yoy_pct": None if pd.isna(r.yoy_pct) else round(float(r.yoy_pct), 3),
                }
                for r in cpi.itertuples()
            ],
        },
        "pushpak_monthly": [
            {"period": r.period, "index_value": round(float(r.pushpak_index), 4),
             "n_days": int(r.n_days)}
            for r in pushpak_monthly.itertuples()
        ],
        "overlapping_months": len(overlap),
        "comparable_as_level": False,
        "caveat": (
            "NOT A VALIDATION AND NOT A LIKE-FOR-LIKE COMPARISON. The workbook "
            "contains only the CPI General Index (All Groups); it has no "
            "transport sub-group and no airfare component. A headline index "
            "dominated by food and housing should not be expected to track an "
            "airfare index, so no correlation is computed. The two series are "
            "shown together to illustrate collection frequency and granularity, "
            "and because the general index is the process Pushpak Index would feed into "
            "as one input among many."
        ),
        "frequency_gap": frequency_gap(pushpak_daily, series).as_dict(),
    }


def statewise_latest(measure: str = "inflation_pct") -> dict:
    """Latest month's state-wise figures. Extra official statistics context."""
    df = load_statewise(measure)
    if not len(df):
        return {"measure": measure, "period": None, "states": []}
    latest = df["period"].max()
    cur = df[df["period"] == latest].copy()
    cur = cur.sort_values("combined", ascending=False, na_position="last")
    return {
        "measure": measure,
        "period": latest,
        "source": SOURCE_NOTE,
        "n_states": int(cur["state"].nunique()),
        "states": [
            {
                "state": r.state,
                "rural": None if pd.isna(r.rural) else round(float(r.rural), 2),
                "urban": None if pd.isna(r.urban) else round(float(r.urban), 2),
                "combined": None if pd.isna(r.combined) else round(float(r.combined), 2),
            }
            for r in cur.itertuples()
        ],
    }
