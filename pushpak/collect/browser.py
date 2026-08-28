"""Component 1 — the rendering scraper (Playwright/Chromium).

Why this module exists
----------------------
Every Indian fare page is a single-page application. The HTML a static HTTP
client receives contains navigation, marketing copy and an empty ``<div>``; the
fares arrive later, from XHR calls the page makes after it boots. So a static
client does not collect *few* fares on these sites, it collects *zero*, and no
amount of parser tuning changes that. :mod:`pushpak.collect.live` demonstrates
the limit; this module is the answer to it.

What it does
------------
Drives a real Chromium via Playwright: navigates the search URL, waits for the
fare grid to paint, dismisses the consent/onboarding overlays that otherwise sit
on top of it, and reads prices out of the rendered DOM.

The four hard requirements from the brief, and how each is met
--------------------------------------------------------------
**JavaScript-rendered pages.** The whole point. ``_settle()`` waits for a
source-specific selector, then for network quiet, then for a currency token to
actually appear in the DOM — three conditions, because SPAs routinely satisfy
the first two while still showing a spinner.

**CAPTCHAs.** Detected, never solved. ``classify_wall()`` inspects the rendered
text for verification markers; a hit yields ``CAPTCHA_BLOCKED``, aborts the
source for the run, and the runner falls through to the licensed API. A CAPTCHA
is a site saying stop. A government statistical pipeline that taught itself to
answer one would be the story, not the index.

**Anti-bot measures.** Handled by being a real browser with honest headers
rather than by masquerading as a different one. We set an Indian locale and
timezone because we are collecting Indian consumer prices and a US-locale
browser is shown different fares — that is measurement correctness, not
evasion. The user agent still names Pushpak, so a site that wants to block us
can, and ``BOT_BLOCKED`` records it when one does.

**IP rotation.** ``ProxyPool``, fed from ``PUSHPAK_PROXIES``. Empty by default
and documented in config as being for regional egress (fares are geo-targeted),
not for evading a block already issued.

**Session management.** Cookies and localStorage are persisted per host under
``data/cache/sessions/`` and replayed, so we stop re-triggering each site's
consent flow on every request. That roughly halves the requests a site has to
serve us.

Ordering
--------
The robots gate still runs first and still decides. Rendering changes what we
can read from a page we are permitted to fetch; it never changes whether we are
permitted to fetch it. ``goindigo.in`` disallows ``/booking/*`` and is therefore
not collected here, browser or no browser.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from .. import config
from ..schema import (
    CollectionMode, FareQuote, QuoteStatus, SourceType, utc_now_iso,
)
from .audit import AuditLog
from .live import split_taxes
from .ratelimit import RateLimiter
from .robots import RobotsGate

# Rendered-DOM price tokens. Tighter than the static parser's pattern because a
# rendered page has far more incidental numbers on it (seat counts, baggage
# allowances, loyalty points) than a server-rendered one.
_PRICE_RE = re.compile(r"(?:₹|Rs\.?\s?|INR\s?)\s*([1-9][0-9,]{2,7})(?!\s*%)")
_FLIGHT_RE = re.compile(r"\b(6E|AI|IX|QP|SG)[\s\-]?(\d{2,4})\b")
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")

#: A domestic economy fare outside this band is not a fare. Rejecting here keeps
#: baggage charges (₹500) and total-package prices (₹1,20,000) out of the cell.
PLAUSIBLE_FARE = (1200.0, 90000.0)


# ---------------------------------------------------------------------------
# Egress rotation
# ---------------------------------------------------------------------------
class ProxyPool:
    """Round-robin over operator-supplied egress proxies. Empty by default."""

    def __init__(self, proxies: Optional[list[str]] = None):
        if proxies is None:
            raw = os.environ.get(config.PROXY_POOL_ENV, "")
            proxies = [p.strip() for p in raw.split(",") if p.strip()]
        self.proxies = proxies
        self._cycle = itertools.cycle(proxies) if proxies else None
        self._assigned: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def for_host(self, host: str) -> Optional[str]:
        """Stable per-host assignment, so one host sees one egress per run."""
        if self._cycle is None:
            return None
        if config.PROXY_ROTATE_PER_REQUEST:
            return next(self._cycle)
        if host not in self._assigned:
            self._assigned[host] = next(self._cycle)
        return self._assigned[host]

    @staticmethod
    def redact(proxy: Optional[str]) -> Optional[str]:
        """Strip credentials before anything touches the audit ledger."""
        if not proxy:
            return None
        return re.sub(r"//[^@/]*@", "//***@", proxy)


# ---------------------------------------------------------------------------
# Per-source recipes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Recipe:
    """How to get one source's fare grid on screen and where the prices are.

    Kept as data rather than as a subclass per site so that adding a source is
    a dict entry a reviewer can diff, and so that a selector that rots can be
    fixed without touching collection logic.
    """
    source: str
    #: Selectors that, once present, mean the fare grid has painted. Any one.
    ready_selectors: tuple[str, ...] = ()
    #: Rows to read prices out of. Tried in order; first non-empty wins.
    row_selectors: tuple[str, ...] = ()
    #: Consent banners / modals to dismiss before reading.
    dismiss_selectors: tuple[str, ...] = ()
    #: Extra settle time after the ready selector, for late-arriving prices.
    extra_settle_ms: int = 1500


RECIPES: dict[str, Recipe] = {
    "spicejet.com": Recipe(
        source="spicejet.com",
        ready_selectors=(
            "div[class*='flight-list']", "div[class*='FlightSelect']",
            "[data-testid*='flight']", "div[class*='fare']",
        ),
        row_selectors=(
            "div[class*='flight-row']", "div[class*='FlightRow']",
            "div[class*='flight-card']", "div[class*='fare-card']",
        ),
        dismiss_selectors=(
            "button:has-text('Accept')", "button:has-text('OK')",
            "[aria-label='Close']", "button:has-text('Got it')",
        ),
    ),
    "akasaair.com": Recipe(
        source="akasaair.com",
        ready_selectors=(
            "[class*='flight-detail']", "[class*='FlightCard']",
            "[data-testid*='flight']", "[class*='fare-type']",
        ),
        row_selectors=(
            "[class*='flight-card']", "[class*='FlightCard']",
            "[class*='flight-row']",
        ),
        dismiss_selectors=(
            "button:has-text('Accept')", "[aria-label='close']",
            "button:has-text('Allow')",
        ),
    ),
    "airindia.com": Recipe(
        source="airindia.com",
        ready_selectors=(
            "[class*='flight-list']", "[class*='fare-family']",
            "[class*='FlightResult']", "[data-testid*='flight']",
        ),
        row_selectors=(
            "[class*='flight-item']", "[class*='FlightResult']",
            "[class*='flight-card']",
        ),
        dismiss_selectors=(
            "button:has-text('Accept All')", "button:has-text('Accept')",
            "#onetrust-accept-btn-handler", "[aria-label='Close']",
        ),
    ),
    "airindiaexpress.com": Recipe(
        source="airindiaexpress.com",
        ready_selectors=(
            "[class*='flight']", "[class*='fare']", "[data-testid*='flight']",
        ),
        row_selectors=("[class*='flight-card']", "[class*='flight-row']"),
        dismiss_selectors=("button:has-text('Accept')", "[aria-label='Close']"),
    ),
    "yatra.com": Recipe(
        source="yatra.com",
        ready_selectors=(
            "[class*='flight-block']", "[class*='fare']", "[id*='flight']",
        ),
        row_selectors=("[class*='flight-block']", "[class*='flight-card']"),
        dismiss_selectors=("button:has-text('Accept')", "[aria-label='Close']"),
    ),
}

#: A source with no recipe still gets collected, using the generic whole-page
#: scan. The recipe only makes extraction more precise.
GENERIC_RECIPE = Recipe(source="generic")


# ---------------------------------------------------------------------------
# Wall detection
# ---------------------------------------------------------------------------
def classify_wall(text: str) -> Optional[QuoteStatus]:
    """Return CAPTCHA_BLOCKED / BOT_BLOCKED if the page is a wall, else None.

    Checked against a *short* rendered page only. A 200 KB results page that
    happens to contain the word "cloudflare" in a script tag is not a block, and
    treating it as one would silently discard real fares.
    """
    low = text.lower()
    if len(low) > 6000:
        return None
    if any(m in low for m in config.CAPTCHA_MARKERS):
        return QuoteStatus.CAPTCHA_BLOCKED
    if any(m in low for m in config.BOT_WALL_MARKERS):
        return QuoteStatus.BOT_BLOCKED
    return None


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def extract_from_texts(texts: Iterable[str]) -> list[dict]:
    """Pull (fare, carrier, flight_number, departure_time) out of row texts.

    One row of the rendered grid is one flight. Taking the *minimum* plausible
    price in a row is deliberate: airlines render a fare-family strip (Saver /
    Flexi / Business) inside one row, and the CPI-relevant quote is the lowest
    economy fare a consumer can actually buy, not the mean of the ladder.
    """
    out: list[dict] = []
    for text in texts:
        if not text:
            continue
        prices = [
            float(m.group(1).replace(",", "")) for m in _PRICE_RE.finditer(text)
        ]
        prices = [p for p in prices if PLAUSIBLE_FARE[0] <= p <= PLAUSIBLE_FARE[1]]
        if not prices:
            continue
        fm = _FLIGHT_RE.search(text)
        tm = _TIME_RE.search(text)
        out.append({
            "total_fare": min(prices),
            "carrier": fm.group(1) if fm else "",
            "flight_number": f"{fm.group(1)}{fm.group(2)}" if fm else None,
            "departure_time": f"{int(tm.group(1)):02d}:{tm.group(2)}" if tm else None,
        })

    # Two rows reporting the same flight number are the same flight rendered
    # twice (mobile + desktop DOM, or a sticky header). Keep the cheapest.
    best: dict[str, dict] = {}
    for r in out:
        key = r["flight_number"] or f"anon-{r['total_fare']:.0f}"
        if key not in best or r["total_fare"] < best[key]["total_fare"]:
            best[key] = r
    return list(best.values())


# ---------------------------------------------------------------------------
@dataclass
class BrowserRunStats:
    pages_rendered: int = 0
    fares_found: int = 0
    captcha_hits: int = 0
    bot_walls: int = 0
    nav_failures: int = 0
    empty_renders: int = 0
    proxy_enabled: bool = False
    notes: list[str] = field(default_factory=list)


class BrowserCollector:
    """Playwright-backed collector. Same interface shape as LiveCollector."""

    def __init__(
        self,
        audit: AuditLog,
        gate: Optional[RobotsGate] = None,
        limiter: Optional[RateLimiter] = None,
        proxies: Optional[ProxyPool] = None,
        headless: bool = config.BROWSER_HEADLESS,
        dry_run: bool = False,
    ):
        self.audit = audit
        self.gate = gate or RobotsGate()
        self.limiter = limiter or RateLimiter()
        self.proxies = proxies or ProxyPool()
        self.headless = headless
        self.dry_run = dry_run
        self.stats = BrowserRunStats(proxy_enabled=self.proxies.enabled)
        self._pw = None
        self._browser = None
        self._contexts: dict[str, object] = {}
        #: Sources that served a wall this run. Not retried — that is the point.
        self._abandoned: set[str] = set()

    # ------------------------------------------------------------------
    @staticmethod
    def available() -> tuple[bool, str]:
        """Is Playwright importable and is a browser installed?"""
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return False, (
                "playwright is not installed. Run:\n"
                "    pip install playwright && python3 -m playwright install chromium"
            )
        return True, "ok"

    def __enter__(self) -> "BrowserCollector":
        ok, msg = self.available()
        if not ok:
            raise RuntimeError(msg)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        launch: dict = {"headless": self.headless}
        self._browser = getattr(self._pw, config.BROWSER_ENGINE).launch(**launch)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for ctx in self._contexts.values():
            try:
                ctx.close()               # type: ignore[attr-defined]
            except Exception:             # noqa: BLE001 — teardown is best-effort
                pass
        self._contexts.clear()
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, meth)()
                except Exception:         # noqa: BLE001
                    pass
        self._browser = self._pw = None

    # ------------------------------------------------------------------
    def _session_path(self, host: str) -> Path:
        config.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        return config.SESSION_DIR / f"{host.replace(':', '_')}.json"

    def _context(self, host: str):
        """One browser context per host, with its session state replayed."""
        if host in self._contexts:
            return self._contexts[host]

        opts: dict = {
            "user_agent": config.USER_AGENT,
            "locale": config.BROWSER_LOCALE,
            "timezone_id": config.BROWSER_TIMEZONE,
            "viewport": config.BROWSER_VIEWPORT,
            "extra_http_headers": {"Accept-Language": "en-IN,en;q=0.9"},
        }
        proxy = self.proxies.for_host(host)
        if proxy:
            opts["proxy"] = {"server": proxy}

        state = self._session_path(host)
        if state.exists():
            age = time.time() - state.stat().st_mtime
            if age < config.SESSION_MAX_AGE_SECONDS:
                try:
                    json.loads(state.read_text())      # reject a truncated file
                    opts["storage_state"] = str(state)
                except Exception:                      # noqa: BLE001
                    state.unlink(missing_ok=True)

        ctx = self._browser.new_context(**opts)
        ctx.set_default_navigation_timeout(config.BROWSER_NAV_TIMEOUT_MS)
        self._contexts[host] = ctx
        return ctx

    def _save_session(self, host: str) -> None:
        ctx = self._contexts.get(host)
        if ctx is None:
            return
        try:
            ctx.storage_state(path=str(self._session_path(host)))  # type: ignore
        except Exception:                                          # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    def _dismiss(self, page, recipe: Recipe) -> int:
        """Click away consent banners. Each is best-effort and never fatal."""
        n = 0
        for sel in recipe.dismiss_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=800):
                    loc.click(timeout=1500)
                    n += 1
                    page.wait_for_timeout(250)
            except Exception:              # noqa: BLE001 — absent overlay is normal
                continue
        return n

    def _settle(self, page, recipe: Recipe) -> str:
        """Wait for the fare grid, then return the rendered page text.

        Three conditions rather than one, because an SPA reaches
        ``domcontentloaded`` and even ``networkidle`` while still showing a
        spinner. The condition that actually matters is the last: a currency
        token is on screen.
        """
        for sel in recipe.ready_selectors:
            try:
                page.wait_for_selector(sel, timeout=config.BROWSER_SETTLE_TIMEOUT_MS)
                break
            except Exception:              # noqa: BLE001 — try the next selector
                continue
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:                  # noqa: BLE001 — polling sites never idle
            pass
        try:
            page.wait_for_function(
                "() => /(₹|Rs\\.?|INR)\\s*[1-9][0-9,]{2,}/.test(document.body.innerText)",
                timeout=config.BROWSER_SETTLE_TIMEOUT_MS,
            )
        except Exception:                  # noqa: BLE001 — recorded as empty render
            pass
        page.wait_for_timeout(recipe.extra_settle_ms)
        try:
            return page.inner_text("body")
        except Exception:                  # noqa: BLE001
            return ""

    def _read_rows(self, page, recipe: Recipe, body_text: str) -> list[dict]:
        """Prefer structured rows; fall back to the whole rendered page."""
        for sel in recipe.row_selectors:
            try:
                texts = page.locator(sel).all_inner_texts()
            except Exception:              # noqa: BLE001
                continue
            rows = extract_from_texts(texts)
            if rows:
                return rows
        # Generic fallback: split the body on flight-number boundaries so each
        # chunk is one flight, then run the same extractor.
        chunks = _FLIGHT_RE.split(body_text)
        if len(chunks) > 2:
            rejoined = [
                f"{chunks[i]}{chunks[i + 1]}{chunks[i + 2]}"
                for i in range(1, len(chunks) - 2, 3)
            ]
            rows = extract_from_texts(rejoined)
            if rows:
                return rows
        return extract_from_texts([body_text])

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
                        if src.name in self._abandoned:
                            continue
                        yield from self._collect_one(
                            src, host, route_id, origin, dest,
                            observation_date, travel_date, ap,
                        )
            self._save_session(host)

    # ------------------------------------------------------------------
    def _collect_one(
        self, src: config.Source, host: str, route_id: str,
        origin: str, dest: str, observation_date: date, travel_date: date, ap: int,
    ) -> Iterator[FareQuote]:
        recipe = RECIPES.get(src.name, GENERIC_RECIPE)
        url = src.search_url_pattern.format(
            origin=origin.upper(), dest=dest.upper(),
            travel_date=travel_date.isoformat(),
            travel_date_compact=travel_date.strftime("%y%m%d"),
        )
        proxy_note = ProxyPool.redact(self.proxies.for_host(host))

        def stub(status: QuoteStatus, note: str, req_id: Optional[str]) -> FareQuote:
            return FareQuote(
                origin=origin, destination=dest, route_id=route_id, carrier="",
                source=src.name, source_type=SourceType(src.source_type),
                collection_mode=CollectionMode.BROWSER_SCRAPE,
                scrape_ts=utc_now_iso(),
                observation_date=observation_date.isoformat(),
                travel_date=travel_date.isoformat(),
                advance_purchase_days=ap,
                total_fare=None, base_fare=None, taxes_fees=None,
                quote_status=status, audit_request_id=req_id, notes=note,
            )

        # ---- gate 1: robots.txt. Rendering does not buy permission. --------
        verdict = self.gate.check(url)
        if config.RESPECT_ROBOTS and not verdict.allowed:
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="BLOCKED_BY_ROBOTS",
                robots_checked=True, robots_allowed=False, robots_rule=verdict.rule,
                crawl_delay_s=verdict.crawl_delay,
                error=f"{verdict.reason} [{verdict.decision}]: {verdict.rule}",
            )
            yield stub(
                QuoteStatus.BLOCKED_BY_ROBOTS,
                f"robots.txt {verdict.reason} ({verdict.decision}); no page opened",
                req_id,
            )
            return

        # ---- gate 2: per-run request budget --------------------------------
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
                error=f"would render in {config.BROWSER_ENGINE}; egress={proxy_note}",
            )
            return

        # ---- gate 3: rate limit --------------------------------------------
        interval = max(verdict.crawl_delay, src.min_seconds_between_requests)
        waited = self.limiter.wait(host, interval)

        page = None
        started = time.perf_counter()
        status: Optional[int] = None
        body_text = ""
        rows: list[dict] = []
        wall: Optional[QuoteStatus] = None
        nav_error: Optional[Exception] = None

        # Everything that needs a live page happens inside this block. Reading
        # rows after the page is closed was a real bug here once: extraction
        # silently fell back to a text-only scan and lost every structured row.
        try:
            page = self._context(host).new_page()
            resp = page.goto(url, wait_until="domcontentloaded")
            status = resp.status if resp is not None else None
            # Dismiss first: a consent modal can block the fare grid from ever
            # rendering, so waiting before dismissing waits forever.
            self._dismiss(page, recipe)
            body_text = self._settle(page, recipe)
            self._dismiss(page, recipe)
            wall = classify_wall(body_text)
            if wall is None:
                rows = self._read_rows(page, recipe, body_text)
            self.stats.pages_rendered += 1
        except Exception as exc:                       # noqa: BLE001
            nav_error = exc
        finally:
            latency = (time.perf_counter() - started) * 1000
            if page is not None:
                try:
                    page.close()
                except Exception:                      # noqa: BLE001
                    pass

        if nav_error is not None:
            self.stats.nav_failures += 1
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome="RENDER_FAILED",
                robots_checked=True, robots_allowed=True,
                crawl_delay_s=verdict.crawl_delay, waited_s=waited,
                latency_ms=latency,
                error=f"{type(nav_error).__name__}: {nav_error}"[:400],
            )
            yield stub(QuoteStatus.FETCH_FAILED,
                       f"render error: {type(nav_error).__name__}", req_id)
            return

        # ---- a wall means stop, for the whole source ------------------------
        if wall is not None:
            self._abandoned.add(src.name)
            if wall is QuoteStatus.CAPTCHA_BLOCKED:
                self.stats.captcha_hits += 1
            else:
                self.stats.bot_walls += 1
            self.stats.notes.append(
                f"{src.name}: {wall.value} — source abandoned for this run, "
                f"no retry, no rotation, no solver"
            )
            req_id = self.audit.record(
                source=src.name, host=host, url=url,
                url_pattern=src.search_url_pattern, outcome=wall.value,
                robots_checked=True, robots_allowed=True,
                crawl_delay_s=verdict.crawl_delay, waited_s=waited,
                http_status=status, response_bytes=len(body_text),
                latency_ms=latency,
                error="verification wall served; backing off for the run",
            )
            yield stub(
                wall,
                "human-verification wall served; Pushpak backs off rather than "
                "solving it (see README §Ethics)",
                req_id,
            )
            return

        self.stats.fares_found += len(rows)
        if not rows:
            self.stats.empty_renders += 1

        req_id = self.audit.record(
            source=src.name, host=host, url=url,
            url_pattern=src.search_url_pattern,
            outcome="OK" if rows else "RENDER_EMPTY",
            robots_checked=True, robots_allowed=True,
            crawl_delay_s=verdict.crawl_delay, waited_s=waited,
            http_status=status, response_bytes=len(body_text), latency_ms=latency,
            error=None if rows else "page rendered but no fare tokens present",
        )

        if not rows:
            yield stub(
                QuoteStatus.PARSE_FAILED,
                f"rendered {len(body_text)} chars, no fare found "
                f"(deep-link did not run a search, or no inventory)",
                req_id,
            )
            return

        for r in rows:
            base_fare, taxes = split_taxes(r["total_fare"])
            yield FareQuote(
                origin=origin, destination=dest, route_id=route_id,
                carrier=r["carrier"], source=src.name,
                source_type=SourceType(src.source_type),
                collection_mode=CollectionMode.BROWSER_SCRAPE,
                scrape_ts=utc_now_iso(),
                observation_date=observation_date.isoformat(),
                travel_date=travel_date.isoformat(),
                advance_purchase_days=ap,
                base_fare=base_fare, taxes_fees=taxes,
                total_fare=r["total_fare"],
                quote_status=QuoteStatus.OK,
                flight_number=r.get("flight_number"),
                departure_time=r.get("departure_time"),
                audit_request_id=req_id,
                notes="rendered in Chromium; tax split derived from the statutory "
                      "structure, not scraped",
            )
