import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushpak import config, storage  # noqa: E402
from pushpak.schema import (  # noqa: E402
    CollectionMode, FareQuote, QuoteStatus, SourceType,
)


@pytest.fixture(scope="session")
def db_url() -> str:
    """An empty Pushpak schema in Postgres, or skip.

    There is no per-test database the way there was a per-test SQLite file, so
    the tests share one server and start from a dropped schema. Point
    PUSHPAK_DB_URL at a throwaway database — this fixture drops its tables.
    """
    url = config.DB_URL
    try:
        storage.drop_all(url)
        storage.init_db(url)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Postgres at {url}: {exc}")
    return url


def make_quote(**kw) -> FareQuote:
    """A valid quote with sane defaults; override any field by keyword."""
    total = kw.pop("total_fare", 5000.0)
    status = kw.pop("quote_status", QuoteStatus.OK)
    if status is not QuoteStatus.OK:
        total = None
    base = taxes = None
    if total is not None:
        taxes = round(total * 0.05 / 1.05 + 480.0, 0)
        base = round(total - taxes, 0)
    defaults = dict(
        origin="DEL", destination="BOM", route_id="",
        carrier="6E", source="synthetic-ota-a",
        source_type=SourceType.SYNTHETIC,
        collection_mode=CollectionMode.SYNTHETIC,
        scrape_ts="2026-08-01T00:00:00+00:00",
        observation_date="2026-08-01", travel_date="2026-08-08",
        advance_purchase_days=7,
        base_fare=base, taxes_fees=taxes, total_fare=total,
        quote_status=status,
    )
    defaults.update(kw)
    return FareQuote(**defaults)


def frame(quotes) -> pd.DataFrame:
    return pd.DataFrame([q.to_row() for q in quotes])


@pytest.fixture
def two_route_weights() -> pd.DataFrame:
    """A deliberately lopsided 2-route basket, so weighting is observable."""
    return pd.DataFrame([
        {"route_id": "BOM-DEL", "weight": 0.75, "annual_pax": 750.0,
         "national_share": 0.05, "traffic_window": "202506..202605"},
        {"route_id": "BLR-DEL", "weight": 0.25, "annual_pax": 250.0,
         "national_share": 0.02, "traffic_window": "202506..202605"},
    ])
