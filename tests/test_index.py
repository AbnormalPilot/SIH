"""Component 8 — tests for the index arithmetic.

The index is the number a reviewer will challenge, so these tests pin down its
*properties*, not just that it runs: base = 100, correct response to a known
price change, correct weighting, scale invariance, and exact reconciliation
between the published value and its per-cell decomposition.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pushpak import config
from pushpak.index import (
    ap_weights, cell_weights, compute_base_prices, compute_index, explain,
    load_route_weights, quality_flag,
)
from pushpak.clean import build_cells
from conftest import frame, make_quote


# ---------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------
def test_route_weights_sum_to_one():
    w = load_route_weights()
    assert np.isclose(w["weight"].sum(), 1.0)
    assert len(w) == config.BASKET_SIZE


def test_route_weights_are_dgca_derived_not_hand_typed():
    """Regenerating from the DGCA file must reproduce the committed weights."""
    import subprocess, sys
    committed = load_route_weights().set_index("route_id")["weight"].sort_index()
    r = subprocess.run(
        [sys.executable, "scripts/build_weights.py"],
        capture_output=True, text=True, cwd=config.ROOT,
    )
    assert r.returncode == 0, r.stderr
    regenerated = load_route_weights().set_index("route_id")["weight"].sort_index()
    pd.testing.assert_series_equal(committed, regenerated)


def test_ap_weights_normalise_to_one():
    assert np.isclose(sum(ap_weights().values()), 1.0)


def test_ap_weights_renormalise_on_subset():
    sub = ap_weights((7, 15))
    assert set(sub) == {7, 15}
    assert np.isclose(sum(sub.values()), 1.0)


def test_cell_weights_are_product_of_route_and_ap(two_route_weights):
    w = cell_weights(two_route_weights, (7, 15))
    assert np.isclose(w.sum(), 1.0)
    apw = ap_weights((7, 15))
    assert np.isclose(w[("BOM-DEL", 7)], 0.75 * apw[7])
    assert np.isclose(w[("BLR-DEL", 15)], 0.25 * apw[15])


# ---------------------------------------------------------------------------
# base period
# ---------------------------------------------------------------------------
def _series(prices_by_day, route="BOM-DEL", ap=7):
    """Build quotes where each day has one cell at an exact known price."""
    o, d = route.split("-")
    qs = []
    for day, price in prices_by_day.items():
        qs.append(make_quote(
            origin=o, destination=d, observation_date=day,
            travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
            advance_purchase_days=ap, total_fare=price,
        ))
    return frame(qs)


def test_index_is_exactly_100_in_base_period(two_route_weights):
    days = ["2026-08-01", "2026-08-02", "2026-08-03"]
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d = route.split("-")
        for day in days:
            for ap in (7, 15):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=5000.0,
                ))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=3)
    assert np.allclose(res.daily["index_value"], 100.0)


def test_base_period_uses_mean_of_first_n_days():
    df = _series({"2026-08-01": 1000.0, "2026-08-02": 2000.0, "2026-08-03": 9999.0})
    cells = build_cells(df)
    base, dates = compute_base_prices(cells, base_days=2)
    assert dates == ["2026-08-01", "2026-08-02"]
    assert np.isclose(base[("BOM-DEL", 7)], 1500.0)


# ---------------------------------------------------------------------------
# the core arithmetic
# ---------------------------------------------------------------------------
def test_uniform_10pct_price_rise_moves_index_to_110(two_route_weights):
    """Every cell up 10% must give exactly 110.0 — no more, no less."""
    days = ["2026-08-01", "2026-08-02"]
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d = route.split("-")
        for ap in (7, 15):
            for day, price in zip(days, (4000.0, 4400.0)):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=price,
                ))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    assert np.isclose(res.daily["index_value"].iloc[0], 100.0)
    assert np.isclose(res.daily["index_value"].iloc[1], 110.0)


def test_weighting_follows_dgca_shares_not_equal_shares(two_route_weights):
    """Only the 75%-weight route moves; the index must move by 0.75 x that."""
    days = ["2026-08-01", "2026-08-02"]
    qs = []
    for route, prices in (("BOM-DEL", (1000.0, 1200.0)),   # +20%
                          ("BLR-DEL", (1000.0, 1000.0))):  # flat
        o, d = route.split("-")
        for ap in (7, 15):
            for day, price in zip(days, prices):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=price,
                ))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    # 100 x (0.75 x 1.20 + 0.25 x 1.00) = 115.0
    assert np.isclose(res.daily["index_value"].iloc[1], 115.0)
    # an equal-weighted index would give 110 — prove we are not that
    assert not np.isclose(res.daily["index_value"].iloc[1], 110.0)


def test_ap_window_weighting_is_applied(two_route_weights):
    """Move only the T+7 cells; the index must move by v(7), not by 1/2."""
    days = ["2026-08-01", "2026-08-02"]
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d = route.split("-")
        for ap in (7, 15):
            prices = (1000.0, 1500.0) if ap == 7 else (1000.0, 1000.0)
            for day, price in zip(days, prices):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=price,
                ))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    v7 = ap_weights((7, 15))[7]
    expected = 100.0 * (v7 * 1.5 + (1 - v7) * 1.0)
    assert np.isclose(res.daily["index_value"].iloc[1], expected)


def test_index_is_invariant_to_currency_rescaling(two_route_weights):
    """Doubling every fare in every period must leave the index unchanged.

    A price index measures relative change; a units change must not show up.
    """
    days = ["2026-08-01", "2026-08-02", "2026-08-03"]
    def build(scale):
        qs = []
        for route in ("BOM-DEL", "BLR-DEL"):
            o, d = route.split("-")
            for ap in (7, 15):
                for i, day in enumerate(days):
                    qs.append(make_quote(
                        origin=o, destination=d, observation_date=day,
                        travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                        advance_purchase_days=ap,
                        total_fare=(3000.0 + 250.0 * i) * scale,
                    ))
        return compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)

    a = build(1.0).daily["index_value"].to_numpy()
    b = build(2.0).daily["index_value"].to_numpy()
    assert np.allclose(a, b)


def test_index_ignores_routes_outside_the_basket(two_route_weights):
    """A route with no weight must not leak into the index."""
    days = ["2026-08-01", "2026-08-02"]
    qs = []
    for ap in (7, 15):
        for day, price in zip(days, (1000.0, 1000.0)):
            qs.append(make_quote(
                origin="BOM", destination="DEL", observation_date=day,
                travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                advance_purchase_days=ap, total_fare=price))
            qs.append(make_quote(
                origin="BLR", destination="DEL", observation_date=day,
                travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                advance_purchase_days=ap, total_fare=price))
            # a wildly-priced route that is NOT in the basket
            qs.append(make_quote(
                origin="MAA", destination="CCU", observation_date=day,
                travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                advance_purchase_days=ap, total_fare=price * (1 if day == days[0] else 50)))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    assert np.allclose(res.daily["index_value"], 100.0)


# ---------------------------------------------------------------------------
# auditability
# ---------------------------------------------------------------------------
def test_explain_reconciles_to_the_published_value(two_route_weights):
    days = ["2026-08-01", "2026-08-02", "2026-08-03"]
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d = route.split("-")
        for ap in (7, 15):
            for i, day in enumerate(days):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=4000.0 + 137.0 * i + 11.0 * ap,
                ))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    e = explain(res, "2026-08-03")
    assert e["reconciles"] is True
    assert abs(e["residual"]) < 1e-9
    assert np.isclose(sum(t["weight"] for t in e["terms"]), 1.0)
    assert len(e["terms"]) == 4          # 2 routes x 2 windows


def test_explain_terms_carry_provenance_flags(two_route_weights):
    days = ["2026-08-01", "2026-08-02"]
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d = route.split("-")
        for ap in (7, 15):
            for day in days:
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=5000.0))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    e = explain(res, "2026-08-02")
    assert all(t["synthetic_share"] == 1.0 for t in e["terms"])
    assert e["weight_provenance"]["route_weights"] == "DGCA_DERIVED"
    assert e["weight_provenance"]["ap_window_weights"] == "ASSUMED_PRIOR"


# ---------------------------------------------------------------------------
# resampling & quality
# ---------------------------------------------------------------------------
def test_weekly_resample_averages_daily_values(two_route_weights):
    days = [f"2026-08-{d:02d}" for d in range(3, 10)]   # Mon..Sun, one ISO week
    qs = []
    for route in ("BOM-DEL", "BLR-DEL"):
        o, d_ = route.split("-")
        for ap in (7, 15):
            for i, day in enumerate(days):
                qs.append(make_quote(
                    origin=o, destination=d_, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=1000.0 * (1 + 0.01 * i)))
    res = compute_index(frame(qs), two_route_weights, windows=(7, 15), base_days=1)
    weekly = res.resample("weekly")
    assert len(weekly) == 1
    assert np.isclose(weekly["index_value"].iloc[0], res.daily["index_value"].mean())
    assert weekly["n_days"].iloc[0] == 7


def test_quality_flag_thresholds():
    assert quality_flag(0.0, 0.0) == "green"
    assert quality_flag(1.0, 0.0) == "amber"      # synthetic can never be green
    assert quality_flag(0.0, 0.05) == "green"
    assert quality_flag(0.0, 0.25) == "amber"
    assert quality_flag(0.0, 0.90) == "red"


def test_all_synthetic_data_can_never_be_flagged_green():
    """The labelling guarantee, enforced at the index layer."""
    for imputed in (0.0, 0.05, 0.2, 0.9):
        assert quality_flag(1.0, imputed) != "green"
