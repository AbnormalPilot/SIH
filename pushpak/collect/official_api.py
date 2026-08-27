"""Component 1 — licensed fare data via an official API, not scraping.

Why this exists
---------------
The robots audit shows that no configured fare page yields prices to a
compliant static client. The README's conclusion is that a production Pushpak
should obtain fares through licensed channels rather than a faster crawler.
This module is that conclusion implemented, so the argument is demonstrable
rather than aspirational.

Why it is not Amadeus any more
------------------------------
This module previously used the Amadeus for Developers Self-Service Flight
Offers Search API. **Amadeus decommissioned that portal on 17 July 2026.**
It is not deprecated or gated — it is gone: ``api.amadeus.com`` and
``test.api.amadeus.com`` no longer resolve in DNS, so the old code path could
not succeed on any machine. Amadeus Enterprise is unaffected but requires a
commercial contract.

That is recorded rather than quietly patched, because it is the substantive
finding: **a national statistical product cannot rest on a commercial
self-service API that can be withdrawn at a vendor's discretion.** The DGCA
government-to-government channel in ``config.OFFICIAL_DATA_CHANNELS`` is the
answer to that, and this module is the interim stand-in.

What it uses now
----------------
The **Travelpayouts / Aviasales Flight Data API**
(``/aviasales/v3/prices_for_dates`` with ``one_way=true``), which has a free
self-service token and returns real observed fares for Indian domestic city
pairs.

Two things were found by probing the live API rather than reading the docs,
and both would have silently corrupted the index:

1. The older ``/v1/prices/calendar`` endpoint **accepts a departure date and
   then ignores it.** Asked for 2026-09-10 it returned sixteen other dates
   spread from August to the following February. An exact-date lookup against
   it collects almost nothing, which looks like thin coverage rather than a
   bug.
2. Every row that endpoint returns carries a ``return_at`` — they are
   **round-trip** prices. Indexing those as one-way fares would measure a
   different quantity entirely.

Hence the v3 endpoint with ``one_way=true``, queried by month and filtered to
exact travel dates locally.

One property must still be stated plainly, because it changes what the data
means: Travelpayouts serves **cached fare observations aggregated from
Aviasales user searches**, not live shopping quotes issued against inventory.
A cell gets a handful of observations rather than a full quote distribution,
and the underlying search may predate the collection run. Quotes are therefore
labelled ``OFFICIAL_API`` with a note naming the cache, and
``pushpak.cli collect --mode api --probe`` reports the observed coverage so
thinness is measured rather than assumed. Measured coverage on the shipped
15-route basket is **45 of 60 cells (75%)**.

Enabling it
-----------
    export TRAVELPAYOUTS_TOKEN=...     # free at travelpayouts.com/developers/api

    python3 -m pushpak.cli collect --mode api

Without a token the module reports itself unconfigured and the runner falls
back exactly as it would for any other source, with the fallback labelled. The
token is never written to the database or the audit log.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Optional

import httpx

from .. import config
from ..schema import (
    CollectionMode, FareQuote, QuoteStatus, SourceType, canonical_route_id,
    utc_now_iso,
)
from .audit import AuditLog
from .ratelimit import RateLimiter

HOST = "https://api.travelpayouts.com"
PRICES_PATH = "/aviasales/v3/prices_for_dates"
SOURCE_NAME = "travelpayouts"
PROVIDER = "Travelpayouts / Aviasales Flight Data API"

# The provider that used to sit here, kept as a machine-readable record rather
# than deleted. /api/v1/provenance and the README both cite it.
RETIRED_PROVIDERS = [
    {
        "provider": "Amadeus for Developers (Self-Service)",
        "endpoint": "https://test.api.amadeus.com /v2/shopping/flight-offers",
        "status": "DECOMMISSIONED",
        "date": "2026-07-17",
        "evidence": "host no longer resolves in DNS (NXDOMAIN)",
        "note": (
            "Announced February 2026, new registrations paused March 2026, "
            "portal decommissioned 17 July 2026. Amadeus Enterprise is "
            "unaffected but needs a commercial contract."
        ),
    },
]


@dataclass
class OfficialApiConfig:
    token: Optional[str] = None

    @classmethod
    def from_env(cls) -> "OfficialApiConfig":
        return cls(token=os.environ.get("TRAVELPAYOUTS_TOKEN"))

    @property
    def configured(self) -> bool:
        return bool(self.token)


class OfficialApiCollector:
    """Fetches real fare observations through a licensed API, not by scraping.

    Provider-neutral by name on purpose: the previous provider was withdrawn
    mid-project, so the seam that survives a provider change is the useful one.
    """

    def __init__(self, audit: AuditLog, cfg: Optional[OfficialApiConfig] = None,
                 limiter: Optional[RateLimiter] = None):
        self.cfg = cfg or OfficialApiConfig.from_env()
        self.audit = audit
        # Documented limit is 300 req/min. Stay well under it: this is
        # somebody else's infrastructure.
        self.limiter = limiter or RateLimiter(default_interval=0.5, jitter=0.2)
        self.client = httpx.Client(timeout=config.REQUEST_TIMEOUT_SECONDS)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OfficialApiCollector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def configured(self) -> bool:
        return self.cfg.configured

    # ------------------------------------------------------------------
    def _fetch_month(self, origin: str, dest: str, month: str) -> list[dict]:
        """One month of one-way quotes for a directional leg.

        Queried by month, not by exact date. The v1 calendar endpoint takes a
        date but ignores it — it returns whatever the cache happens to hold,
        scattered across months, and every row carries a ``return_at`` because
        those are round-trip prices. Both facts were found by probing the live
        API, not read off the docs. This endpoint with ``one_way=true`` returns
        genuinely one-way rows, which is the quantity an airfare index needs.
        """
        host = HOST.split("//")[-1]
        url_pattern = f"{HOST}{PRICES_PATH}?origin={{o}}&destination={{d}}&departure_at={{month}}&one_way=true"
        self.limiter.wait(host)
        started = time.perf_counter()
        try:
            r = self.client.get(
                f"{HOST}{PRICES_PATH}",
                params={
                    "origin": origin, "destination": dest,
                    "departure_at": month,
                    "one_way": "true",          # never round-trip prices
                    "currency": "inr",
                    "limit": 1000, "sorting": "price",
                },
                # Header rather than a query param, so the token cannot leak
                # into any URL that gets logged.
                headers={"x-access-token": self.cfg.token or ""},
            )
            latency = (time.perf_counter() - started) * 1000
        except Exception as exc:                        # noqa: BLE001
            self.audit.record(
                source=SOURCE_NAME, host=host, url_pattern=url_pattern,
                outcome="FETCH_FAILED", robots_checked=False,
                error=f"{type(exc).__name__}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            return []

        ok = r.status_code == 200
        req_id = self.audit.record(
            source=SOURCE_NAME, host=host, url_pattern=url_pattern,
            outcome="OK" if ok else ("AUTH_FAILED" if r.status_code in (401, 403)
                                     else "HTTP_ERROR"),
            robots_checked=False, http_status=r.status_code,
            response_bytes=len(r.content), latency_ms=latency,
            # Never the body: an auth error can echo the token back.
            error=None if ok else f"HTTP {r.status_code}",
        )
        if not ok:
            return []
        try:
            rows = r.json().get("data") or []
        except ValueError:
            return []
        for row in rows:
            row["_audit_request_id"] = req_id
        return rows

    # ------------------------------------------------------------------
    def collect(
        self,
        observation_date: date,
        routes: list[str],
        ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
    ) -> Iterator[FareQuote]:
        """Real one-way fares for exactly the basket's advance-purchase cells.

        Only rows whose departure date is *exactly* observation_date + N are
        kept, for N in the configured windows. Nothing is snapped to a nearby
        date: a fare for T+9 is not evidence about T+7, and bucketing it as
        though it were would quietly corrupt the advance-purchase dimension
        that the whole index rests on. Cells with no exact match stay empty and
        are handled by the imputation ladder, flagged, like any other gap.
        """
        if not self.configured:
            return

        want = {observation_date + timedelta(days=w): w for w in ap_windows}
        months = sorted({d.strftime("%Y-%m") for d in want})

        for route_id in routes:
            a, b = route_id.split("-")
            for origin, dest in ((a, b), (b, a)):
                for month in months:
                    for row in self._fetch_month(origin, dest, month):
                        dep = (row.get("departure_at") or "")[:10]
                        try:
                            dep_d = date.fromisoformat(dep)
                        except ValueError:
                            continue
                        ap = want.get(dep_d)
                        if ap is None:
                            continue
                        q = self._to_quote(
                            row, origin, dest, observation_date, dep_d, ap,
                            row.get("_audit_request_id"),
                        )
                        if q is not None:
                            yield q

    # ------------------------------------------------------------------
    @staticmethod
    def _to_quote(entry, origin, dest, observation_date, travel_date, ap,
                  req_id) -> Optional[FareQuote]:
        try:
            total = float(entry.get("price"))
            if total <= 0:
                return None
            carrier = (entry.get("airline") or "").strip()
            fno = entry.get("flight_number")
            dep = entry.get("departure_at") or ""

            return FareQuote(
                origin=origin, destination=dest,
                route_id=canonical_route_id(origin, dest),
                carrier=carrier, source=SOURCE_NAME,
                source_type=SourceType.GDS,
                collection_mode=CollectionMode.OFFICIAL_API,
                scrape_ts=utc_now_iso(),
                observation_date=observation_date.isoformat(),
                travel_date=travel_date.isoformat(),
                advance_purchase_days=ap,
                fare_class="ECONOMY",
                # The endpoint returns an all-in price with no base/tax split.
                # Leaving both NULL is honest; inventing a split would put a
                # fabricated number in a fare component.
                base_fare=None, taxes_fees=None, total_fare=round(total, 2),
                quote_status=QuoteStatus.OK,
                flight_number=f"{carrier}{fno}" if carrier and fno else None,
                departure_time=(dep[11:16] or None) if len(dep) >= 16 else None,
                stops=int(entry.get("transfers") or 0),
                audit_request_id=req_id,
                notes=(
                    "licensed API content (Travelpayouts/Aviasales one-way "
                    "cached fare), not scraped; cache may predate this run"
                ),
            )
        except (TypeError, ValueError, KeyError):
            return None


def probe(routes: Optional[list[str]] = None,
          ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
          observation_date: Optional[date] = None) -> dict:
    """Measure how much of the basket this channel actually covers.

    Runs the *real* collector and counts what comes back, rather than
    reimplementing the fetch. A probe that measures a parallel code path can
    drift from the thing it claims to describe.

    Coverage is reported, never assumed. If Indian domestic depth turns out to
    be thin, that is a finding to state, not a reason to dress up sparse data.
    Nothing is written to the quote store.
    """
    cfg = OfficialApiConfig.from_env()
    out = {
        "provider": PROVIDER,
        "configured": cfg.configured,
        "cells_probed": 0,
        "cells_with_a_fare": 0,
        "coverage_share": 0.0,
        "quotes_returned": 0,
        "routes_with_a_fare": [],
        "coverage_by_window": {},
        "sample": [],
    }
    if not cfg.configured:
        out["note"] = "TRAVELPAYOUTS_TOKEN not set; nothing probed."
        return out

    if routes is None:
        import pandas as pd
        routes = list(pd.read_csv(config.ROUTE_WEIGHTS_CSV)["route_id"])

    obs = observation_date or date.today()
    out["observation_date"] = obs.isoformat()
    out["cells_probed"] = len(routes) * len(ap_windows)

    cells: dict[tuple[str, int], list[float]] = {}
    # Probe requests are real requests, so they belong on the audit ledger like
    # any other. Tagged as a probe run so they are not mistaken for collection.
    audit = AuditLog(f"probe-{obs.isoformat()}-{uuid.uuid4().hex[:8]}")
    try:
        with OfficialApiCollector(audit, cfg) as c:
            for q in c.collect(obs, routes, ap_windows):
                out["quotes_returned"] += 1
                cells.setdefault((q.route_id, q.advance_purchase_days), []).append(
                    q.total_fare
                )
    finally:
        audit.flush()

    out["cells_with_a_fare"] = len(cells)
    if out["cells_probed"]:
        out["coverage_share"] = round(
            out["cells_with_a_fare"] / out["cells_probed"], 4
        )
    out["routes_with_a_fare"] = sorted({r for r, _ in cells})
    out["coverage_by_window"] = {
        f"T+{w}": f"{sum(1 for (_, x) in cells if x == w)}/{len(routes)}"
        for w in ap_windows
    }
    for (rid, ap), prices in sorted(cells.items())[:6]:
        out["sample"].append({
            "route": rid, "ap_days": ap, "n": len(prices),
            "min_inr": round(min(prices)), "mean_inr": round(sum(prices) / len(prices)),
        })
    return out


def status() -> dict:
    """Whether the official channel is usable, for the API and the dashboard."""
    cfg = OfficialApiConfig.from_env()
    return {
        "provider": PROVIDER,
        "configured": cfg.configured,
        "endpoint": f"{HOST}{PRICES_PATH}?one_way=true",
        "docs": "https://support.travelpayouts.com/hc/en-us/sections/201008338",
        "enable_with": [
            "export TRAVELPAYOUTS_TOKEN=...",
            "python3 -m pushpak.cli collect --mode api",
        ],
        "data_character": (
            "Cached one-way fare observations aggregated from Aviasales user "
            "searches, not live shopping quotes against inventory. A cell gets "
            "a handful of observations rather than a full quote distribution, "
            "and the underlying search may predate the collection run."
        ),
        "measured_coverage": {
            "basket": f"{config.BASKET_SIZE} routes x "
                      f"{len(config.ADVANCE_PURCHASE_WINDOWS)} windows",
            "observed": ["45/60 (75%) on 2026-08-26", "38/60 (63%) on 2026-08-27"],
            "reproduce_with": "python3 -m pushpak.cli collect --mode api --probe",
            "note": (
                "Coverage is not a fixed property of the channel: it depends on "
                "which travel dates the cache happens to hold on the day you "
                "ask, and it moved 12 points overnight. Never quote a single "
                "figure as the channel's coverage — re-run the probe."
            ),
        },
        "note": (
            "Licensed API content, labelled OFFICIAL_API. Not scraping, and not "
            "synthetic. The token is read from the environment and is never "
            "written to the database or the audit log."
        ),
        "retired_providers": RETIRED_PROVIDERS,
    }
