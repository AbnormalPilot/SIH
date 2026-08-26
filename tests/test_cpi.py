"""Component 8 — tests for the official CPI context.

Two things are pinned here. First, that the workbook parses correctly, since
its sheets have title rows, trailing spaces in sheet names, a blank header cell
for the state column, and inconsistent state spellings across sheets. Second,
and more importantly, that the module *refuses* to present the general CPI as a
benchmark for an airfare index.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pushpak import cpi
from pushpak.index import compute_index
from conftest import frame, make_quote


pytestmark = pytest.mark.skipif(
    not cpi.CPI_WORKBOOK.exists(), reason="CPI workbook not present"
)


@pytest.fixture(scope="module")
def parsed():
    return cpi.parse_workbook()


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_all_india_parses_all_three_variants(parsed):
    ai, _ = parsed
    assert set(ai["series"]) == {"rural", "urban", "combined"}
    for s in ("rural", "urban", "combined"):
        assert len(ai[ai["series"] == s]) >= 12


def test_periods_are_iso_year_month(parsed):
    ai, _ = parsed
    assert ai["period"].str.match(r"^\d{4}-\d{2}$").all()


def test_index_values_are_plausible(parsed):
    ai, _ = parsed
    v = ai["index_value"]
    assert v.notna().all()
    assert (v > 50).all() and (v < 500).all()


def test_series_is_monotonic_in_period_and_has_no_gaps(parsed):
    ai, _ = parsed
    c = ai[ai["series"] == "combined"].sort_values("period")
    months = pd.PeriodIndex(c["period"], freq="M")
    assert (months.astype(int).diff().dropna() == 1).all(), "gap in the monthly series"


def test_statewise_blocks_both_parse(parsed):
    """Regression: the index block was silently dropped by a column selector.

    The state column has a blank header, so pandas names it "Unnamed: N" and a
    naive ".1"-suffix filter loses it, after which every row is discarded as
    stateless and the whole block vanishes without an error.
    """
    _, sw = parsed
    for measure in ("index_value", "inflation_pct"):
        block = sw[sw["measure"] == measure]
        assert len(block) > 0, f"{measure} block is empty"
        assert block["state"].notna().all()


def test_state_names_are_normalised_across_sheets(parsed):
    """The workbook spells states differently per sheet. One spelling must win."""
    _, sw = parsed
    a = set(sw[sw["measure"] == "index_value"]["state"])
    b = set(sw[sw["measure"] == "inflation_pct"]["state"])
    assert a == b, f"state name mismatch: {sorted(a ^ b)}"
    assert len(a) == 36, f"expected 36 states and UTs, got {len(a)}"


@pytest.mark.parametrize("raw,expected", [
    ("Chandigarh*", "Chandigarh"),
    ("Andaman and Nicobar Islands", "Andaman And Nicobar Islands"),
    ("Delhi", "NCT of Delhi"),
    ("  Kerala  ", "Kerala"),
])
def test_state_normalisation_cases(raw, expected):
    assert cpi.normalise_state(raw) == expected


# ---------------------------------------------------------------------------
# derived figures
# ---------------------------------------------------------------------------
def test_inflation_columns_are_computed():
    df = cpi.cpi_inflation("combined")
    assert "mom_pct" in df and "yoy_pct" in df
    assert pd.isna(df["mom_pct"].iloc[0])            # no prior month for the first row
    assert df["mom_pct"].iloc[1:].notna().all()


def test_yoy_matches_a_hand_computed_value():
    df = cpi.cpi_inflation("combined").reset_index(drop=True)
    if len(df) <= 12:
        pytest.skip("series shorter than 13 months")
    i = 12
    expected = (df["index_value"].iloc[i] / df["index_value"].iloc[0] - 1) * 100
    assert np.isclose(df["yoy_pct"].iloc[i], expected)


# ---------------------------------------------------------------------------
# the honesty constraints
# ---------------------------------------------------------------------------
def _pushpak(days=5):
    qs = []
    for i in range(days):
        day = (date(2026, 7, 1) + timedelta(days=i)).isoformat()
        for route in ("BOM-DEL", "BLR-DEL"):
            o, d = route.split("-")
            for ap in (7, 15):
                qs.append(make_quote(
                    origin=o, destination=d, observation_date=day,
                    travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
                    advance_purchase_days=ap, total_fare=5000.0 + 50 * i,
                ))
    w = pd.DataFrame([
        {"route_id": "BOM-DEL", "weight": 0.6, "annual_pax": 6.0, "national_share": 0.04,
         "traffic_window": "x"},
        {"route_id": "BLR-DEL", "weight": 0.4, "annual_pax": 4.0, "national_share": 0.03,
         "traffic_window": "x"},
    ])
    return compute_index(frame(qs), w, windows=(7, 15), base_days=1)


def test_compare_refuses_to_treat_cpi_as_a_benchmark():
    res = _pushpak()
    out = cpi.compare_to_pushpak(res.daily)
    assert out["comparable_as_level"] is False
    assert "NOT A VALIDATION" in out["caveat"]
    assert "no transport sub-group" in out["caveat"]


def test_compare_computes_no_correlation_at_all():
    """A correlation here would be meaningless, so none may be produced.

    Checks the *keys* of the returned structure. The caveat prose legitimately
    contains the word "correlation" while explaining why none is offered, so a
    substring search over the whole payload would fail for the wrong reason.
    """
    out = cpi.compare_to_pushpak(_pushpak().daily)

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    banned = ("correl", "r_squared", "r2", "beta", "tracking_error")
    offenders = [k for k in keys(out) if any(b in k for b in banned)]
    assert not offenders, f"comparison must not report fit statistics: {offenders}"


def test_cpi_is_not_used_by_the_backtest():
    """The validation benchmark must stay a separate, separately-labelled input."""
    import inspect
    from pushpak import validate
    src = inspect.getsource(validate)
    assert "cpi" not in src.lower().replace("cpi_dashboard", "")


def test_frequency_gap_favours_pushpak_and_is_computed_not_asserted():
    res = _pushpak(days=10)
    gap = cpi.frequency_gap(res.daily).as_dict()
    assert gap["pushpak"]["observations_per_year"] > gap["cpi"]["observations_per_year"]
    assert gap["frequency_multiple"] > 1
    assert gap["cpi"]["observations"] == len(cpi.load_all_india("combined"))


def test_statewise_latest_returns_every_state():
    out = cpi.statewise_latest("inflation_pct")
    assert out["n_states"] == 36
    assert out["period"] is not None
