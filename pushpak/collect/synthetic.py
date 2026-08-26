"""Component 1 — the CLEARLY LABELLED synthetic fare generator.

Why this exists
---------------
Live scraping of Indian fare pages is blocked (see collect/robots.py): every
major OTA disallows its flight-search paths and the airline sites refuse
non-browser clients. A demo cannot depend on it, and the brief anticipates
exactly this. So this module stands in for the *shape* of the data a
production feed would deliver under a data-sharing agreement.

The labelling guarantee
-----------------------
Every record produced here carries ``collection_mode=SYNTHETIC`` and
``source_type=SYNTHETIC``, and its ``source`` string begins with ``synthetic-``.
That field is required by :class:`pushpak.schema.FareQuote` with no default, so a
synthetic record cannot enter the database unlabelled. The index, API and
dashboard all propagate the synthetic share. ``tests/test_clean.py`` asserts
this end to end.

The price model
---------------
Fares are not random noise; they reproduce four effects a reviewer can see in
the lead-time chart:

  1. **Advance-purchase decay** -- fares climb steeply into departure.
     ``f(ap) = 1 + A * exp(-ap / tau)``, so T+1 is ~50-60% above T+30.
  2. **Day-of-week effects** on the *travel* date -- Friday and Sunday peak,
     Tuesday/Wednesday trough. This is a property of the flight, not the
     scrape day, which is why it is keyed on travel_date.
  3. **Demand spikes** -- occasional multi-day surges on a travel date
     (festival/event demand), persistent across observation days so the same
     flight tells a coherent story as departure approaches.
  4. **Sold-out inventory** -- probability rises as departure nears and as the
     flight's price percentile rises. Emitted as SOLD_OUT rows with NULL
     fares, never as absent rows, because dropping sold-out quotes biases an
     airfare index *downward*.

Flight-level randomness is seeded from a stable hash of
(route, carrier, travel_date, flight_index) so that re-running the generator
reproduces the identical series, and so one flight's price evolves smoothly
across consecutive observation dates instead of jumping.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, Optional

from .. import config
from ..schema import (
    CollectionMode, FareQuote, QuoteStatus, SourceType, canonical_route_id,
)

GENERATOR_VERSION = "synthetic-v1"

# Approximate great-circle distances (km) for the basket, used to scale fares
# so that the relative price level across routes is not arbitrary.
ROUTE_DISTANCE_KM = {
    "BOM-DEL": 1148,
    "BLR-DEL": 1740,
    "BLR-BOM": 842,
    "DEL-HYD": 1253,
    "CCU-DEL": 1305,
}

# INR/km anchor plus a fixed leg cost, giving plausible economy fare levels.
FARE_PER_KM = 2.65
FARE_FIXED_LEG = 2100.0

# Advance-purchase curve: f(ap) = 1 + AP_AMPLITUDE * exp(-ap / AP_TAU)
AP_AMPLITUDE = 0.85
AP_TAU = 11.0

# Day-of-week multipliers on the TRAVEL date. Monday = 0.
DOW_FACTOR = {
    0: 1.04,   # Mon — business outbound
    1: 0.95,   # Tue
    2: 0.94,   # Wed — trough
    3: 1.00,   # Thu
    4: 1.12,   # Fri — peak
    5: 0.99,   # Sat
    6: 1.09,   # Sun — return peak
}

# Carrier price positioning relative to the route mean.
CARRIER_FACTOR = {
    "6E": 0.98,   # IndiGo — scale leader, prices at/below market
    "AI": 1.09,   # Air India — full service
    "IX": 0.92,   # Air India Express — LCC
    "QP": 0.96,   # Akasa
    "SG": 0.94,   # SpiceJet
}

# OTAs sometimes shave or add margin vs the airline's own site.
SOURCE_FACTOR = {
    "synthetic-airline-a": 1.000,
    "synthetic-airline-b": 1.004,
    "synthetic-ota-a": 0.985,
    "synthetic-ota-b": 0.993,
}
SOURCE_TYPES = {
    "synthetic-airline-a": SourceType.SYNTHETIC,
    "synthetic-airline-b": SourceType.SYNTHETIC,
    "synthetic-ota-a": SourceType.SYNTHETIC,
    "synthetic-ota-b": SourceType.SYNTHETIC,
}
#: What each synthetic source stands in for. Surfaced in the UI so nobody
#: mistakes "synthetic-ota-a" for a real OTA feed.
SOURCE_STANDS_IN_FOR = {
    "synthetic-airline-a": "an airline direct site (IndiGo-like)",
    "synthetic-airline-b": "an airline direct site (Air India-like)",
    "synthetic-ota-a": "an OTA/metasearch feed (Skyscanner/Cleartrip-like)",
    "synthetic-ota-b": "an OTA feed (ixigo/EaseMyTrip-like)",
}

FLIGHTS_PER_ROUTE_CARRIER = 3
TAX_GST_RATE = 0.05          # GST on domestic economy tickets
TAX_FIXED_FEES = 480.0       # UDF + PSF + aviation security fee, approx.


def _stable_rng(*parts: object) -> random.Random:
    """Deterministic RNG seeded by a stable hash of ``parts``."""
    key = "|".join(str(p) for p in parts).encode()
    seed = int.from_bytes(hashlib.sha1(key).digest()[:8], "big")
    return random.Random(seed)


def route_base_fare(route_id: str) -> float:
    km = ROUTE_DISTANCE_KM.get(route_id, 1200)
    return FARE_FIXED_LEG + FARE_PER_KM * km


def ap_factor(ap_days: int) -> float:
    """Advance-purchase decay. Monotonically decreasing in ap_days."""
    return 1.0 + AP_AMPLITUDE * math.exp(-ap_days / AP_TAU)


def dow_factor(travel_date: date) -> float:
    return DOW_FACTOR[travel_date.weekday()]


def demand_spike(route_id: str, travel_date: date, seed: int) -> float:
    """Occasional multi-day demand surge on a travel date (festival/event).

    Keyed on the travel date only, so every observation of that date sees the
    same surge — which is what makes the backfilled series coherent.
    """
    rng = _stable_rng(seed, "spike", route_id, travel_date.isoformat())
    roll = rng.random()
    if roll < 0.06:
        return 1.0 + rng.uniform(0.25, 0.60)     # strong surge
    if roll < 0.16:
        return 1.0 + rng.uniform(0.06, 0.20)     # mild surge
    if roll < 0.22:
        return 1.0 - rng.uniform(0.05, 0.14)     # soft patch
    return 1.0


def _seasonal(travel_date: date) -> float:
    """Gentle annual seasonality so month-on-month movement isn't flat."""
    doy = travel_date.timetuple().tm_yday
    return 1.0 + 0.045 * math.sin(2 * math.pi * (doy - 30) / 365.0)


