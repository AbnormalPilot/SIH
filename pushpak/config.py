"""Pushpak configuration: the basket, the weights, and their provenance.

Everything that a reviewer would want to audit lives here as data, not as
magic numbers scattered through the code.  Each weight set carries an explicit
``provenance`` tag so the README's honesty claims are machine-checkable:

  DGCA_DERIVED   -> computed from the official DGCA city-pair traffic release
                    that ships in data/. Reproducible via scripts/build_weights.py
  ASSUMED_PRIOR  -> a documented modelling assumption, NOT an official statistic.
                    Must be replaced with real booking-curve data before any
                    production use.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("PUSHPAK_DB", DATA_DIR / "pushpak.db"))
AUDIT_LOG_PATH = Path(os.environ.get("PUSHPAK_AUDIT_LOG", DATA_DIR / "scrape_audit.jsonl"))
DGCA_TRAFFIC_CSV = DATA_DIR / "dgca_domestic_city_pair_traffic.csv"
ROUTE_WEIGHTS_CSV = DATA_DIR / "route_weights.csv"
DGCA_FARE_BENCHMARK_CSV = DATA_DIR / "dgca_fare_benchmark.csv"

# --------------------------------------------------------------------------
# 1. THE BASKET  (component 1: route selection)
# --------------------------------------------------------------------------
# Chosen as the top-10 domestic city pairs by passengers carried over the most
# recent 12 months in the DGCA release shipped in data/.  Together they carry
# ~20.0% of ALL Indian domestic passengers.  Weights are recomputed from that
# file by scripts/build_weights.py — they are never hand-typed.
#
# The selection rule is "top N by DGCA traffic", applied mechanically. It is
# stated as a rule rather than a list precisely so that nobody can accuse the
# basket of being cherry-picked: raising BASKET_SIZE and re-running
# scripts/build_weights.py is the only supported way to change it.

IATA = {
    "DELHI": "DEL",
    "MUMBAI": "BOM",
    "BENGALURU": "BLR",
    "HYDERABAD": "HYD",
    "KOLKATA": "CCU",
    "CHENNAI": "MAA",
    "PUNE": "PNQ",
    "AHMEDABAD": "AMD",
}

# DGCA renamed Mumbai's entry when Navi Mumbai (NMIA) opened, splitting one
# city into three labels mid-series.  Left unhandled this silently halves the
# busiest route in the country.  Metro-area is the correct unit for a city-pair
# fare index, so the three labels collapse to one city.
DGCA_CITY_ALIASES = {
    "MUMBAI (MUMBAI)": "MUMBAI",
    "MUMBAI (NAVI MUMBAI)": "MUMBAI",
    "GOA (DABOLIM, SOUTH GOA)": "GOA",
    "GOA (MOPA, NORTH GOA)": "GOA",
    "MOPA, GOA": "GOA",
}

BASKET_SIZE = 10
TRAFFIC_WINDOW_MONTHS = 12


@dataclass(frozen=True)
class Route:
    """One elementary route in the fixed basket."""
    route_id: str          # canonical undirected id, e.g. "BLR-DEL"
    city_a: str
    city_b: str
    iata_a: str
    iata_b: str
    weight: float          # share of basket passengers (sums to 1.0 across basket)
    annual_pax: float
    national_share: float  # share of ALL domestic pax, for context

    @property
    def legs(self) -> list[tuple[str, str]]:
        """Both directional legs. Fares are directional; weights are not."""
        return [(self.iata_a, self.iata_b), (self.iata_b, self.iata_a)]


# --------------------------------------------------------------------------
# 2. ADVANCE-PURCHASE WINDOWS  (component 1: AP granularity)
# --------------------------------------------------------------------------
# The brief's T+1 / T+7 / T+15 / T+30 / T+45. Each (route, AP window) pair is
# one "elementary aggregate" cell in CPI terminology.
ADVANCE_PURCHASE_WINDOWS: tuple[int, ...] = (1, 7, 15, 30, 45)

# Within-route weights across AP windows. These approximate the domestic
# booking curve (most Indian domestic leisure+business booking is short-lead).
#
#   *** PROVENANCE: ASSUMED_PRIOR — this is NOT an official statistic. ***
#
# A production Pushpak would source these from airline/OTA booking-date
# distributions obtained under a data-sharing MoU (see README §Ethics).
# They are kept here, versioned and normalised, precisely so a reviewer can
# see exactly which part of the index rests on an assumption.
#
# When T+45 was added, the four pre-existing shares were NOT re-guessed. Each
# was multiplied by exactly 0.90 and the freed 0.10 assigned to T+45, so the
# relative shape of the curve is unchanged and the edit is reproducible in one
# line of arithmetic. That rule is the whole justification; it is written down
# rather than buried in a commit message.
AP_WINDOW_WEIGHTS: dict[int, float] = {
    1: 0.18,   # walk-up / last-minute, disproportionately business travel
    7: 0.27,
    15: 0.25,
    30: 0.20,
    45: 0.10,  # added 2026-08-26: prior shares x 0.90, remainder to T+45
}
AP_WINDOW_WEIGHT_PROVENANCE = "ASSUMED_PRIOR"
ROUTE_WEIGHT_PROVENANCE = "DGCA_DERIVED"

# --------------------------------------------------------------------------
# 3. INDEX PARAMETERS  (component 3)
# --------------------------------------------------------------------------
BASE_INDEX_VALUE = 100.0
BASE_PERIOD_DAYS = 3          # base price = mean cell price over first N days
MAX_CARRY_FORWARD_DAYS = 7    # staleness cap on last-observation-carried-forward
OUTLIER_MULTIPLE = 5.0        # a quote > 5x the cell median is treated as bad data
MIN_QUOTES_PER_CELL = 1       # below this a cell is "thin" and gets flagged

# Quality thresholds surfaced in the API/UI so consumers can gate on them.
QUALITY_THRESHOLDS = {
    "green": {"max_imputed_share": 0.10, "max_synthetic_share": 0.00},
    "amber": {"max_imputed_share": 0.30, "max_synthetic_share": 1.00},
    # anything worse is "red"
}

# --------------------------------------------------------------------------
# 4. SOURCES  (component 1: >=1 airline site + >=1 OTA)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    name: str
    source_type: str          # "AIRLINE" | "OTA"
    base_url: str
    robots_url: str
    # A URL *pattern* is logged for audit rather than only the concrete URL,
    # so the audit trail shows the access shape without hoarding query strings.
    search_url_pattern: str
    min_seconds_between_requests: float = 6.0
    enabled: bool = True


SOURCES: tuple[Source, ...] = (
    # ---- AIRLINE sources -------------------------------------------------
    Source(
        name="goindigo.in",
        source_type="AIRLINE",
        base_url="https://www.goindigo.in",
        robots_url="https://www.goindigo.in/robots.txt",
        search_url_pattern="https://www.goindigo.in/booking/flight-select.html?origin={origin}&destination={dest}&departureDate={travel_date}",
        min_seconds_between_requests=8.0,
    ),
    Source(
        name="airindia.com",
        source_type="AIRLINE",
        base_url="https://www.airindia.com",
        robots_url="https://www.airindia.com/robots.txt",
        search_url_pattern="https://www.airindia.com/in/en/book/flight-search.html?origin={origin}&destination={dest}&departureDate={travel_date}",
        min_seconds_between_requests=8.0,
    ),
    # ---- OTA / metasearch sources ---------------------------------------
    Source(
        name="skyscanner.co.in",
        source_type="OTA",
        base_url="https://www.skyscanner.co.in",
        robots_url="https://www.skyscanner.co.in/robots.txt",
        # Skyscanner's fare pages live under /transport/flights/... , which its
        # robots.txt Disallows for User-agent: * . The gate in collect/robots.py
        # therefore refuses this source by design -- see README section 5.
        search_url_pattern="https://www.skyscanner.co.in/transport/flights/{origin}/{dest}/{travel_date_compact}/",
        min_seconds_between_requests=6.0,
    ),
    Source(
        name="cleartrip.com",
        source_type="OTA",
        base_url="https://www.cleartrip.com",
        robots_url="https://www.cleartrip.com/robots.txt",
        search_url_pattern="https://www.cleartrip.com/flights/search?from={origin}&to={dest}&depart_date={travel_date}&adults=1&class=Economy",
        min_seconds_between_requests=8.0,
    ),
    Source(
        name="ixigo.com",
        source_type="OTA",
        base_url="https://www.ixigo.com",
        robots_url="https://www.ixigo.com/robots.txt",
        search_url_pattern="https://www.ixigo.com/search/result/flight?from={origin}&to={dest}&date={travel_date}&adults=1&class=e",
        min_seconds_between_requests=8.0,
    ),
    # These two permit crawling. SpiceJet's robots.txt allows everything except
    # /cgi-bin/, /api/v1, /public/ and /externalBooking; Akasa Air publishes a
    # robots.txt with no Disallow at all. They are in the basket precisely so
    # the gate is demonstrably discriminating rather than blanket-refusing.
    Source(
        name="spicejet.com",
        source_type="AIRLINE",
        base_url="https://www.spicejet.com",
        robots_url="https://www.spicejet.com/robots.txt",
        # NOT /api/v1: SpiceJet disallows its booking API by absolute URL, and
        # the gate honours that (see collect/robots.py). We use the public page.
        search_url_pattern="https://www.spicejet.com/?departure={origin}&arrival={dest}&departureDate={travel_date}&adult=1",
        min_seconds_between_requests=8.0,
    ),
    Source(
        name="akasaair.com",
        source_type="AIRLINE",
        base_url="https://www.akasaair.com",
        robots_url="https://www.akasaair.com/robots.txt",
        search_url_pattern="https://www.akasaair.com/book-flight-tickets?origin={origin}&destination={dest}&departureDate={travel_date}",
        min_seconds_between_requests=8.0,
    ),
    Source(
        name="easemytrip.com",
        source_type="OTA",
        base_url="https://www.easemytrip.com",
        robots_url="https://www.easemytrip.com/robots.txt",
        search_url_pattern="https://www.easemytrip.com/flight-search/listing?from={origin}&to={dest}&date={travel_date}",
        min_seconds_between_requests=8.0,
    ),
    # The four below were added after the first audit, on the reasoning that a
    # compliance claim is only interesting if the sample is wide enough to be
    # falsifiable. They are the remaining large Indian booking surfaces: one
    # more LCC and the three biggest domestic OTAs. Their verdicts are whatever
    # the gate returns -- they were not pre-screened for a convenient answer.
    Source(
        name="airindiaexpress.com",
        source_type="AIRLINE",
        base_url="https://www.airindiaexpress.com",
        robots_url="https://www.airindiaexpress.com/robots.txt",
        search_url_pattern="https://www.airindiaexpress.com/booking/select-flight?origin={origin}&destination={dest}&departureDate={travel_date}",
        min_seconds_between_requests=8.0,
    ),
    Source(
        name="makemytrip.com",
        source_type="OTA",
        base_url="https://www.makemytrip.com",
        robots_url="https://www.makemytrip.com/robots.txt",
        search_url_pattern="https://www.makemytrip.com/flight/search?itinerary={origin}-{dest}-{travel_date}&tripType=O&paxType=A-1&cabinClass=E",
        min_seconds_between_requests=10.0,
    ),
    Source(
        name="goibibo.com",
        source_type="OTA",
        base_url="https://www.goibibo.com",
        robots_url="https://www.goibibo.com/robots.txt",
        search_url_pattern="https://www.goibibo.com/flights/air-{origin}-{dest}-{travel_date_compact}-1-0-0-E-D/",
        min_seconds_between_requests=10.0,
    ),
    Source(
        name="yatra.com",
        source_type="OTA",
        base_url="https://www.yatra.com",
        robots_url="https://www.yatra.com/robots.txt",
        search_url_pattern="https://www.yatra.com/air-search-ui/dom2/trigger?type=O&origin={origin}&destination={dest}&flight_depart_date={travel_date}&class=Economy&adults=1",
        min_seconds_between_requests=10.0,
    ),
)

# Official partner APIs that a production Pushpak should use INSTEAD of scraping.
# Recorded here because the brief asks what a production version would do
# differently: it would sign data-sharing agreements, not scrape harder.
OFFICIAL_DATA_CHANNELS = {
    "skyscanner.co.in": {
        "channel": "Skyscanner Partners - Flights Live Prices / Indicative Prices API",
        "url": "https://www.partners.skyscanner.net/product/travel-api",
        "access": "PARTNER_ONLY_APPLICATION_REQUIRED",
        "note": "Closed API. Approval is discretionary and typically takes ~2 weeks.",
    },
    "goindigo.in": {
        "channel": "Airline distribution feed / NDC or direct MoU with MoSPI",
        "url": "https://www.goindigo.in",
        "access": "COMMERCIAL_AGREEMENT_REQUIRED",
        "note": "Preferred production channel: statutory data-sharing MoU.",
    },
    "airindia.com": {
        "channel": "Airline distribution feed / NDC or direct MoU with MoSPI",
        "url": "https://www.airindia.com",
        "access": "COMMERCIAL_AGREEMENT_REQUIRED",
        "note": "Preferred production channel: statutory data-sharing MoU.",
    },
    "spicejet.com": {
        "channel": "Airline distribution feed or MoU",
        "url": "https://www.spicejet.com",
        "access": "CRAWLING_PERMITTED_BUT_NO_STATIC_FARES",
        "note": (
            "robots.txt permits the public search page, but fares are rendered "
            "client-side from /api/v1, which robots.txt disallows. Compliant "
            "static collection therefore yields no prices."
        ),
    },
    "akasaair.com": {
        "channel": "Airline distribution feed or MoU",
        "url": "https://www.akasaair.com",
        "access": "CRAWLING_PERMITTED_BUT_NO_STATIC_FARES",
        "note": (
            "robots.txt contains no Disallow at all. Fares are still rendered "
            "client-side, so a compliant static client sees promotional banners "
            "rather than route-level quotes."
        ),
    },
    "amadeus": {
        "channel": "Amadeus for Developers, Flight Offers Search",
        "url": "https://developers.amadeus.com",
        "access": "SELF_SERVICE_API_KEY",
        "note": (
            "Free self-service test tier with real airline content. This is the "
            "supported way to obtain live fares without scraping, and Pushpak "
            "uses it when AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET are set."
        ),
    },
    "airindiaexpress.com": {
        "channel": "Airline distribution feed or MoU",
        "url": "https://www.airindiaexpress.com",
        "access": "CRAWLING_PERMITTED_BUT_NO_STATIC_FARES",
        "note": (
            "robots.txt permits the booking path. Verified 2026-08-26: the URL "
            "returns an 8 KB single-page-app shell with zero currency-shaped "
            "tokens and zero occurrences of the word 'flight'. Permission to "
            "crawl and availability of data are independent problems."
        ),
    },
    "makemytrip.com": {
        "channel": "MakeMyTrip affiliate / partner API",
        "url": "https://www.makemytrip.com",
        "access": "PARTNER_ONLY_APPLICATION_REQUIRED",
        "note": (
            "robots.txt itself is unreachable to a declared non-browser client, "
            "so the fail-closed gate refuses the source. No permission can be "
            "inferred from a document that cannot be read."
        ),
    },
    "goibibo.com": {
        "channel": "Goibibo (MakeMyTrip group) partner API",
        "url": "https://www.goibibo.com",
        "access": "PARTNER_ONLY_APPLICATION_REQUIRED",
        "note": "robots.txt unreachable to a declared bot; gate fails closed.",
    },
    "yatra.com": {
        "channel": "Yatra partner / affiliate feed",
        "url": "https://www.yatra.com",
        "access": "PARTNER_ONLY_APPLICATION_REQUIRED",
        "note": "robots.txt unreachable to a declared bot; gate fails closed.",
    },
    "_general": {
        "channel": "DGCA Tariff Monitoring Unit fare submissions",
        "url": "https://www.dgca.gov.in",
        "access": "GOVT_TO_GOVT",
        "note": (
            "Airlines already file fare data with DGCA's Tariff Monitoring Unit. "
            "The cheapest production path for MoSPI is an inter-departmental "
            "feed from DGCA, not scraping OTAs at all."
        ),
    },
}

# Carriers modelled by the synthetic generator and expected from live parsing.
CARRIERS = {
    "6E": "IndiGo",
    "AI": "Air India",
    "IX": "Air India Express",
    "QP": "Akasa Air",
    "SG": "SpiceJet",
}

# --------------------------------------------------------------------------
# Fare classes.
#
# The schema, the collectors and the index all carry fare_class end to end, so
# a premium-cabin index is a config change and not a rewrite. Only ECONOMY is
# COLLECTED by default: it is the cabin the CPI basket is about, and generating
# three cabins triples the row count for a prototype that gains nothing by it.
#
# ``INDEX_FARE_CLASS`` is what the published Pushpak Index is computed over.
# Mixing cabins inside one elementary aggregate would be a methodological
# error -- a business fare is a different product, not a dearer economy seat --
# so the index takes exactly one class and compute_index() filters on it.
# --------------------------------------------------------------------------
FARE_CLASSES = ("ECONOMY", "PREMIUM_ECONOMY", "BUSINESS")
COLLECT_FARE_CLASSES: tuple[str, ...] = ("ECONOMY",)
INDEX_FARE_CLASS = "ECONOMY"

# --------------------------------------------------------------------------
# 4b. SOURCE DISCOVERY  (component 1: widen the compliance evidence)
#
# The twelve sources above were chosen by hand, which is a fair objection to
# the project's central finding: of course a hand-picked list refuses, someone
# picked it. Discovery answers that by letting a search engine choose instead.
# Brave Search returns fare pages, the same fail-closed gate runs against each
# domain it names, and the verdicts land beside the curated ones.
#
# Brave cannot supply fares. It returns titles, URLs and snippets, not
# structured quotes, so it is a source of *addresses to audit*, never a source
# of prices. The index still comes from the licensed API.
# --------------------------------------------------------------------------
BRAVE_API_KEY_ENV = "BRAVE_SEARCH_API_KEY"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_CACHE_DIR = DATA_DIR / "cache" / "brave"
# The free tier is one query per second and 2,000 a month. The floor is the
# published limit plus headroom; the cache is what keeps a re-run free.
BRAVE_MIN_SECONDS_BETWEEN_QUERIES = 1.2
BRAVE_CACHE_TTL_SECONDS = 7 * 24 * 3600
BRAVE_RESULTS_PER_QUERY = 20
BRAVE_COUNTRY = "IN"

# Stated as queries rather than as a domain list, for the same reason the
# basket is stated as a rule: nobody can accuse the result of being curated if
# the curation step is a search engine everyone can re-run.
DISCOVERY_QUERIES: tuple[str, ...] = (
    "book domestic flights india cheap fares",
    "flight ticket booking delhi to mumbai",
    "compare airfares india online travel agency",
    "indian airline official booking site",
    "lowest airfare search bangalore delhi",
)

# Domains that will appear in any flight-related search and are not fare
# sources. Excluded by name so the exclusion is auditable rather than a
# heuristic that quietly drops things.
DISCOVERY_EXCLUDED_DOMAINS: frozenset[str] = frozenset({
    "wikipedia.org", "en.wikipedia.org", "reddit.com", "quora.com",
    "youtube.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "linkedin.com", "pinterest.com", "medium.com", "blogspot.com",
    "amazon.in", "flipkart.com", "play.google.com", "apps.apple.com",
})

# A discovered host is only worth auditing if its URL looks like a fare page
# rather than a blog post about one. Substring match on the path, documented
# here so a reviewer can see exactly what was and was not audited.
DISCOVERY_PATH_HINTS: tuple[str, ...] = (
    "flight", "flights", "air", "booking", "book", "fare", "fares", "search",
)

# --------------------------------------------------------------------------
# 4c. BROWSER COLLECTION  (component 1: JS-rendered pages, sessions, proxies)
#
# Indian fare pages are single-page applications: the HTML a static client
# receives contains no prices at all. Rendering is therefore not an optimisation
# here, it is the difference between collecting fares and collecting nothing.
# The brief asks for Playwright/Selenium/Scrapy explicitly; we use Playwright.
# --------------------------------------------------------------------------
BROWSER_ENGINE = "chromium"
BROWSER_HEADLESS = os.environ.get("PUSHPAK_HEADFUL", "") == ""
BROWSER_NAV_TIMEOUT_MS = 45_000
BROWSER_SETTLE_TIMEOUT_MS = 25_000    # how long to wait for a fare grid to paint
BROWSER_LOCALE = "en-IN"
BROWSER_TIMEZONE = "Asia/Kolkata"
BROWSER_VIEWPORT = {"width": 1440, "height": 900}

# Session state (cookies + localStorage) is persisted per host and replayed on
# the next run. This is politeness, not evasion: reusing a session means we stop
# re-triggering the consent/onboarding flow on every single request, which cuts
# the number of requests a site has to serve us by roughly half.
SESSION_DIR = DATA_DIR / "cache" / "sessions"
SESSION_MAX_AGE_SECONDS = 12 * 3600

# Egress IP rotation. The brief asks for it; we implement it as an explicit,
# operator-supplied pool rather than a bundled proxy list, because rotating IPs
# to evade a block a site has already issued is exactly what the ethics section
# says we will not do. The supported use is the legitimate one: a statistical
# agency collecting from several regional egress points so that geo-targeted
# fares are observed as an Indian consumer in that region would see them.
#
#     export PUSHPAK_PROXIES="http://user:pass@a.example:8080,http://b.example:8080"
#
# Empty by default. Rotation is round-robin per host and recorded in the audit
# ledger, so which egress produced which quote is always reconstructable.
PROXY_POOL_ENV = "PUSHPAK_PROXIES"
PROXY_ROTATE_PER_REQUEST = False       # rotate per host per run, not per request

# Strings that mean "a human-verification wall was served instead of content".
# Detected, recorded as CAPTCHA_BLOCKED, and then we back off. Pushpak does not
# solve CAPTCHAs: a CAPTCHA is a site telling us to stop, and the correct
# response from a government pipeline is to stop and fall back to the licensed
# channel.
CAPTCHA_MARKERS: tuple[str, ...] = (
    "captcha", "recaptcha", "hcaptcha", "are you a human", "verify you are human",
    "unusual traffic", "px-captcha", "checking your browser",
)
BOT_WALL_MARKERS: tuple[str, ...] = (
    "access denied", "request blocked", "you have been blocked",
    "reference #18.", "akamai", "cloudflare ray id", "incapsula",
)

# --------------------------------------------------------------------------
# 5. SCRAPING ETIQUETTE  (component 1: ethical + auditable)
# --------------------------------------------------------------------------
USER_AGENT = (
    "Pushpak-Research-Bot/0.1 (MoSPI Airfare Price Index prototype; "
    "non-commercial statistical research; contact: diid-pushpak@example.gov.in)"
)
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 2
RESPECT_ROBOTS = True          # hard gate; see collect/robots.py. Do not flip.
DEFAULT_CRAWL_DELAY = 6.0      # used when robots.txt declares no Crawl-delay
MAX_REQUESTS_PER_SOURCE_PER_RUN = 40
COLLECT_PII = False            # asserted in tests; the schema has no PII fields


@dataclass
class Settings:
    """Runtime knobs, overridable from the CLI."""
    db_path: Path = field(default_factory=lambda: DB_PATH)
    audit_log_path: Path = field(default_factory=lambda: AUDIT_LOG_PATH)
    seed: int = 20260826

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.audit_log_path = Path(self.audit_log_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
