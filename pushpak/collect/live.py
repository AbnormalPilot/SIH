"""Component 1 — the live scraper.

This is a real HTTP client against real sources, gated on robots.txt and rate
limited. It is written to *fail loudly and honestly*:

  * If robots.txt disallows the path, no request is made and the source emits
    ``BLOCKED_BY_ROBOTS`` quotes with NULL fares.
  * If the fetch fails, it emits ``FETCH_FAILED``.
  * If the page loads but no fares can be extracted (almost always because the
    fare grid is client-rendered), it emits ``PARSE_FAILED``.

In every case the outcome is recorded in the audit ledger and surfaced in the
API and dashboard. What this module must never do is quietly return synthetic
numbers when live collection fails -- that decision belongs to the runner, and
the resulting rows are labelled SYNTHETIC.

Expected result today
---------------------
All configured fare pages are robots-disallowed or bot-blocked, so a live run
yields zero priced quotes and a full ledger of refusals. That is the correct
behaviour and it is the project's central empirical finding, not a bug.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from typing import Iterable, Iterator, Optional

import httpx
from bs4 import BeautifulSoup

from .. import config
from ..schema import (
    CollectionMode, FareQuote, QuoteStatus, SourceType, canonical_route_id,
    utc_now_iso,
)
from .audit import AuditLog
from .ratelimit import RateLimiter
from .robots import RobotsGate

# Indian fare strings: "₹5,499", "Rs. 5499", "INR 5,499"
_PRICE_RE = re.compile(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]{2,8})", re.IGNORECASE)
_CARRIER_RE = re.compile(r"\b(6E|AI|IX|QP|SG)[\s-]?(\d{2,4})\b")
_PLAUSIBLE_FARE = (1200.0, 90000.0)     # sanity band for a domestic economy fare


def parse_fares(html: str) -> list[dict]:
    """Best-effort fare extraction from a results page.

    Deliberately generic: each OTA renders differently and none of them are
    scrapeable today anyway. Kept simple and unit-tested against fixtures
    (tests/test_clean.py::test_parse_fares_*) rather than tuned to a live DOM
    we are not permitted to fetch.
    """
    out: list[dict] = []
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        text = " ".join(tag.stripped_strings)
        if not text or len(text) > 400:
            continue
        m = _PRICE_RE.search(text)
        if not m:
            continue
        try:
            fare = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if not (_PLAUSIBLE_FARE[0] <= fare <= _PLAUSIBLE_FARE[1]):
            continue
        cm = _CARRIER_RE.search(text)
        out.append({
            "total_fare": fare,
            "carrier": cm.group(1) if cm else "",
            "flight_number": f"{cm.group(1)}{cm.group(2)}" if cm else None,
        })

    # De-duplicate. Nested tags repeat the same price: an outer container
    # yields the fare with no carrier, an inner one yields it with the flight
    # number. Key on the fare alone and keep the richest match, otherwise every
    # fare is counted twice and the cell mean is unaffected but n_quotes lies.
    best: dict[float, dict] = {}
    for r in out:
        prev = best.get(r["total_fare"])
        if prev is None or (not prev["carrier"] and r["carrier"]):
            best[r["total_fare"]] = r
    return list(best.values())


def split_taxes(total: float) -> tuple[float, float]:
    """Split a gross fare into (base, taxes) using the statutory structure.

    Domestic economy: 5% GST on the fare component, plus fixed UDF/PSF/ASF.
    Recorded so the schema's base+taxes==total invariant holds for live rows
    too; flagged in notes as a derived split, not a scraped one.
    """
    fixed = 480.0
    fare_component = max(total - fixed, total * 0.5)
    taxes = round(total - fare_component / 1.05, 0)
    taxes = min(max(taxes, 0.0), total - 500.0)
    return round(total - taxes, 0), round(taxes, 0)


class LiveCollector:
    def __init__(self, audit: AuditLog, gate: Optional[RobotsGate] = None,
                 limiter: Optional[RateLimiter] = None, dry_run: bool = False):
        self.audit = audit
        self.gate = gate or RobotsGate()
        self.limiter = limiter or RateLimiter()
        self.dry_run = dry_run
        self.client = httpx.Client(
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-IN,en;q=0.9",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "LiveCollector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    def collect(
        self,
        observation_date: date,
        routes: Iterable[str],
        ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
        sources: Iterable[config.Source] = config.SOURCES,
    ) -> Iterator[FareQuote]:
        for src in sources:
            if not src.enabled:
                continue
            host = src.base_url.split("//", 1)[-1]
            for route_id in routes:
                a, b = route_id.split("-")
                for ap in ap_windows:
                    travel_date = observation_date + timedelta(days=ap)
                    for origin, dest in ((a, b), (b, a)):
                        yield from self._collect_one(
                            src, host, route_id, origin, dest,
                            observation_date, travel_date, ap,
                        )

    # ------------------------------------------------------------------
    def _collect_one(
        self, src: config.Source, host: str, route_id: str,
        origin: str, dest: str, observation_date: date, travel_date: date, ap: int,
    ) -> Iterator[FareQuote]:

        url = src.search_url_pattern.format(
            origin=origin.lower(), dest=dest.lower(),
            origin_slug=config.CITY_SLUGS.get(origin.upper(), origin.lower()),
            dest_slug=config.CITY_SLUGS.get(dest.upper(), dest.lower()),
            travel_date=travel_date.isoformat(),
            travel_date_compact=travel_date.strftime("%y%m%d"),
        )

        def stub(status: QuoteStatus, note: str, req_id: str | None) -> FareQuote:
            return FareQuote(
                origin=origin, destination=dest, route_id=route_id, carrier="",
                source=src.name, source_type=SourceType(src.source_type),
                collection_mode=CollectionMode.LIVE_SCRAPE,
                scrape_ts=utc_now_iso(),
                observation_date=observation_date.isoformat(),
                travel_date=travel_date.isoformat(),
                advance_purchase_days=ap,
                total_fare=None, base_fare=None, taxes_fees=None,
                quote_status=status, audit_request_id=req_id, notes=note,
            )

        # ---- gate 1: robots.txt ---------------------------------------
        verdict = self.gate.check(url)
        if config.RESPECT_ROBOTS and not verdict.allowed:
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern,
                outcome="BLOCKED_BY_ROBOTS", robots_checked=True,
                robots_allowed=False, robots_rule=verdict.rule,
                crawl_delay_s=verdict.crawl_delay,
                error=f"{verdict.reason}: {verdict.rule}",
            )
            yield stub(
                QuoteStatus.BLOCKED_BY_ROBOTS,
                f"robots.txt {verdict.reason} ({verdict.rule}); no request issued",
                req_id,
            )
            return

        # ---- gate 2: per-run request budget ---------------------------
        if self.limiter.budget_exhausted(host):
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="BUDGET_EXHAUSTED",
                robots_checked=True, robots_allowed=True,
                error=f"per-run cap of {config.MAX_REQUESTS_PER_SOURCE_PER_RUN} reached",
            )
            yield stub(QuoteStatus.FETCH_FAILED, "per-run request budget exhausted", req_id)
            return

        if self.dry_run:
            self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="DRY_RUN",
                robots_checked=True, robots_allowed=True,
                crawl_delay_s=verdict.crawl_delay,
            )
            return

        # ---- gate 3: rate limit ---------------------------------------
        interval = max(verdict.crawl_delay, src.min_seconds_between_requests)
        waited = self.limiter.wait(host, interval)

        started = time.perf_counter()
        try:
            resp = self.client.get(url)
            latency = (time.perf_counter() - started) * 1000
        except Exception as exc:                      # noqa: BLE001
            latency = (time.perf_counter() - started) * 1000
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="FETCH_FAILED",
                robots_checked=True, robots_allowed=True,
                crawl_delay_s=verdict.crawl_delay, waited_s=waited,
                latency_ms=latency, error=f"{type(exc).__name__}: {exc}",
            )
            yield stub(QuoteStatus.FETCH_FAILED, f"fetch error: {type(exc).__name__}", req_id)
            return

        if resp.status_code != 200:
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="HTTP_ERROR",
                robots_checked=True, robots_allowed=True,
                crawl_delay_s=verdict.crawl_delay, waited_s=waited,
                http_status=resp.status_code, response_bytes=len(resp.content),
                latency_ms=latency, error=f"HTTP {resp.status_code}",
            )
            yield stub(QuoteStatus.FETCH_FAILED, f"HTTP {resp.status_code}", req_id)
            return

        rows = parse_fares(resp.text)
        req_id = self.audit.record(
            source=src.name, host=host, url=url,
            url_pattern=src.search_url_pattern,
            outcome="OK" if rows else "PARSE_EMPTY",
            robots_checked=True, robots_allowed=True,
            crawl_delay_s=verdict.crawl_delay, waited_s=waited,
            http_status=200, response_bytes=len(resp.content), latency_ms=latency,
            error=None if rows else "no fare elements matched",
        )

        if not rows:
            yield stub(
                QuoteStatus.PARSE_FAILED,
                "page fetched but no fares extractable (client-rendered fare grid)",
                req_id,
            )
            return

        for r in rows:
            base_fare, taxes = split_taxes(r["total_fare"])
            yield FareQuote(
                origin=origin, destination=dest, route_id=route_id,
                carrier=r["carrier"], source=src.name,
                source_type=SourceType(src.source_type),
                collection_mode=CollectionMode.LIVE_SCRAPE,
                scrape_ts=utc_now_iso(),
                observation_date=observation_date.isoformat(),
                travel_date=travel_date.isoformat(),
                advance_purchase_days=ap,
                base_fare=base_fare, taxes_fees=taxes,
                total_fare=r["total_fare"],
                quote_status=QuoteStatus.OK,
                flight_number=r.get("flight_number"),
                audit_request_id=req_id,
                notes="tax split derived from statutory structure, not scraped",
            )