def _soldout_probability(ap_days: int, price_ratio: float) -> float:
    """Rises into departure and with relative price."""
    near = math.exp(-ap_days / 6.0)          # ~0.85 at T+1, ~0.006 at T+30
    return min(0.45, 0.30 * near + 0.10 * max(0.0, price_ratio - 1.15))


def generate_quotes(
    observation_date: date,
    routes: list[str],
    ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
    sources: Optional[list[str]] = None,
    carriers: Optional[list[str]] = None,
    seed: int = 20260826,
    scrape_ts: Optional[str] = None,
) -> Iterator[FareQuote]:
    """Yield one observation day's worth of clearly-labelled synthetic quotes."""
    sources = sources or list(SOURCE_FACTOR)
    carriers = carriers or list(CARRIER_FACTOR)
    ts = scrape_ts or datetime.combine(
        observation_date, datetime.min.time(), tzinfo=timezone.utc
    ).isoformat(timespec="seconds")

    for route_id in routes:
        a, b = route_id.split("-")
        base = route_base_fare(route_id)

        for ap in ap_windows:
            travel_date = observation_date + timedelta(days=ap)
            spike = demand_spike(route_id, travel_date, seed)
            season = _seasonal(travel_date)
            dow = dow_factor(travel_date)

            for origin, dest in ((a, b), (b, a)):
                for carrier in carriers:
                    cfac = CARRIER_FACTOR[carrier]
                    for flight_ix in range(FLIGHTS_PER_ROUTE_CARRIER):
                        frng = _stable_rng(
                            seed, route_id, origin, carrier, flight_ix,
                            travel_date.isoformat(),
                        )
                        # Stable per-flight identity
                        dep_hour = 6 + (flight_ix * 6 + frng.randrange(0, 4)) % 16
                        dep_min = frng.choice((0, 10, 15, 25, 35, 45, 50))
                        flight_no = f"{carrier}{2000 + (hash((route_id, carrier, flight_ix)) % 800)}"
                        flight_premium = frng.uniform(0.90, 1.14)   # time-of-day desirability

                        for source in sources:
                            sfac = SOURCE_FACTOR[source]
                            # Small per-observation noise; seeded on the
                            # observation date so re-runs reproduce exactly.
                            nrng = _stable_rng(
                                seed, "obs", observation_date.isoformat(),
                                route_id, origin, carrier, flight_ix, source, ap,
                            )
                            noise = nrng.gauss(1.0, 0.035)

                            total = (
                                base * ap_factor(ap) * dow * spike * season
                                * cfac * sfac * flight_premium * max(0.75, noise)
                            )
                            price_ratio = total / (base * ap_factor(ap))

                            status = QuoteStatus.OK
                            if nrng.random() < _soldout_probability(ap, price_ratio):
                                status = QuoteStatus.SOLD_OUT
                            elif nrng.random() < 0.004:
                                status = QuoteStatus.CANCELLED

                            if status is QuoteStatus.OK:
                                total = round(total, 0)
                                taxes = round(
                                    total * TAX_GST_RATE / (1 + TAX_GST_RATE)
                                    + TAX_FIXED_FEES, 0
                                )
                                taxes = min(taxes, total - 500.0)
                                base_fare = round(total - taxes, 0)
                            else:
                                total = base_fare = taxes = None

                            yield FareQuote(
                                origin=origin,
                                destination=dest,
                                route_id=canonical_route_id(origin, dest),
                                carrier=carrier,
                                source=source,
                                source_type=SOURCE_TYPES[source],
                                collection_mode=CollectionMode.SYNTHETIC,
                                scrape_ts=ts,
                                observation_date=observation_date.isoformat(),
                                travel_date=travel_date.isoformat(),
                                advance_purchase_days=ap,
                                fare_class="ECONOMY",
                                base_fare=base_fare,
                                taxes_fees=taxes,
                                total_fare=total,
                                quote_status=status,
                                flight_number=flight_no,
                                departure_time=f"{dep_hour:02d}:{dep_min:02d}",
                                stops=0,
                                notes=(
                                    f"SYNTHETIC ({GENERATOR_VERSION}); stands in for "
                                    f"{SOURCE_STANDS_IN_FOR[source]}; not a real quote"
                                ),
                            )


def generate_backfill(
    end_date: date,
    days: int,
    routes: list[str],
    seed: int = 20260826,
    **kwargs,
) -> Iterator[FareQuote]:
    """Generate ``days`` consecutive observation days ending at ``end_date``.

    Used to give the demo an index history without waiting 30 days. The
    resulting series is still fully labelled SYNTHETIC.
    """
    for i in range(days - 1, -1, -1):
        yield from generate_quotes(
            end_date - timedelta(days=i), routes, seed=seed, **kwargs
        )
