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
# Postgres connection string. Everything else in this file is a path; this one
# is a DSN, so it is named accordingly.
DB_URL = os.environ.get(
    "PUSHPAK_DB_URL", "postgresql://pushpak:pushpak@localhost:5432/pushpak"
)
AUDIT_LOG_PATH = Path(os.environ.get("PUSHPAK_AUDIT_LOG", DATA_DIR / "scrape_audit.jsonl"))
DGCA_TRAFFIC_CSV = DATA_DIR / "dgca_domestic_city_pair_traffic.csv"
ROUTE_WEIGHTS_CSV = DATA_DIR / "route_weights.csv"
DGCA_FARE_BENCHMARK_CSV = DATA_DIR / "dgca_fare_benchmark.csv"

# --------------------------------------------------------------------------
# 1. THE BASKET  (component 1: route selection)
# --------------------------------------------------------------------------
# Chosen as the top-N domestic city pairs by passengers carried over the most
# recent 12 months in the DGCA release shipped in data/.  At N=15 they carry
# ~26.2% of ALL Indian domestic passengers.  Weights are recomputed from that
# file by scripts/build_weights.py — they are never hand-typed.

IATA = {
    "DELHI": "DEL",
    "MUMBAI": "BOM",
    "BENGALURU": "BLR",
    "HYDERABAD": "HYD",
    "KOLKATA": "CCU",
    "CHENNAI": "MAA",
    "PUNE": "PNQ",
    "AHMEDABAD": "AMD",
    "GOA": "GOI",        # metro code; DGCA_CITY_ALIASES collapses Dabolim + Mopa
    "SRINAGAR": "SXR",
    "GUWAHATI": "GAU",
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

# Top-15 covers 26.2% of all Indian domestic passengers; top-5 covered 12.9%.
# build_weights.py fails loudly on any city missing from IATA above, so raising
# this cannot silently drop a route.
BASKET_SIZE = 15
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
# The brief's T+1 / T+7 / T+15 / T+30. Each (route, AP window) pair is one
# "elementary aggregate" cell in CPI terminology.
ADVANCE_PURCHASE_WINDOWS: tuple[int, ...] = (1, 7, 15, 30)

# Within-route weights across AP windows. These approximate the domestic
# booking curve (most Indian domestic leisure+business booking is short-lead).
#
#   *** PROVENANCE: ASSUMED_PRIOR — this is NOT an official statistic. ***
#
# A production Pushpak would source these from airline/OTA booking-date
# distributions obtained under a data-sharing MoU (see README §Ethics).
# They are kept here, versioned and normalised, precisely so a reviewer can
# see exactly which part of the index rests on an assumption.
AP_WINDOW_WEIGHTS: dict[int, float] = {
    1: 0.20,   # walk-up / last-minute, disproportionately business travel
    7: 0.30,
    15: 0.28,
    30: 0.22,
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
# Some sources publish one SEO page per city pair addressed by full city name
# rather than IATA code, so a pattern can ask for {origin_slug}/{dest_slug}
# instead of {origin}/{dest}. Verified against Akasa's sitemap on 2026-08-27:
# they use "new-delhi", not "delhi", for DEL.
CITY_SLUGS: dict[str, str] = {
    iata: city.lower().replace(" ", "-") for city, iata in IATA.items()
}
CITY_SLUGS["DEL"] = "new-delhi"


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
        # /book-flight-tickets?origin=...  404s — that path does not exist, and
        # the sitemap_index.xml robots.txt advertises under it 502s. The real
        # shape, verified 2026-08-27, is one page per city pair by city name.
        # Note it carries NO travel date: every advance-purchase window for a
        # route resolves to the same URL, so a run re-requests it once per
        # window. The per-source budget caps that; a date-aware collector would
        # have to drive the search form at /searchpanel instead.
        search_url_pattern="https://www.akasaair.com/flight-booking/{origin_slug}-to-{dest_slug}",
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
        "verified_on": "2026-08-27",
        "note": (
            "robots.txt permits the public search page, but fares are rendered "
            "client-side from /api/v1, which robots.txt disallows. Compliant "
            "static collection therefore yields no prices — the search URL "
            "returns HTTP 200 and 33 KB whose entire visible text is 'Please "
            "enable Javascript to view this site'. Note this rules out a "
            "headless browser too, not just a static client: rendering the page "
            "makes the browser fetch the disallowed /api/v1 itself. Unlike "
            "akasaair.com, the blocker here is permission, not engineering."
        ),
    },
    "akasaair.com": {
        "channel": "Airline distribution feed or MoU",
        "url": "https://www.akasaair.com",
        "access": "CRAWLING_PERMITTED_BUT_NO_STATIC_FARES",
        "verified_on": "2026-08-27",
        "note": (
            "robots.txt contains no Disallow at all — every path is permitted. "
            "The route page returns HTTP 200 and 353 KB, but carries no fare: "
            "no rupee amount appears anywhere in the served document, and the "
            "190 KB __NEXT_DATA__ island holds CMS copy (story, navFooter), not "
            "prices. The URL also has no travel-date parameter, so this page "
            "shape cannot express T+1/T+7/T+15/T+30 at all. Reaching a priced "
            "result means driving the booking form through /searchpanel. "
            "Sitemap lists 148 city-pair pages, covering 9 of the 15 basket "
            "routes (69.2% of basket weight)."
        ),
    },
    "amadeus": {
        "channel": "Amadeus for Developers, Flight Offers Search",
        "url": "https://developers.amadeus.com",
        "access": "WITHDRAWN_BY_VENDOR",
        "retired_on": "2026-07-17",
        "note": (
            "Pushpak originally collected licensed fares here. Amadeus "
            "decommissioned the self-service portal on 17 July 2026: "
            "api.amadeus.com and test.api.amadeus.com no longer resolve in "
            "DNS. Amadeus Enterprise is unaffected but requires a commercial "
            "contract. Kept in this registry rather than deleted, because it "
            "is the evidence for the entry below."
        ),
    },
    "travelpayouts": {
        "channel": "Travelpayouts / Aviasales Flight Data API (prices/calendar)",
        "url": "https://www.travelpayouts.com/developers/api",
        "access": "SELF_SERVICE_API_KEY",
        "note": (
            "Current interim channel for real fares without scraping. Set "
            "TRAVELPAYOUTS_TOKEN. Serves cached cheapest-fare observations "
            "aggregated from Aviasales user searches, not live shopping "
            "quotes: one observation per route-date rather than a "
            "distribution. Labelled OFFICIAL_API with that caveat in the "
            "quote's own notes field."
        ),
    },
    "_vendor_risk": {
        "channel": "Commercial self-service APIs are not a durable base for a "
                   "national statistic",
        "url": "https://www.phocuswire.com/amadeus-shut-down-self-service-apis-portal-developers",
        "access": "FINDING",
        "note": (
            "Observed during this build, not hypothesised: Amadeus withdrew "
            "self-service on 17 July 2026; Kiwi.com Tequila closed self-serve "
            "signups in 2026; Skyscanner Partners is discretionary. A vendor "
            "can withdraw access at will, so an official index should rest on "
            "the government-to-government channel below and treat commercial "
            "APIs as replaceable."
        ),
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

FARE_CLASSES = ("ECONOMY",)

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
    db_url: str = field(default_factory=lambda: DB_URL)
    audit_log_path: Path = field(default_factory=lambda: AUDIT_LOG_PATH)
    seed: int = 20260826

    def __post_init__(self) -> None:
        self.audit_log_path = Path(self.audit_log_path)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
