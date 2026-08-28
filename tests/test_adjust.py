"""Component 8 — tests for chain-linking and seasonal adjustment.

These two treatments are the ones most likely to be waved through unexamined,
because both produce a plausible-looking series whatever they are fed. So the
tests pin down their *properties*: a geometric chain over unchanged prices must
return exactly to its starting value, an arithmetic chain over oscillating
prices must drift upward (that is the whole point of the diagnostic), and a
seasonal adjustment that cannot be estimated must say so instead of quietly
returning the unadjusted series as if it were adjusted.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pushpak import config
from pushpak.adjust import (
    ANNUAL_SEASONALITY_NOTE, LINK_FORMS, LINK_FREQUENCIES, MIN_DAYS_FOR_DOW,
    chain_drift, chain_link, chain_report, dow_factors, seasonal_summary,
    seasonally_adjust,
)
from pushpak.index import compute_index
from conftest import frame, make_quote


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def cells_from_prices(prices: dict[str, list[float]], start="2026-08-01") -> pd.DataFrame:
    """Build a minimal imputed-cells frame: {route_id: [price per day]}."""
    d0 = date.fromisoformat(start)
    n_cells = len(prices)
    rows = []
    for route, series in prices.items():
        for i, p in enumerate(series):
            rows.append({
                "observation_date": (d0 + timedelta(days=i)).isoformat(),
                "route_id": route,
                "advance_purchase_days": 7,
                "cell_price_final": float(p),
                "cell_weight": 1.0 / n_cells,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# chain-linking
# ---------------------------------------------------------------------------
def test_chain_starts_at_base_value():
    ch = chain_link(cells_from_prices({"BOM-DEL": [5000, 5100, 5200]}))
    assert ch["chained_index"].iloc[0] == pytest.approx(config.BASE_INDEX_VALUE)
    assert ch["link_factor"].iloc[0] == 1.0
    assert ch["n_linked_cells"].iloc[0] == 0


def test_chain_tracks_a_single_cell_exactly():
    """One cell, so the chained index is just the price relative x 100."""
    ch = chain_link(cells_from_prices({"BOM-DEL": [5000, 5500, 6050]}))
    assert ch["chained_index"].tolist() == pytest.approx([100.0, 110.0, 121.0])


def test_flat_prices_produce_no_movement():
    ch = chain_link(cells_from_prices({"BOM-DEL": [5000] * 10}))
    assert ch["chained_index"].tolist() == pytest.approx([100.0] * 10)


def test_geometric_chain_returns_to_start_when_prices_do():
    """A round trip in prices must be a round trip in a geometric chain.

    This is the defining property of the Jevons link and the reason it is
    offered: it has no chain drift.
    """
    cells = cells_from_prices({
        "BOM-DEL": [5000, 6000, 4500, 5200, 5000],
        "BLR-DEL": [4000, 3600, 4800, 4100, 4000],
    })
    ch = chain_link(cells, link_form="geometric")
    assert ch["chained_index"].iloc[-1] == pytest.approx(100.0, abs=1e-9)


def test_arithmetic_chain_drifts_upward_on_the_same_round_trip():
    """The arithmetic link does NOT return to 100 -- it drifts above it.

    A weighted arithmetic mean of relatives is >= their geometric mean, so
    every link contributes a small upward bias whenever the cells disagree.
    Pinning this down is what lets the API present drift as a measured
    property rather than an anomaly.
    """
    cells = cells_from_prices({
        "BOM-DEL": [5000, 6000, 4500, 5200, 5000],
        "BLR-DEL": [4000, 3600, 4800, 4100, 4000],
    })
    arithmetic = chain_link(cells, link_form="arithmetic")["chained_index"].iloc[-1]
    geometric = chain_link(cells, link_form="geometric")["chained_index"].iloc[-1]
    assert arithmetic > geometric
    assert arithmetic > 100.0


def test_link_frequency_reduces_the_number_of_links():
    cells = cells_from_prices({"BOM-DEL": [5000 + 50 * i for i in range(28)]})
    daily = chain_link(cells, link_frequency="daily")
    weekly = chain_link(cells, link_frequency="weekly")
    monthly = chain_link(cells, link_frequency="monthly")
    assert len(daily) == 28
    assert len(weekly) == 5          # 28 days spans five ISO weeks here
    assert len(monthly) == 1
    assert len(monthly) < len(weekly) < len(daily)


def test_a_cell_absent_for_a_day_links_from_its_own_last_observation():
    """Sample rotation is the reason chaining exists; it must not lose a cell."""
    cells = cells_from_prices({"BOM-DEL": [5000, 5000, 5000], "BLR-DEL": [4000, 4000, 4000]})
    # Delete BLR-DEL's middle day entirely.
    gap = cells[~((cells["route_id"] == "BLR-DEL")
                  & (cells["observation_date"] == "2026-08-02"))]
    ch = chain_link(gap)
    assert ch["n_linked_cells"].tolist() == [0, 1, 2]
    assert ch["chained_index"].iloc[-1] == pytest.approx(100.0)


def test_unknown_link_arguments_are_rejected():
    cells = cells_from_prices({"BOM-DEL": [5000, 5100]})
    with pytest.raises(ValueError, match="link frequency"):
        chain_link(cells, link_frequency="hourly")
    with pytest.raises(ValueError, match="link form"):
        chain_link(cells, link_form="harmonic")


def test_chain_link_on_empty_cells_returns_an_empty_frame():
    out = chain_link(pd.DataFrame())
    assert len(out) == 0
    assert "chained_index" in out.columns


def test_chain_drift_reports_both_endpoints():
    fixed = pd.DataFrame({"period": ["2026-08-01", "2026-08-02", "2026-08-03"],
                          "index_value": [100.0, 100.0, 100.0]})
    chained = pd.DataFrame({"period": ["2026-08-01", "2026-08-02", "2026-08-03"],
                            "chained_index": [100.0, 101.0, 102.0],
                            "link_factor": [1.0, 1.01, 1.0099],
                            "n_linked_cells": [0, 2, 2]})
    d = chain_drift(fixed, chained)
    assert d["available"] is True
    assert d["final_drift_index_points"] == pytest.approx(2.0)
    assert d["n_links"] == 2
    assert len(d["series"]) == 3


def test_chain_drift_needs_two_overlapping_periods():
    fixed = pd.DataFrame({"period": ["2026-08-01"], "index_value": [100.0]})
    chained = pd.DataFrame({"period": ["2026-08-09"], "chained_index": [100.0],
                            "link_factor": [1.0], "n_linked_cells": [0]})
    assert chain_drift(fixed, chained)["available"] is False


# ---------------------------------------------------------------------------
# seasonal adjustment
# ---------------------------------------------------------------------------
def _daily(values: list[float], start="2026-08-01") -> pd.DataFrame:
    d0 = date.fromisoformat(start)
    return pd.DataFrame({
        "period": [(d0 + timedelta(days=i)).isoformat() for i in range(len(values))],
        "index_value": values,
    })


def test_dow_factors_refuse_a_short_series():
    est = dow_factors(_daily([100.0] * (MIN_DAYS_FOR_DOW - 1)))
    assert est["available"] is False
    assert "day-of-week" in est["reason"]


def test_dow_factors_recover_a_known_weekly_pattern():
    """Inject a clean weekly cycle and check it comes back out."""
    pattern = [1.10, 0.95, 0.90, 1.00, 1.05, 1.02, 0.98]   # Sat..Fri from 2026-08-01
    d0 = date.fromisoformat("2026-08-01")
    values = [100.0 * pattern[(d0 + timedelta(days=i)).weekday()] for i in range(28)]
    est = dow_factors(_daily(values))

    assert est["available"] is True
    for wd, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        assert est["factors"][name] == pytest.approx(
            pattern[wd] / float(np.exp(np.mean(np.log(pattern)))), rel=1e-6
        )


def test_dow_factors_multiply_to_one():
    """Normalisation must redistribute the level, never shift it.

    The published factors are rounded to six decimals, so seven of them can
    only multiply to 1 within about 4e-6. The tolerance is set by that display
    rounding, not by any slack in the normalisation itself.
    """
    d0 = date.fromisoformat("2026-08-01")
    values = [100.0 + 5 * ((d0 + timedelta(days=i)).weekday() - 3) for i in range(28)]
    est = dow_factors(_daily(values))
    product = float(np.prod(list(est["factors"].values())))
    assert product == pytest.approx(1.0, abs=1e-5)


def test_adjustment_removes_a_pure_weekly_cycle():
    d0 = date.fromisoformat("2026-08-01")
    pattern = [1.10, 0.95, 0.90, 1.00, 1.05, 1.02, 0.98]
    values = [100.0 * pattern[(d0 + timedelta(days=i)).weekday()] for i in range(28)]
    sa = seasonally_adjust(_daily(values))
    assert sa["sa_applied"].all()
    assert sa["index_sa"].std(ddof=1) < sa["index_value"].std(ddof=1) / 10


def test_adjustment_falls_back_visibly_when_it_cannot_be_estimated():
    """A refused adjustment must be flagged, never silently passed through."""
    sa = seasonally_adjust(_daily([100.0, 101.0, 102.0]))
    assert not sa["sa_applied"].any()
    assert sa["dow_factor"].tolist() == [1.0, 1.0, 1.0]
    assert sa["index_sa"].tolist() == sa["index_value"].tolist()


def test_seasonal_summary_always_states_the_annual_caveat():
    """Whether or not adjustment ran, the reader is told what was NOT removed."""
    short = seasonal_summary(_daily([100.0, 101.0]))
    assert short["applied"] is False
    assert short["annual_seasonality"] == ANNUAL_SEASONALITY_NOTE

    d0 = date.fromisoformat("2026-08-01")
    long = seasonal_summary(_daily(
        [100.0 + (d0 + timedelta(days=i)).weekday() for i in range(28)]
    ))
    assert long["applied"] is True
    assert long["annual_seasonality"] == ANNUAL_SEASONALITY_NOTE
    assert "12 months" in long["annual_seasonality"]


def test_seasonal_summary_handles_an_empty_series():
    out = seasonal_summary(pd.DataFrame(columns=["period", "index_value"]))
    assert out["applied"] is False


# ---------------------------------------------------------------------------
# the combined report
# ---------------------------------------------------------------------------
def _synthetic_result(days=21):
    from pushpak.collect.synthetic import generate_backfill
    weights = pd.DataFrame([
        {"route_id": "BOM-DEL", "weight": 0.6},
        {"route_id": "BLR-DEL", "weight": 0.4},
    ])
    quotes = list(generate_backfill(
        date(2026, 8, 26), days, ["BOM-DEL", "BLR-DEL"],
        ap_windows=(7, 30), carriers=["6E"], sources=["synthetic-ota-a"],
    ))
    return compute_index(frame(quotes), route_weights=weights, windows=(7, 30))


def test_chain_report_covers_every_frequency_and_form():
    rep = chain_report(_synthetic_result())
    assert rep["headline_is_chained"] is False
    seen = {(v["link_frequency"], v.get("link_form")) for v in rep["variants"]
            if v.get("available")}
    for freq in LINK_FREQUENCIES:
        for form in LINK_FORMS:
            if freq == "monthly":
                continue                # a 21-day series may span one month only
            assert (freq, form) in seen


def test_chain_report_names_the_least_drifting_variant():
    rep = chain_report(_synthetic_result())
    assert rep["least_drift"] is not None
    assert rep["least_drift"]["link_form"] in LINK_FORMS
    best = abs(rep["least_drift"]["final_drift_index_points"])
    others = [abs(v["final_drift_index_points"]) for v in rep["variants"]
              if v.get("available")]
    assert best == pytest.approx(min(others))


def test_chain_report_explains_drift_rather_than_hiding_it():
    rep = chain_report(_synthetic_result())
    assert "not a bug" in rep["note"]
    assert "annually" in rep["note"]
