"""Component 8 — tests for the derived statistics.

The attribution and diffusion measures are the ones that could quietly lie, so
they are pinned against hand-built series with known answers.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pushpak.index import compute_index
from pushpak.stats import (
    changes, diffusion, fare_dispersion, inflation_summary, route_contributions,
    summary, volatility,
)
from conftest import frame, make_quote


def _build(prices_by_route_day, windows=(7, 15), weights=None):
    """prices_by_route_day: {route: {day: price}} -> IndexResult."""
    qs = []
    for route, days in prices_by_route_day.items():
        o, d = route.split("-")
        for day, price in days.items():
            for ap in windows:
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=float(price),
                ))
    return compute_index(frame(qs), weights, windows=windows, base_days=1)


# ---------------------------------------------------------------------------
def test_changes_computes_expected_columns(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1100},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1100}},
        weights=two_route_weights,
    )
    d = changes(res.daily)
    assert np.isclose(d["change_pct"].iloc[1], 10.0)
    assert np.isclose(d["change_abs"].iloc[1], 10.0)


def test_inflation_reports_since_base_and_refuses_yoy(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1200},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1200}},
        weights=two_route_weights,
    )
    inf = inflation_summary(res.daily)
    assert inf["available"] is True
    assert np.isclose(inf["since_base_pct"], 20.0)
    # A 2-day series cannot produce a year-on-year rate, and must not invent one.
    assert inf["year_on_year_pct"] is None
    assert "366" in inf["year_on_year_note"]
    assert inf["annualised_is_projection"] is True


def test_inflation_unavailable_on_a_single_period(two_route_weights):
    res = _build({"BOM-DEL": {"2026-08-01": 1000}, "BLR-DEL": {"2026-08-01": 1000}},
                 weights=two_route_weights)
    assert inflation_summary(res.daily)["available"] is False


def test_flat_series_has_zero_volatility(two_route_weights):
    days = {f"2026-08-{d:02d}": 1000 for d in range(1, 8)}
    res = _build({"BOM-DEL": dict(days), "BLR-DEL": dict(days)},
                 weights=two_route_weights)
    v = volatility(res.daily)
    assert v["available"] is True
    assert np.isclose(v["daily_stdev_pct"], 0.0)
    assert np.isclose(v["mean_abs_daily_change_pct"], 0.0)


def test_volatility_rises_with_a_noisier_series(two_route_weights):
    calm = {f"2026-08-{d:02d}": 1000 + d for d in range(1, 9)}
    wild = {f"2026-08-{d:02d}": 1000 + (400 if d % 2 else -300) for d in range(1, 9)}
    v_calm = volatility(_build({"BOM-DEL": dict(calm), "BLR-DEL": dict(calm)},
                               weights=two_route_weights).daily)
    v_wild = volatility(_build({"BOM-DEL": dict(wild), "BLR-DEL": dict(wild)},
                               weights=two_route_weights).daily)
    assert v_wild["daily_stdev_pct"] > v_calm["daily_stdev_pct"]


# ---------------------------------------------------------------------------
def test_route_contributions_sum_to_the_total_change(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1300},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 900}},
        weights=two_route_weights,
    )
    a = route_contributions(res.cells, "2026-08-02")
    assert a["available"] is True
    total = sum(r["contribution_points"] for r in a["routes"])
    assert abs(total - a["total_change_points"]) < 1e-6
    # and it must equal the actual index move
    move = res.daily["index_value"].iloc[1] - res.daily["index_value"].iloc[0]
    assert abs(a["total_change_points"] - move) < 1e-6


def test_attribution_credits_the_heavier_route_correctly(two_route_weights):
    """Only the 75%-weight route moves, so it must own the entire change."""
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1200},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1000}},
        weights=two_route_weights,
    )
    a = route_contributions(res.cells, "2026-08-02")
    by = {r["route_id"]: r for r in a["routes"]}
    assert np.isclose(by["BOM-DEL"]["contribution_points"], 15.0)   # 0.75 x 20
    assert np.isclose(by["BLR-DEL"]["contribution_points"], 0.0)
    assert np.isclose(by["BOM-DEL"]["share_of_change"], 1.0)


def test_attribution_needs_a_previous_period(two_route_weights):
    res = _build({"BOM-DEL": {"2026-08-01": 1000}, "BLR-DEL": {"2026-08-01": 1000}},
                 weights=two_route_weights)
    assert route_contributions(res.cells, "2026-08-01")["available"] is False


# ---------------------------------------------------------------------------
def test_diffusion_is_100_when_every_cell_rises(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1100},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1050}},
        weights=two_route_weights,
    )
    d = diffusion(res.cells, "2026-08-02")
    assert d["diffusion_index"] == 100.0
    assert d["cells_down"] == 0


def test_diffusion_separates_a_narrow_rise_from_a_broad_one(two_route_weights):
    """A rise driven by one route must not look broad-based."""
    narrow = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1600},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 980}},
        weights=two_route_weights,
    )
    broad = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1100},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1120}},
        weights=two_route_weights,
    )
    # both indices rise
    assert narrow.daily["index_value"].iloc[1] > 100
    assert broad.daily["index_value"].iloc[1] > 100
    # but only one is broad-based
    assert diffusion(narrow.cells, "2026-08-02")["diffusion_index"] == 50.0
    assert diffusion(broad.cells, "2026-08-02")["diffusion_index"] == 100.0


# ---------------------------------------------------------------------------
def test_dispersion_reports_per_window_spread(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 5000}, "BLR-DEL": {"2026-08-01": 9000}},
        weights=two_route_weights,
    )
    disp = fare_dispersion(res.cells)
    assert disp["available"] is True
    assert {w["advance_purchase_days"] for w in disp["by_window"]} == {7, 15}
    for w in disp["by_window"]:
        assert np.isclose(w["mean_fare"], 7000.0)
        assert w["p90_p10_ratio"] > 1.0


def test_summary_assembles_every_block(two_route_weights):
    res = _build(
        {"BOM-DEL": {"2026-08-01": 1000, "2026-08-02": 1100},
         "BLR-DEL": {"2026-08-01": 1000, "2026-08-02": 1050}},
        weights=two_route_weights,
    )
    s = summary(res.daily, res.cells)
    for block in ("inflation", "volatility", "dispersion", "attribution", "diffusion"):
        assert block in s
    assert s["period"] == "2026-08-02"


def test_empty_input_does_not_explode():
    empty = pd.DataFrame(columns=["period", "index_value"])
    assert inflation_summary(empty)["available"] is False
    assert volatility(empty)["available"] is False
    assert fare_dispersion(pd.DataFrame())["available"] is False
