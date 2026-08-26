"""Component 8 — tests for normalisation, validation and imputation.

These cover the second thing a reviewer will challenge: what happens to
missing, sold-out and implausible data, and whether synthetic data can ever
lose its label.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pushpak import config
from pushpak.clean import (
    build_cells, clean, impute_cells, normalise, screen_outliers, validate,
)
from pushpak.schema import (
    COLUMNS, CollectionMode, FORBIDDEN_PII_FIELDS, FareQuote, ImputationMethod,
    QuoteStatus, SourceType, canonical_route_id,
)
from conftest import frame, make_quote


# ---------------------------------------------------------------------------
# schema invariants
# ---------------------------------------------------------------------------
def test_canonical_route_id_is_direction_free():
    assert canonical_route_id("DEL", "BOM") == canonical_route_id("BOM", "DEL") == "BOM-DEL"


def test_sold_out_quote_must_have_null_fare():
    q = make_quote(quote_status=QuoteStatus.SOLD_OUT)
    assert q.total_fare is None
    assert q.validate() == []


def test_sold_out_quote_with_a_fare_is_rejected():
    q = make_quote()
    q.quote_status = QuoteStatus.SOLD_OUT       # force the bad combination
    errs = q.validate()
    assert any("NULL total_fare" in e for e in errs)


def test_ok_quote_without_a_fare_is_rejected():
    q = make_quote()
    q.total_fare = None
    assert any("requires a non-null total_fare" in e for e in q.validate())


def test_fare_components_must_reconcile():
    q = make_quote(total_fare=5000.0)
    q.taxes_fees = 9999.0
    assert any("!= total_fare" in e for e in q.validate())


def test_advance_purchase_days_must_match_the_dates():
    q = make_quote(advance_purchase_days=7)
    q.travel_date = "2026-08-20"                # not 7 days after 2026-08-01
    assert any("advance_purchase_days" in e for e in q.validate())


def test_collection_mode_is_required_and_has_no_default():
    """The labelling guarantee: synthetic data cannot enter unlabelled."""
    import inspect
    sig = inspect.signature(FareQuote.__init__)
    assert sig.parameters["collection_mode"].default is inspect.Parameter.empty


def test_schema_contains_no_pii_fields():
    assert not (set(COLUMNS) & FORBIDDEN_PII_FIELDS)
    assert config.COLLECT_PII is False


def test_quote_id_is_stable_and_distinguishes_collection_mode():
    a = make_quote()
    b = make_quote()
    assert a.quote_id == b.quote_id
    c = make_quote(collection_mode=CollectionMode.LIVE_SCRAPE,
                   source_type=SourceType.OTA, source="cleartrip.com")
    assert c.quote_id != a.quote_id


# ---------------------------------------------------------------------------
# normalisation & validation
# ---------------------------------------------------------------------------
def test_normalise_canonicalises_route_and_uppercases_codes():
    df = frame([make_quote(origin="del", destination="bom")])
    out = normalise(df)
    assert out["origin"].iloc[0] == "DEL"
    assert out["route_id"].iloc[0] == "BOM-DEL"


def test_validate_quarantines_rather_than_deleting():
    good = make_quote()
    bad = make_quote()
    df = frame([good, bad])
    df.loc[1, "total_fare"] = -5.0              # corrupt one row
    ok, quarantined, reasons = validate(normalise(df))
    assert len(ok) == 1 and len(quarantined) == 1
    assert reasons == {"non_positive_fare": 1}
    assert "quarantine_reason" in quarantined.columns


def test_clean_reports_every_category():
    qs = [
        make_quote(carrier="6E"),
        make_quote(carrier="AI", quote_status=QuoteStatus.SOLD_OUT),
        make_quote(carrier="SG", quote_status=QuoteStatus.CANCELLED),
        make_quote(carrier="QP", quote_status=QuoteStatus.BLOCKED_BY_ROBOTS),
        make_quote(carrier="IX", quote_status=QuoteStatus.FETCH_FAILED),
    ]
    _, _, rep = clean(frame(qs))
    d = rep.as_dict()
    assert d["priced"] == 1 and d["soldout"] == 1 and d["unavailable"] == 1
    assert d["blocked"] == 1 and d["failed"] == 1
    assert d["synthetic_share"] == 1.0


# ---------------------------------------------------------------------------
# outlier screening
# ---------------------------------------------------------------------------
def test_outlier_is_flagged_not_deleted():
    qs = [make_quote(carrier=c, total_fare=f)
          for c, f in zip("ABCDE", [5000, 5100, 4900, 5050, 500000])]
    out, n = screen_outliers(frame(qs))
    assert n == 1
    assert len(out) == 5                                  # nothing deleted
    bad = out[out["quote_status"] == QuoteStatus.OUTLIER_REJECTED.value]
    assert len(bad) == 1 and bad["total_fare"].isna().all()
    assert "screened" in bad["notes"].iloc[0]


def test_outliers_are_screened_within_cell_not_globally():
    """A legitimately expensive T+1 fare must survive beside cheap T+30 fares."""
    qs = []
    for c, f in zip("ABCDE", [2000, 2050, 1980, 2100, 2020]):
        qs.append(make_quote(carrier=c, advance_purchase_days=30,
                             travel_date="2026-08-31", total_fare=float(f)))
    for c, f in zip("ABCDE", [9000, 9200, 8900, 9100, 9050]):
        qs.append(make_quote(carrier=c, advance_purchase_days=1,
                             travel_date="2026-08-02", total_fare=float(f)))
    out, n = screen_outliers(frame(qs))
    assert n == 0, "cross-cell price differences must not be treated as outliers"


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------
def test_cell_price_is_mean_of_priced_quotes_only():
    qs = [
        make_quote(carrier="6E", total_fare=4000.0),
        make_quote(carrier="AI", total_fare=6000.0),
        make_quote(carrier="SG", quote_status=QuoteStatus.SOLD_OUT),
    ]
    cells = build_cells(normalise(frame(qs)))
    assert len(cells) == 1
    row = cells.iloc[0]
    assert np.isclose(row["cell_price"], 5000.0)      # sold-out excluded from mean
    assert row["n_quotes"] == 3                        # but counted in the cell
    assert row["n_priced"] == 2 and row["n_soldout"] == 1
    assert np.isclose(row["soldout_share"], 1 / 3)


def test_fully_sold_out_cell_has_no_price_but_still_exists():
    """The bias argument: the cell must survive as a gap, not vanish."""
    qs = [make_quote(carrier=c, quote_status=QuoteStatus.SOLD_OUT) for c in "ABC"]
    cells = build_cells(normalise(frame(qs)))
    assert len(cells) == 1
    assert pd.isna(cells["cell_price"].iloc[0])
    assert cells["n_quotes"].iloc[0] == 3
    assert np.isclose(cells["soldout_share"].iloc[0], 1.0)


def test_synthetic_share_survives_aggregation():
    qs = [
        make_quote(carrier="6E", total_fare=5000.0),
        make_quote(carrier="AI", total_fare=5000.0,
                   collection_mode=CollectionMode.LIVE_SCRAPE,
                   source_type=SourceType.OTA, source="cleartrip.com"),
    ]
    cells = build_cells(normalise(frame(qs)))
    assert np.isclose(cells["synthetic_share"].iloc[0], 0.5)


# ---------------------------------------------------------------------------
# imputation ladder
# ---------------------------------------------------------------------------
def _cells(prices_by_day, route="BOM-DEL", ap=7):
    qs = []
    for day, price in prices_by_day.items():
        kw = dict(
            origin=route.split("-")[1], destination=route.split("-")[0],
            observation_date=day, advance_purchase_days=ap,
            travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat(),
        )
        if price is None:
            qs.append(make_quote(quote_status=QuoteStatus.SOLD_OUT, **kw))
        else:
            qs.append(make_quote(total_fare=price, **kw))
    return build_cells(normalise(frame(qs)))


def test_carry_forward_fills_a_gap_with_the_last_observed_price():
    cells = _cells({"2026-08-01": 5000.0, "2026-08-02": None})
    base = pd.Series({("BOM-DEL", 7): 5000.0})
    out = impute_cells(cells, base)
    row = out[out["observation_date"] == "2026-08-02"].iloc[0]
    assert row["is_imputed"] is np.True_ or row["is_imputed"] is True
    assert row["imputation_method"] == ImputationMethod.CARRY_FORWARD.value
    assert np.isclose(row["cell_price_final"], 5000.0)
    assert "last observed 2026-08-01" in row["imputed_from"]


def test_carry_forward_expires_after_the_staleness_cap():
    stale_day = (date(2026, 8, 1)
                 + timedelta(days=config.MAX_CARRY_FORWARD_DAYS + 1)).isoformat()
    cells = _cells({"2026-08-01": 5000.0, stale_day: None})
    base = pd.Series({("BOM-DEL", 7): 5000.0})
    out = impute_cells(cells, base)
    row = out[out["observation_date"] == stale_day].iloc[0]
    assert row["imputation_method"] != ImputationMethod.CARRY_FORWARD.value


def test_class_mean_imputes_the_movement_not_the_level():
    """A missing T+1 cell must inherit the route's *relative* move, not its level.

    T+1 base 10000, T+30 base 5000. On day 2 the observed T+30 cell rises 20%.
    The missing T+1 cell must be imputed at 10000 x 1.20 = 12000 — not at the
    other cell's level (6000), which a naive cross-sectional mean would give.
    """
    qs = []
    for day, p1, p30 in (("2026-08-01", 10000.0, 5000.0),
                         ("2026-08-02", None, 6000.0)):
        for ap, price in ((1, p1), (30, p30)):
            kw = dict(origin="BOM", destination="DEL", observation_date=day,
                      advance_purchase_days=ap,
                      travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat())
            if price is None:
                qs.append(make_quote(quote_status=QuoteStatus.SOLD_OUT, **kw))
            else:
                qs.append(make_quote(total_fare=price, **kw))

    cells = build_cells(normalise(frame(qs)))
    base = pd.Series({("BOM-DEL", 1): 10000.0, ("BOM-DEL", 30): 5000.0})
    # carry-forward must not apply, so use a zero-day staleness cap
    out = impute_cells(cells, base, max_carry_days=0)
    row = out[(out["observation_date"] == "2026-08-02")
              & (out["advance_purchase_days"] == 1)].iloc[0]
    assert row["imputation_method"] == ImputationMethod.CLASS_MEAN.value
    assert np.isclose(row["cell_price_final"], 12000.0)
    assert not np.isclose(row["cell_price_final"], 6000.0)


def test_base_carry_is_the_last_resort_when_the_route_has_nothing():
    cells = _cells({"2026-08-01": 5000.0, "2026-08-02": None})
    base = pd.Series({("BOM-DEL", 7): 5000.0})
    out = impute_cells(cells, base, max_carry_days=0)
    row = out[out["observation_date"] == "2026-08-02"].iloc[0]
    assert row["imputation_method"] == ImputationMethod.BASE_CARRY.value
    assert np.isclose(row["cell_price_final"], 5000.0)


def test_imputed_values_never_feed_other_imputations_on_the_same_day():
    """Route-level relative must be computed from OBSERVED cells only.

    Two cells, both missing on day 2 except one. If the imputed cell were fed
    back, the second imputation would drift.
    """
    qs = []
    for day, p7, p15 in (("2026-08-01", 5000.0, 4000.0),
                         ("2026-08-02", 5500.0, None)):
        for ap, price in ((7, p7), (15, p15)):
            kw = dict(origin="BOM", destination="DEL", observation_date=day,
                      advance_purchase_days=ap,
                      travel_date=(date.fromisoformat(day) + timedelta(days=ap)).isoformat())
            if price is None:
                qs.append(make_quote(quote_status=QuoteStatus.SOLD_OUT, **kw))
            else:
                qs.append(make_quote(total_fare=price, **kw))
    cells = build_cells(normalise(frame(qs)))
    base = pd.Series({("BOM-DEL", 7): 5000.0, ("BOM-DEL", 15): 4000.0})
    out = impute_cells(cells, base, max_carry_days=0)
    row = out[(out["observation_date"] == "2026-08-02")
              & (out["advance_purchase_days"] == 15)].iloc[0]
    # observed relative on day 2 is 5500/5000 = 1.10 -> 4000 x 1.10 = 4400
    assert np.isclose(row["cell_price_final"], 4400.0)


def test_every_imputed_row_is_flagged_with_a_method_and_a_reason():
    cells = _cells({"2026-08-01": 5000.0, "2026-08-02": None, "2026-08-03": None})
    base = pd.Series({("BOM-DEL", 7): 5000.0})
    out = impute_cells(cells, base)
    imputed = out[out["is_imputed"]]
    assert len(imputed) == 2
    assert (imputed["imputation_method"] != ImputationMethod.NONE.value).all()
    assert imputed["imputed_from"].notna().all()


def test_observed_cells_are_never_marked_imputed():
    cells = _cells({"2026-08-01": 5000.0, "2026-08-02": 5100.0})
    base = pd.Series({("BOM-DEL", 7): 5000.0})
    out = impute_cells(cells, base)
    assert not out["is_imputed"].any()
    assert (out["imputation_method"] == ImputationMethod.NONE.value).all()


def test_empty_input_does_not_explode():
    empty = pd.DataFrame(columns=list(COLUMNS))
    ok, quarantined, rep = clean(empty)
    assert rep.rows_in == 0
    cells = build_cells(ok)
    assert len(cells) == 0
    out = impute_cells(cells, pd.Series(dtype=float))
    assert len(out) == 0
