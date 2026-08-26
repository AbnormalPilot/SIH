"""Component 1 — licensed fare data via an official API, not scraping.

Why this exists
---------------
The robots audit shows that no configured fare page yields prices to a
compliant static client. The README's conclusion is that a production Pushpak
should obtain fares through licensed channels rather than a faster crawler.
This module is that conclusion implemented, so the argument is demonstrable
rather than aspirational.

It uses the **Amadeus for Developers Self-Service** Flight Offers Search API,
which has a free tier and returns real airline content. Quotes arrive labelled
``collection_mode=OFFICIAL_API``: not scraped, and not synthetic either.

Enabling it
-----------
    export AMADEUS_CLIENT_ID=...
    export AMADEUS_CLIENT_SECRET=...
    export AMADEUS_ENV=test          # or "production"

    python3 -m pushpak.cli collect --mode api

Without credentials the module reports itself unconfigured and the runner falls
back exactly as it would for any other source, with the fallback labelled. No
credential is ever written to the database or the audit log.
"""
from __future__ import annotations

import os
import time
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

HOSTS = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}
SOURCE_NAME = "amadeus"


@dataclass
class AmadeusConfig:
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    env: str = "test"

    @classmethod
    def from_env(cls) -> "AmadeusConfig":
        return cls(
            client_id=os.environ.get("AMADEUS_CLIENT_ID"),
            client_secret=os.environ.get("AMADEUS_CLIENT_SECRET"),
            env=os.environ.get("AMADEUS_ENV", "test"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def host(self) -> str:
        return HOSTS.get(self.env, HOSTS["test"])


class AmadeusCollector:
    """Fetches real fare offers through a licensed API."""

    def __init__(self, audit: AuditLog, cfg: Optional[AmadeusConfig] = None,
                 limiter: Optional[RateLimiter] = None):
        self.cfg = cfg or AmadeusConfig.from_env()
        self.audit = audit
        # The free tier is rate limited; be conservative regardless.
        self.limiter = limiter or RateLimiter(default_interval=1.0, jitter=0.3)
        self._token: Optional[str] = None
        self._token_expires: float = 0.0
        self.client = httpx.Client(timeout=config.REQUEST_TIMEOUT_SECONDS)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "AmadeusCollector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def configured(self) -> bool:
        return self.cfg.configured

    # ------------------------------------------------------------------
    def _access_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_expires - 30:
            return self._token
        try:
            r = self.client.post(
                f"{self.cfg.host}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.cfg.client_id,
                    "client_secret": self.cfg.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                self.audit.record(
                    source=SOURCE_NAME, host=self.cfg.host.split("//")[-1],
                    url_pattern=f"{self.cfg.host}/v1/security/oauth2/token",
                    outcome="AUTH_FAILED", robots_checked=False,
                    http_status=r.status_code,
                    error=f"HTTP {r.status_code}",   # body may echo the secret
                )
                return None
            body = r.json()
            self._token = body.get("access_token")
            self._token_expires = time.time() + float(body.get("expires_in", 1799))
            return self._token
        except Exception as exc:                       # noqa: BLE001
            self.audit.record(
                source=SOURCE_NAME, host=self.cfg.host.split("//")[-1],
                url_pattern=f"{self.cfg.host}/v1/security/oauth2/token",
                outcome="AUTH_FAILED", robots_checked=False,
                error=f"{type(exc).__name__}",
            )
            return None

    # ------------------------------------------------------------------
    def collect(
        self,
        observation_date: date,
        routes: list[str],
        ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
        max_offers: int = 20,
    ) -> Iterator[FareQuote]:
        if not self.configured:
            return

        token = self._access_token()
        if not token:
            return

        host = self.cfg.host.split("//")[-1]
        url_pattern = f"{self.cfg.host}/v2/shopping/flight-offers?originLocationCode={{o}}&destinationLocationCode={{d}}&departureDate={{date}}"

        for route_id in routes:
            a, b = route_id.split("-")
            for ap in ap_windows:
                travel_date = observation_date + timedelta(days=ap)
                for origin, dest in ((a, b), (b, a)):
                    self.limiter.wait(host)
                    started = time.perf_counter()
                    try:
                        r = self.client.get(
                            f"{self.cfg.host}/v2/shopping/flight-offers",
                            params={
                                "originLocationCode": origin,
                                "destinationLocationCode": dest,
                                "departureDate": travel_date.isoformat(),
                                "adults": 1,
                                "currencyCode": "INR",
                                "nonStop": "true",
                                "max": max_offers,
                            },
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        latency = (time.perf_counter() - started) * 1000
                    except Exception as exc:            # noqa: BLE001
                        self.audit.record(
                            source=SOURCE_NAME, host=host, url_pattern=url_pattern,
                            outcome="FETCH_FAILED", robots_checked=False,
                            error=f"{type(exc).__name__}",
                            latency_ms=(time.perf_counter() - started) * 1000,
                        )
                        continue

                    req_id = self.audit.record(
                        source=SOURCE_NAME, host=host, url_pattern=url_pattern,
                        outcome="OK" if r.status_code == 200 else "HTTP_ERROR",
                        robots_checked=False, http_status=r.status_code,
                        response_bytes=len(r.content), latency_ms=latency,
                        error=None if r.status_code == 200 else f"HTTP {r.status_code}",
                    )
                    if r.status_code != 200:
                        continue

                    for offer in (r.json().get("data") or []):
                        q = self._to_quote(
                            offer, origin, dest, route_id, observation_date,
                            travel_date, ap, req_id,
                        )
                        if q is not None:
                            yield q

    # ------------------------------------------------------------------
    @staticmethod
    def _to_quote(offer, origin, dest, route_id, observation_date, travel_date,
                  ap, req_id) -> Optional[FareQuote]:
        try:
            price = offer.get("price") or {}
            total = float(price.get("grandTotal") or price.get("total"))
            base = float(price.get("base") or 0.0) or None
            taxes = round(total - base, 2) if base else None

            carriers = offer.get("validatingAirlineCodes") or []
            carrier = carriers[0] if carriers else ""

            seg = None
            itin = (offer.get("itineraries") or [{}])[0]
            segs = itin.get("segments") or []
            if segs:
                seg = segs[0]

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
                base_fare=base, taxes_fees=taxes, total_fare=round(total, 2),
                quote_status=QuoteStatus.OK,
                flight_number=(
                    f"{seg.get('carrierCode','')}{seg.get('number','')}" if seg else None
                ),
                departure_time=(
                    (seg.get("departure", {}).get("at", "") or "")[11:16] or None
                    if seg else None
                ),
                stops=max(len(segs) - 1, 0),
                audit_request_id=req_id,
                notes="licensed API content (Amadeus Flight Offers Search), not scraped",
            )
        except (TypeError, ValueError, KeyError):
            return None


def status() -> dict:
    """Whether the official channel is usable, for the API and the dashboard."""
    cfg = AmadeusConfig.from_env()
    return {
        "provider": "Amadeus for Developers, Flight Offers Search",
        "configured": cfg.configured,
        "environment": cfg.env,
        "docs": "https://developers.amadeus.com",
        "enable_with": [
            "export AMADEUS_CLIENT_ID=...",
            "export AMADEUS_CLIENT_SECRET=...",
            "python3 -m pushpak.cli collect --mode api",
        ],
        "note": (
            "Licensed API content, labelled OFFICIAL_API. Not scraping, and not "
            "synthetic. Credentials are read from the environment and are never "
            "written to the database or the audit log."
        ),
    }
