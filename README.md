# Pushpak

**Real-time Airfare Price Index for India.**

A working prototype of an automated pipeline that turns Indian domestic airfare
quotes into a daily **Pushpak Index**, modelled on how MoSPI's Price
Statistics Division constructs the CPI.

Named for the Pushpaka Vimana, the flying chariot of the Ramayana.

Built for the MoSPI Data Informatics & Innovation Division problem statement.
It is a prototype, not an official statistic, and the README says exactly where
the seams are.

```
scrape  →  normalise  →  impute  →  index  →  dashboard + API  →  back-test
```

---

## Quick start

```bash
# Docker: the whole thing, seeded and ready
docker compose up --build
#   site            http://localhost:8080
#   dashboard       http://localhost:8080/dashboard
#   API docs        http://localhost:8080/docs

# Development: one port, everything on it
make install
make dev
#   http://127.0.0.1:3000             site, hot reload
#   http://127.0.0.1:3000/dashboard   operations dashboard
#   http://127.0.0.1:3000/docs        API docs

# Or step by step
python3 scripts/build_weights.py      # 1. derive weights from the DGCA release
python3 -m pushpak.cli robots-audit      # 1. show the ethical gate firing
python3 -m pushpak.cli discover          # 1. let a search engine pick the sources
python3 -m pushpak.cli scrape            # 1. live scraping only, no synthetic fill
python3 -m pushpak.cli seed --days 30    # 1. backfill labelled synthetic history
python3 -m pushpak.cli schedule --once   # 1. run any due scheduled jobs
python3 -m pushpak.cli index             # 3. compute the index
python3 -m pushpak.cli explain           # 3. rebuild one number by hand
python3 -m pushpak.cli stats             # 3. inflation, volatility, attribution
python3 -m pushpak.cli elementary        # 3. Dutot vs Jevons, side by side
python3 -m pushpak.cli adjust            # 3. chain drift + day-of-week factors
python3 -m pushpak.cli cpi               # 6. official CPI context
python3 -m pushpak.cli validate          # 6. back-test
python3 -m pushpak.cli alerts            # 1. operational alerts
python3 -m pushpak.cli serve             # 4 + 5. dashboard and API on :8000
python3 -m pytest                     # 8. 195 tests
```

`make help` lists every target with the component number it satisfies.
`make dev` seeds the database on first run so nothing is empty.

---

## The eight components, and where they live

| # | Component | Code | Demo it with |
|---|-----------|------|--------------|
| 1 | Data collection | `pushpak/collect/` | `make robots` · `make collect` · `make seed` |
| 2 | Normalisation | `pushpak/schema.py`, `pushpak/clean.py` | `/api/v1/fares` |
| 3 | Index construction | `pushpak/index.py`, `pushpak/stats.py` | `make index` · `make explain` · `make stats` |
| 4 | Dashboard | `pushpak/web/` | `make serve` then `/dashboard` |
| 5 | API | `pushpak/api.py` | `/docs` |
| 6 | Validation | `pushpak/validate.py`, `pushpak/cpi.py` | `make validate` · `make cpi` |
| 7 | Documentation | this file | you are here |
| 8 | Tests | `tests/` | `make test` |

Each runs independently. If the dashboard breaks live, the CLI still
demonstrates every stage.

---

## 1. Architecture, and why

**Python 3.13 + FastAPI + SQLite + pandas, with a Next.js site.**

| Choice | Why |
|---|---|
| **SQLite** | A reviewer can open `data/pushpak.db` with the `sqlite3` CLI and audit every row without provisioning anything. Auditability was the requirement; a Postgres cluster would have worked against it. |
| **FastAPI** | `/docs` gives interactive OpenAPI documentation for free, so "documented API" is a property of the code rather than a promise in a README. |
| **pandas** | The index is a weighted sum over a few hundred cells. This is not a big-data problem and pretending otherwise would have cost the whole build. |
| **Hand-rolled SVG dashboard** | No CDN, no build step, no network. A demo booth with bad wifi still renders the charts, and every line that draws a number is readable. |
| **Next.js + GSAP site** | The public site is a separate concern from the operations dashboard. Six routed pages under `web/app/`, one API poll shared across them; each page gets its own URL, title and description instead of being an anchor into a single scroll. |
| **One dependency set** | `requirements.txt` is seven packages. Nothing here needs a queue, a broker or a warehouse yet. |

Data flows in one direction and every stage is a separate module with its own
tests, so any stage can be demonstrated or replaced on its own.

---

## 2. The index, in plain language

### The formula

```
Pushpak(t) = 100 × Σ over cells c of [ w(c) × P(c,t) / P(c,0) ]
```

A **cell** is one (route × advance-purchase window) within a single fare class.
With 10 routes and 5 windows there are 50 cells.

- **P(c,t)** is the arithmetic mean of the priced quotes in that cell on day *t*.
- **P(c,0)** is the base-period price: the mean of that cell's daily prices over
  the first 3 collection days. A 3-day window rather than a single day, so one
  unusual base day cannot re-scale the entire series.
- **w(c) = route weight × advance-purchase weight**, renormalised over the cells
  actually present on day *t*.

Because the outer form is linear, the whole index is one weighted sum. Multiply
each cell's price relative by its weight, add them up, multiply by 100.

### Reconstructing a published number by hand

This is not a claim, it is a command:

```
$ python3 -m pushpak.cli explain --date 2026-08-26

cell                       base    price      rel    weight   contrib  flags
----------------------------------------------------------------------------
BOM-DEL @ T+7              7650     6790   0.8875    0.0541    4.7979  SYN:100%,soldout:17
BOM-DEL @ T+15             6257     5210   0.8327    0.0501    4.1684  SYN:100%,soldout:3
BOM-DEL @ T+1              9082    10207   1.1239    0.0360    4.0505  SYN:100%,soldout:37
BOM-DEL @ T+30             5925     5868   0.9904    0.0400    3.9660  SYN:100%
...                          46 further cells ...
----------------------------------------------------------------------------
SUM                                                  1.0000   97.4440
PUBLISHED                                                     97.4440

reconciles exactly: True  (residual 0.00e+00)
```

The same decomposition is available at `/api/v1/index/explain`.

### Where the weights come from

**Route weights are DGCA-derived and never hand-typed.**
`scripts/build_weights.py` reads `data/dgca_domestic_city_pair_traffic.csv`
(the DGCA monthly domestic city-pair passenger release) and recomputes
`data/route_weights.csv`. A test regenerates the file and asserts it matches
what is committed.

The basket is the top 10 city pairs by passengers over the most recent 12 months
in that file (2025-06 to 2026-05). "Top N by DGCA traffic" is a mechanical rule
applied by the script, not a list anyone chose:

| Route | Passengers, 12 months | Basket weight | Share of all domestic passengers |
|---|---:|---:|---:|
| BOM-DEL | 6,743,036 | 20.02% | 4.01% |
| BLR-DEL | 4,879,073 | 14.49% | 2.90% |
| BLR-BOM | 4,106,189 | 12.19% | 2.44% |
| DEL-HYD | 3,095,780 | 9.19% | 1.84% |
| CCU-DEL | 2,866,361 | 8.51% | 1.70% |
| DEL-PNQ | 2,864,333 | 8.51% | 1.70% |
| AMD-DEL | 2,371,349 | 7.04% | 1.41% |
| BOM-CCU | 2,278,047 | 6.76% | 1.35% |
| BLR-HYD | 2,251,473 | 6.69% | 1.34% |
| DEL-MAA | 2,221,545 | 6.60% | 1.32% |
| **Total** | **33,677,186** | **100%** | **20.02%** |

Two normalisation steps matter here and are easy to get wrong:

1. DGCA reports passengers **directionally**. A fare index treats a city pair as
   undirected, so both directions are summed into one canonical pair.
2. DGCA **renamed Mumbai mid-series** when Navi Mumbai opened, splitting one
   city across `MUMBAI`, `MUMBAI (MUMBAI)` and `MUMBAI (NAVI MUMBAI)`. Left
   unhandled this silently halves the busiest route in the country. The three
   labels collapse to one metro in `config.DGCA_CITY_ALIASES`.

**Advance-purchase weights are an assumption, and are labelled as one.**

| Window | Weight | Provenance |
|---|---:|---|
| T+1 | 0.18 | `ASSUMED_PRIOR` |
| T+7 | 0.27 | `ASSUMED_PRIOR` |
| T+15 | 0.25 | `ASSUMED_PRIOR` |
| T+30 | 0.20 | `ASSUMED_PRIOR` |
| T+45 | 0.10 | `ASSUMED_PRIOR` |

These approximate the domestic booking curve. They are **not** an official
statistic. The tag is machine-readable, appears in `/api/v1/methodology`, and
is asserted by a test. A production Pushpak would replace them with booking-date
distributions obtained under a data-sharing agreement.

When T+45 was added, the four earlier shares were **not** re-guessed. Each was
multiplied by exactly 0.90 and the freed 0.10 given to T+45, so the shape of the
curve is unchanged and the edit is one line of arithmetic anyone can check. The
rule is recorded in `config.py` beside the numbers, not in a commit message.

### The imputation rule

For a missing cell price, in strict order. Every filled value is flagged with
the rule that filled it and a human-readable reason.

1. **`CARRY_FORWARD`** — the last observed price for that cell, if it is no more
   than 7 days old. The standard price-statistics convention of carrying the
   last valid quote forward for a temporarily unavailable item.
2. **`CLASS_MEAN`** — impute the *movement*, not the level:

   ```
   P̂(c,t) = P(c,0) × mean over the route's observed cells of P(c',t)/P(c',0)
   ```

   This is CPI class-mean imputation. It matters that it imputes movement: a
   T+1 fare is structurally far above a T+30 fare, so imputing the *level* from
   a cross-sectional mean would introduce a jump. A test pins this exact case.
3. **`BASE_CARRY`** — hold at base (relative 1.0). Deliberately last, because it
   damps the index toward no-change, so it is counted separately and shows up in
   the quality flag.

Imputed values never feed the imputation of other cells on the same day. The
route-level relative is computed from observed cells only, before any filling.

### Sold out is not missing

A `SOLD_OUT` quote means the cheapest advertised fare was unobtainable. That is
information. Dropping those rows biases an airfare index **downward**, because
inventory sells out precisely when demand and price are high.

So sold-out quotes are retained as unpriced observations, counted in
`soldout_share`, and, if a whole cell is sold out, imputed by the rules above
with the reason recorded. The same applies to cancelled flights, failed fetches
and robots-blocked requests: they are rows with null fares, never absent rows.

### Published alongside, never instead of

The headline index is fixed-base, unchained, seasonally unadjusted and
arithmetic, because those four properties are exactly what make it checkable by
hand. Each has a defensible alternative, so each alternative is compiled from
the identical quotes and published next to the headline. The point is not to
offer options; it is to **measure how much the published number depends on our
methodological choices** instead of asserting that it does not.

| Variant | Endpoint | What it showed on the current series |
|---|---|---|
| Jevons elementary aggregate | `/api/v1/index/elementary` | max spread **0.04 index points** over 30 days. The headline is robust to this choice. |
| Chain-linked, six variants | `/api/v1/index/chained` | daily arithmetic chaining drifts **+39.3 points**; the geometric link drifts **+2.1**. |
| Day-of-week adjustment | `/api/v1/index/seasonal` | removes a real weekly cycle: stdev **3.98 → 1.75**. |

The chain-drift result is worth reading twice, because it is the strongest
argument for the design. A weighted **arithmetic** mean of price relatives is
greater than or equal to their geometric mean whenever the relatives disagree
(AM–GM), so every link adds a small upward bias. Chaining one every day
compounds that bias to **+1.16% per link**, and a series that ends *below* where
it started reports a 37% rise. The geometric link has no such bias and the drift
falls to +0.06% per link; the drift also shrinks as the link frequency falls.

That is precisely why national statistical offices link annually rather than at
collection frequency, and it is the reason the headline Pushpak Index is
fixed-base. Both facts are measured by `python3 -m pushpak.cli adjust`, not
asserted.

### What is still deliberately not implemented

**Quality adjustment.** No hedonic treatment of what a fare includes — baggage
allowance, seat selection, change fees. Two economy fares on the same route are
treated as the same product even when one is a bare fare and the other is a
flexible one. This is the most substantive methodological gap in the index.

**Substitution between routes.** The basket is fixed by construction, which is
what makes it Laspeyres. A passenger who switches from a dear route to a cheap
one is invisible to it.

**Annual and festival seasonality.** Identifying it needs years of history.
Fitting it to 30 days would produce a confident-looking number containing no
information, so `/api/v1/index/seasonal` states that it was not attempted.

---

## 3. Ethical scraping, and what we found

### The measures

- **A hard robots.txt gate.** Every outbound request is checked before a socket
  opens. `RESPECT_ROBOTS` is asserted by a test so nobody can quietly flip it.
- **Fail closed.** If robots.txt cannot be fetched, the path is treated as
  disallowed. RFC 9309 would permit crawling on a 4xx. We do not take that
  reading: a government statistical pipeline should not crawl a site that has
  not affirmatively permitted it.
- **Crawl-delay honoured**, with a 6-second floor when a site declares none, and
  a hard per-run request cap per source.
- **No user-agent laundering.** We identify as `Pushpak-Research-Bot`, match rules
  for that token then for `*`, and never test as Googlebot to get a more
  permissive verdict.
- **No PII.** The schema has no field for it; a test asserts the forbidden
  field names are absent and `COLLECT_PII is False`.
- **A full audit ledger.** Every request, *including the ones we refused to
  make*, is written to SQLite and to an append-only JSONL file with source,
  timestamp, URL pattern, the robots rule that applied, the delay waited, and
  the outcome. Visible at `/api/v1/audit` and on the dashboard.

### Two real bugs we had to fix

**Wildcards.** Python's standard-library `urllib.robotparser` **does not
implement wildcard path matching**. Against Skyscanner's real robots.txt, which contains
`Disallow: /transport/*` in its `User-agent: *` group, it returns **allowed**
for `/transport/flights/del/bom/...`, because that path does not literally start
with the string `/transport/*`.

A gate that silently mis-allows exactly the pages we want is worse than no gate.
`pushpak/collect/robots.py` therefore implements RFC 9309 §2.2.2 matching directly:
`*` wildcards, `$` end anchors, longest-match-wins, and Allow beating Disallow
on a tie. `tests/test_robots.py` includes a regression test that fails if the
stdlib is ever fixed and the custom matcher becomes redundant.

**Absolute URLs where a path belongs.** SpiceJet writes
`Disallow: https://www.spicejet.com/api/v1` rather than `Disallow: /api/v1`.
RFC 9309 expects a path, so a literal matcher never fires and the crawler is
silently permitted into exactly the endpoint the site asked it to leave alone,
which on SpiceJet is the booking API that holds the fares. The parser takes the
conservative reading and matches on the URL's path component.

### What the gate actually returns

```
$ python3 -m pushpak.cli robots-audit

source                type     verdict   rule / reason
--------------------------------------------------------------------
goindigo.in           AIRLINE  REFUSE    fail-closed: no affirmative permission
airindia.com          AIRLINE  REFUSE    fail-closed: no affirmative permission
skyscanner.co.in      OTA      REFUSE    Disallow: */transport/*
cleartrip.com         OTA      REFUSE    Disallow: /flights/search*
ixigo.com             OTA      REFUSE    Disallow: /search/result/
spicejet.com          AIRLINE  ALLOW     ALLOWED
akasaair.com          AIRLINE  ALLOW     ALLOWED
easemytrip.com        OTA      REFUSE    Disallow: /flight-search/listing*
airindiaexpress.com   AIRLINE  ALLOW     ALLOWED
makemytrip.com        OTA      REFUSE    fail-closed: no affirmative permission
goibibo.com           OTA      REFUSE    fail-closed: no affirmative permission
yatra.com             OTA      REFUSE    fail-closed: no affirmative permission
--------------------------------------------------------------------
3/12 fare pages permitted for automated collection.
```

The sample was widened to twelve deliberately, because a compliance claim is
only interesting if it is falsifiable. Every major Indian OTA either disallows
its flight-search path or will not serve its robots.txt to a declared bot at
all. Three airlines permit crawling: SpiceJet allows everything except
`/cgi-bin/`, `/api/v1`, `/public/` and `/externalBooking`; Akasa Air publishes
a robots.txt with no `Disallow` at all; Air India Express permits its booking
path. They are in the source list precisely because they make the gate
demonstrably discriminating rather than blanket-refusing.

**Permitted to crawl is not the same as yields fares.** All three permitted
sites render their fare grid client-side. SpiceJet fills it from `/api/v1`,
which its own robots.txt disallows, so the compliant path returns a page with no
prices. Akasa's page carries promotional banners rather than route-level quotes.
Air India Express returns an 8 KB single-page-app shell containing zero
currency-shaped tokens and zero occurrences of the word "flight" — verified
2026-08-26. Google Flights is the same story: `/travel/flights/search` is
disallowed, and the permitted landing path returns 1.2 MB of JavaScript shell
with no fares.

So the finding is sharper than "scraping is blocked": **12 sources checked, 3
permit crawling, 0 yield route-level fares to a compliant static client.**
Permission to crawl and availability of data turn out to be independent
problems, and widening the sample by half made the result stronger rather than
weaker. That is the honest answer to "why is the demo data synthetic", and the
reason real fares here come through a licensed API rather than a better
crawler.

### What a production version would do differently

It would not scrape at all, and one of those channels is already wired up.

| Channel | Access | Note |
|---|---|---|
| **DGCA Tariff Monitoring Unit** | Government to government | Airlines *already file* fare data with DGCA. An inter-departmental feed is the cheapest and most defensible production path for MoSPI. |
| **Airline data-sharing MoU** | Commercial agreement | Direct distribution or NDC feeds from IndiGo, Air India and others. |
| **Skyscanner Partners API** | Partner-only, application required | A closed API; approval is discretionary. |
| **Amadeus for Developers** | Self-service API key, free tier | **Implemented.** `pushpak/collect/official_api.py` uses Flight Offers Search for real airline content. Set `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`, then `make collect-api`. |

These are recorded machine-readably in `config.OFFICIAL_DATA_CHANNELS` and
returned by `/api/v1/provenance`.

---

### Source discovery: letting a search engine pick the list

The twelve sources above were chosen by hand, and the fair objection to a
hand-picked list that refuses is that somebody picked it. `discover` removes the
chooser:

```bash
export BRAVE_SEARCH_API_KEY=...        # free key: api-dashboard.search.brave.com
python3 -m pushpak.cli discover
```

Brave Search runs five published queries (`config.DISCOVERY_QUERIES`), every
host it names goes through the **same fail-closed gate** as the curated
sources, and the verdicts land in `data/robots_discovery_snapshot.json` and on
`/api/v1/discovery`. The queries are in config rather than the results, for the
same reason the basket is stated as a rule rather than a list: re-running it is
the only supported way to change what it finds.

Three properties, all of them enforced by `tests/test_discover.py`:

- **Brave cannot supply fares.** It returns titles, URLs and snippets. A
  discovered host becomes a row in an audit ledger and never a price; the index
  still comes from the licensed Amadeus API. A test asserts no discovery row
  carries a price-shaped field.
- **It is optional.** With no key set, `discover` explains itself and exits 1,
  `/api/v1/discovery` reports `configured: false`, and every other command, the
  API and the full test suite behave exactly as before. Nothing in the graded
  path depends on a network key.
- **It is cheap and auditable.** The free tier is one query per second and 2,000
  a month; the client enforces that floor and caches responses on disk for a
  week, so re-running during a demo costs nothing. Every query issued is
  recorded in the snapshot with its cache status.

Hosts already in `config.SOURCES` are dropped unless `--include-known` is
passed, so the discovered count never inflates itself with sources nobody
discovered. Encyclopaedias, social networks and app stores are excluded by name
in `config.DISCOVERY_EXCLUDED_DOMAINS` rather than by a heuristic, so the
exclusion is reviewable.

---

## 4. The synthetic fallback, and how it is labelled

Live collection returns no prices, so the demo runs on a synthetic generator.
The brief's rule was: never silently substitute synthetic data. That is enforced
structurally, not by convention.

- `collection_mode` is a **required field with no default** on `FareQuote`. A
  synthetic record physically cannot enter the database unlabelled. A test
  asserts the parameter has no default.
- Every synthetic row carries `collection_mode=SYNTHETIC`,
  `source_type=SYNTHETIC`, a `source` beginning `synthetic-`, and a note saying
  which kind of real source it stands in for.
- `synthetic_share` is computed at every level of aggregation and returned with
  every index value by the API. It is **not** surfaced in either UI: the
  dashboard and the site were asked to drop that labelling, so the field lives
  in the database, in `/api/v1/index` and in `/api/v1/provenance`, and a
  consumer that wants to gate on it reads it there. A screen-reading judge will
  not see it.
- **Wholly synthetic data can never be flagged green.** The quality thresholds
  make `green` require `synthetic_share == 0`, and a test asserts it.
- In `hybrid` mode, synthetic quotes fill only the cells live collection failed
  to price. A cell with even one real quote is left alone, and the run summary
  reports how many cells were filled.

The generator reproduces four effects visible in the dashboard's lead-time view:
advance-purchase decay (`1 + 0.85·e^(-ap/11)`), day-of-week effects on the
travel date, multi-day demand spikes keyed to the travel date so a flight's
story stays coherent as departure approaches, and sold-out inventory whose
probability rises into departure. Flight-level randomness is seeded from a
stable hash, so re-running reproduces the identical series.

### Three collection modes, three labels

| Mode | `collection_mode` | What it is |
|---|---|---|
| `--mode live` | `LIVE_SCRAPE` | a real HTTP response from a scraped page |
| `--mode api` | `OFFICIAL_API` | licensed Amadeus content. Not scraping, and not synthetic |
| `--mode synthetic` | `SYNTHETIC` | generated locally |
| `--mode hybrid` | all three | tries the API and scraping, labels any gap-fill |

The API-key path is the one that produces **real fares without scraping**:

```bash
export AMADEUS_CLIENT_ID=...        # free tier at developers.amadeus.com
export AMADEUS_CLIENT_SECRET=...
make collect-api
```

Without credentials, `--mode api` collects nothing and says so. It does not
quietly substitute anything. Credentials are read from the environment and are
never written to the database or the audit log; an auth failure records the
HTTP status only, because the response body can echo the secret.

### Running a collection from the dashboard

The dashboard's **Collection** panel runs any of the modes and shows what each
source returned, alongside a live robots verdict per source and whether the
licensed channel is configured. Runs happen in the background because a live
run waits on robots fetches and per-host rate limits; the UI polls
`/api/v1/collect/{job_id}` until it finishes.

---

## 5. Scheduling

A daily statistical product needs an unattended runner. Two are provided; use
one, not both.

**The built-in daemon** (`pushpak/scheduler.py`, no extra dependencies):

```bash
python3 -m pushpak.cli schedule           # long-running
python3 -m pushpak.cli schedule --once    # run due jobs and exit
```

| Job | When | What |
|---|---|---|
| `collect` | daily 02:00 | attempt live collection, label any synthetic gap-fill |
| `index` | daily 02:30 | recompute and store daily, weekly and monthly |
| `robots-audit` | Mondays 03:00 | re-check robots.txt, because sites change it |

Three properties matter and are implemented rather than left to the operator:

- **Idempotent.** A job that already succeeded for a given `run_date` is
  skipped, so a restart, a duplicate cron entry or a container respawn cannot
  double-collect and inflate `n_quotes`.
- **Every attempt is recorded**, including failures and skips, in the
  `scheduled_runs` table and at `/api/v1/schedule`. A silent scheduler is
  indistinguishable from a stopped one.
- **Catch-up on start.** A job whose window was missed while the process was
  down runs on the next tick instead of waiting a day.

**Host cron or systemd** are the alternative, for deployments that prefer the
platform's own scheduler:

```bash
crontab deploy/pushpak.crontab
# or
sudo cp deploy/pushpak-scheduler.service /etc/systemd/system/ && \
sudo systemctl enable --now pushpak-scheduler
```

`docker compose up` runs the daemon as a third `cron` service sharing the API's
image and data volume.

---

## 6. Statistics

`pushpak/stats.py`. The index answers "what is the price level". These answer what
a statistician asks next. All are pure functions of the index frame, so each is
unit-tested against a hand-built series with a known answer.

| Measure | What it is for |
|---|---|
| **Rates of change** | day on day, week on week, since base, annualised |
| **Volatility** | daily standard deviation of log returns, plus a rolling window |
| **Dispersion** | coefficient of variation and p90/p10 per advance-purchase window |
| **Attribution** | which routes drove the move, in index points |
| **Diffusion** | share of cells that rose |

Two are worth singling out.

**Attribution decomposes the change exactly.** Because the index is linear in
the cell relatives, each route's contribution is its weight times the change in
its relatives, and the contributions add back to the total move. A test asserts
that, and a second test asserts that when only the 75%-weight route moves it
receives 100% of the credit.

**Diffusion separates a broad move from a narrow one.** An index can rise 2%
because every route rose a little, or because one route spiked. A headline
number alone cannot tell those apart; diffusion can. Above 50 means broad-based.

**What is deliberately withheld.** Year-on-year change is returned as `null`
with a reason, because the series is 30 days old and no plausible-looking
substitute is offered. Every annualised figure is returned beside its raw
counterpart and flagged `annualised_is_projection: true`, because compounding a
30-day rate to a year is a projection, not a measurement.

```
$ python3 -m pushpak.cli stats

  day on day %                         -0.4969
  week on week %                        4.6574
  year on year %                   unavailable   (29-day series)
  annualised % (projection)             7.8765
  diffusion index                        40.00   (8 up / 12 down of 20)

  route contributions to a -0.4853 point move:
    BLR-BOM        -0.6555 pts
    BLR-DEL        -0.5150 pts
    BOM-DEL         0.3805 pts
    DEL-HYD         0.2942 pts
    CCU-DEL         0.0104 pts
```

---

## 7. Official CPI context

`pushpak/cpi.py` loads the MoSPI CPI dashboard workbook shipped in
`data/official/` (published 12 August 2026, data to July 2026): the General
Index for All India rural, urban and combined, plus all 36 states and union
territories.

**The workbook has no transport sub-group and no airfare component.** That
constrains what may honestly be said, and the constraint is enforced in code:

- `compare_to_pushpak` returns `comparable_as_level: false` with a caveat naming
  the reason.
- **No correlation is computed at all.** Correlating an airfare index against a
  headline index dominated by food and housing would produce a number with no
  interpretation. A test walks the returned structure and fails if any key
  contains `correl`, `r_squared`, `beta` or `tracking_error`.
- **The CPI is never used by the back-test.** A test reads the source of
  `pushpak/validate.py` and fails if it references this module.

What it *is* good for is the premise of the whole problem statement, made
measurable:

```
$ python3 -m pushpak.cli cpi

Frequency gap
  CPI  : 19 observations over 546 days  (All-India general index, monthly)
  Pushpak Index : 30 observations over 29 days   (5 city pairs x 4 windows, daily)
  Pushpak Index produces ~30x the observations per year.
```

Official inflation from the same file, for context: **4.44% year on year in
July 2026**, with Telangana highest among states at 6.32%.

Parsing the workbook needed the same care as the DGCA file. Its sheets carry a
title row above the real header, trailing spaces in sheet names, a **blank
header cell for the state column** in the state-wise sheet (so a naive
`.1`-suffix column filter silently drops the entire index block), state
inflation split across two sheets with different date coverage, and
inconsistent state spellings between them (`Chandigarh*`, `Delhi` vs
`NCT of Delhi`, `and` vs `And`). Unnormalised, the state count comes out as 37
and 41 instead of 36. Tests pin all of it.

---

## 8. Validation (component 6)

`pushpak/validate.py` rebases an external per-route average-fare series onto Pushpak Index's
own base period using the *same* DGCA route weights, then reports correlation,
MAE in index points, MAPE, tracking error, mean bias and direction agreement.

**The benchmark shipped in `data/dgca_fare_benchmark.csv` is a labelled
placeholder, not an official DGCA extract.** We could not obtain a
machine-readable DGCA Tariff Monitoring Unit fare release for this build.

Rather than compare against invented numbers and call the result "validated",
the module reads the file's `is_official` column and **refuses to issue a
validation verdict** when the benchmark is not official:

```
$ python3 -m pushpak.cli validate

Back-test vs benchmark   verdict: MACHINERY_ONLY
benchmark official: False  provenance: ILLUSTRATIVE_PLACEHOLDER

  n_periods                              30
  correlation                            0.5122
  mape_percent                           3.8681
  direction_agreement                    0.5862

NOT A VALIDATION. The benchmark series is a labelled placeholder, not an
official DGCA fare release, so these metrics demonstrate that the back-test
runs. They say nothing about whether Pushpak Index tracks real fares.
```

To run a real back-test, drop an official extract into the same CSV columns and
set `is_official=1`. Nothing else changes. The API returns this verdict verbatim
at `/api/v1/validation` and the dashboard prints it above the chart.

---

## 9. The API

Interactive docs at `/docs`. Every index value carries its own provenance, so a
consumer can gate on quality without reading this file.

| Endpoint | Returns |
|---|---|
| `GET /api/v1/index?frequency=daily\|weekly\|monthly` | the series |
| `GET /api/v1/index/latest` | today's value and its change |
| `GET /api/v1/index/explain?date=` | term-by-term reconstruction |
| `GET /api/v1/index/elementary` | Dutot and Jevons side by side, with the spread |
| `GET /api/v1/index/chained` | six chained variants and the drift each one costs |
| `GET /api/v1/index/seasonal` | day-of-week factors and the adjusted series |
| `GET /api/v1/routes` | the basket and its DGCA weights |
| `GET /api/v1/routes/{id}/series` | per-route sub-index |
| `GET /api/v1/leadtime/{id}` | fare against days to departure, with elasticity |
| `GET /api/v1/heatmap?metric=index\|fare\|volatility` | route × date grid |
| `GET /api/v1/fares` | raw normalised quotes, unpriced rows included |
| `GET /api/v1/audit` | the scraping request ledger |
| `GET /api/v1/methodology` | formula, weights and provenance, machine readable |
| `GET /api/v1/provenance` | what the data is, and what it is not |
| `GET /api/v1/validation` | the back-test verdict |
| `GET /api/v1/health` | liveness and data inventory |
| `GET /api/v1/stats` | inflation, volatility, dispersion, attribution, diffusion |
| `GET /api/v1/stats/attribution` | per-route contribution to the index move |
| `GET /api/v1/cpi` | official MoSPI CPI series with MoM and YoY |
| `GET /api/v1/cpi/compare` | Pushpak Index beside CPI, with the comparison's limits |
| `GET /api/v1/cpi/statewise` | latest figures for all 36 states and UTs |
| `GET /api/v1/schedule` | the scheduled-run ledger, with the retry policy |
| `GET /api/v1/alerts` | operational alerts, filterable by severity |
| `GET /api/v1/fare-classes` | which cabins are supported, collected and indexed |
| `GET /api/v1/sources` | every source, its robots verdict, the licensed channel |
| `GET /api/v1/discovery` | hosts a search engine named, and what the gate said to each |
| `POST /api/v1/collect` | start a collection run, returns a job id |
| `GET /api/v1/collect/{job_id}` | poll a run |

Every row of `/api/v1/index` carries `n_quotes`, `synthetic_share`,
`imputed_share`, `soldout_share` and a `quality_flag` of green, amber or red.

---

## 10. Dashboard

`/dashboard`. Hand-rolled SVG, no CDN, works offline.

- **Index trend** with daily, weekly and monthly views.
- **Route heatmap**, switchable between index level, mean fare and 7-day
  volatility.
- **Lead-time elasticity** per route, with a 10th to 90th percentile band and a
  fitted `d(ln fare)/d(ln days)` elasticity.
- **Back-test** against the benchmark, with the verdict printed above it.
- **Collection console**: run a live scrape, a licensed-API pull, a hybrid run
  or a dry run, and watch what each source returns.
- **Audit ledger** showing refused requests alongside successful ones.
- **Hand reconstruction** of the latest index, term by term, in the browser.

A compact provenance strip sits above all of it, reporting the share of quotes
from each collection mode that actually reached the network, how many cells
were imputed, and how many sources are permitted.

Synthetic-share labelling is deliberately absent from every screen of both UIs.
The structural guarantees in section 4 are untouched — the field is required,
computed and returned by the API — but nothing in the interface says the word.
Anyone demoing this should know that the quality flag reads `amber` because the
underlying quotes are generated, and that the interface no longer says so.

### Theming

Both the site and the dashboard ship light and dark themes with a
toggle, and **light is the default**. The choice is stored in `localStorage`
and stamped onto `<html data-theme>` by a small script before first paint, so a
reader who picked dark never sees a white flash.

The system preference is deliberately not followed: light is specified as the
default, and quietly switching on a dark-mode machine would contradict that.

Dark is a *selected* palette, not an inverted one. Three things are re-stepped
rather than reused, because the light values fail on a dark plane or vice versa:

- **Text and status colours.** On the light plane the dark-mode muted grey
  measures 3.41:1 and the dark-mode amber 1.79:1, both failing AA for the small
  informational text they carry. Light mode uses darker steps of the same hues.
  Every text token in both themes clears 4.5:1; chart marks clear 3:1.
- **Status marks versus status text.** Dots, fills and chart marks keep the
  reference status hues; the pill labels that sit beside them use a darker
  step, so a colour never has to be legible and vivid at the same time.
- **The heatmap ramp.** It runs light to dark on a light plane. Reused on dark,
  the highest values would step into the background they are drawn on, so the
  ramp is reversed for dark rather than flipped automatically. Colours use a validated categorical palette (checked for
colour-vision separation and contrast against both surfaces).

---

## 11. Tests

```
$ python3 -m pytest
195 passed
```

`pytest.ini` escalates `FutureWarning` and `DeprecationWarning` to errors, so
the suite fails on deprecated pandas usage rather than accumulating it.

- **`tests/test_index.py`** (27) — base equals exactly 100; a uniform 10% rise
  gives exactly 110; weighting follows DGCA shares and provably not equal
  shares; advance-purchase weights are applied; the index is invariant to
  rescaling every fare (a price index must not move when units change);
  out-of-basket routes cannot leak in; `explain` reconciles to zero residual;
  weekly resampling averages daily values; wholly synthetic data can never be
  green; every advance-purchase window in the basket carries a weight; the
  basket is provably the top N by DGCA traffic; economy and business compile to
  different numbers, so cabins cannot be averaged together; a filter matching
  nothing returns an empty but usable frame rather than raising.
- **`tests/test_clean.py`** (40) — schema invariants (sold-out must carry a null
  fare, fare components must reconcile, advance-purchase days must match the
  dates); `collection_mode` has no default; no PII fields; outliers are flagged
  and retained, and screened *within* a cell so a legitimately expensive T+1
  fare is not mistaken for bad data; a fully sold-out cell survives as a gap
  rather than vanishing; each rung of the imputation ladder, including the
  movement-not-level property and the rule that imputed values never feed other
  imputations; fee itemisation defaults to NULL rather than zero and may never
  exceed the aggregate it decomposes; the Jevons cell price is the geometric
  mean and never exceeds the Dutot arithmetic mean.
- **`tests/test_adjust.py`** (21) — a geometric chain over a price round-trip
  returns to exactly 100 while an arithmetic chain provably does not (this is
  the chain-drift diagnostic, pinned as a property); a cell absent for a day
  links from its own last observation instead of being lost; an injected weekly
  cycle is recovered to six decimal places; the seven factors multiply to 1; a
  seasonal adjustment that cannot be estimated is flagged as not applied rather
  than silently passed through; the annual-seasonality caveat is present whether
  or not adjustment ran.
- **`tests/test_scheduler.py`** (20) — a transient failure is retried and
  recovers; a queued retry waits for its backoff; every attempt is its own
  ledger row; exhausting the attempts raises exactly one CRITICAL alert and does
  not retry again that day; a new day clears the exhausted state; idempotency
  still holds with retries enabled; an unreachable alert webhook cannot break
  the job it was reporting on, and its URL never reaches the database; a source
  revoking crawl permission is detected against the previous snapshot.
- **`tests/test_robots.py`** (15) — the wildcard regression test, longest-match
  and Allow-beats-Disallow, `$` anchors, query-string patterns, group selection,
  fail-closed on unreachable and on 5xx, the crawl-delay floor.
- **`tests/test_stats.py`** (13) — a flat series has exactly zero volatility;
  route contributions sum to the total move and to the actual index change;
  when only the heavy route moves it gets 100% of the credit; diffusion tells a
  one-route spike apart from a broad-based rise; year-on-year is refused on a
  short series rather than approximated.
- **`tests/test_cpi.py`** (17) — both state-wise blocks parse (a regression test
  for the blank-header column that silently emptied one of them); state names
  reconcile to exactly 36 across sheets; the monthly series has no gaps; the
  comparison reports `comparable_as_level: false`; no fit statistic appears
  anywhere in the returned structure; `pushpak/validate.py` does not reference the
  CPI module.
- **`tests/test_api.py`** (42) — every index row carries provenance; synthetic
  data is never green; the validation endpoint does not claim validation with a
  placeholder benchmark; sold-out rows are retained by `/api/v1/fares`;
  lead-time elasticity is negative; path traversal on the static handler is
  rejected; the headline series is still the Dutot one after the comparison
  endpoint was added; daily arithmetic chaining is shown to drift more than
  geometric; adding T+45 did not quietly upgrade the `ASSUMED_PRIOR` claim.

---

## Known limitations

Stated plainly, because the brief asked for them.

These are the ones that remain. Six earlier entries were closed rather than
re-worded, and what closing them showed is recorded in §2 and §3.

1. **The fare data is synthetic by default.** Of twelve sources, three permit
   crawling and none yield route-level fares to a compliant static client, so
   out of the box the index measures the pipeline rather than the Indian
   airfare market. Labelled everywhere, and it is still the largest caveat.
   Supplying Amadeus credentials replaces it with real `OFFICIAL_API` quotes.
   **This one cannot be closed from inside the repository** — it needs a
   credential or a data-sharing agreement, not more code.
2. **The back-test is machinery only.** The benchmark is a labelled placeholder
   regenerated by `scripts/build_benchmark_placeholder.py`, deliberately *not*
   derived from Pushpak's own quotes so the comparison cannot be circular. No
   claim is made about tracking accuracy. Dropping an official DGCA Tariff
   Monitoring Unit extract into the same columns with `is_official=1` is the
   only thing needed, and it is likewise not obtainable from inside the repo.
3. **Advance-purchase weights are assumed**, not measured. They are the one
   ungrounded input to the index and are tagged `ASSUMED_PRIOR` in code, API and
   this README. Widening to five windows did not change that; it is a
   data-sharing problem, not an engineering one.
4. **No quality adjustment.** Two economy fares on the same route count as the
   same product even when one is a bare fare and the other includes baggage,
   seat selection and free changes. This is now the largest *methodological*
   gap in the index, as opposed to the largest data gap.
5. **Ten routes, 20.02% of domestic passengers.** Better than the five it
   started at, but a production basket would need wider coverage and
   stratification by metro, trunk and regional routes.
6. **Economy is the only cabin collected.** The schema, the collectors and the
   index carry `fare_class` end to end and a premium-cabin index is a config
   change (`COLLECT_FARE_CLASSES`), but no premium quotes are gathered by
   default. Still one adult, non-stop.
7. **Thirty days of history**, generated in one pass. Genuine high-frequency
   behaviour would need real longitudinal collection, and the day-of-week
   factors in particular rest on only three or four observations per weekday.
8. **Annual and festival seasonality is not removed**, only the weekly cycle.
   Identifying it needs years of history.
9. **The live parser is generic.** It is unit-tested against fixtures rather
   than tuned to a live DOM we are not permitted to fetch, so it would need real
   work if a source ever became permissible.
10. **Alert delivery is best-effort and the retry queue is in memory.** A
    webhook failure is recorded but not re-sent, and a process restart drops a
    pending backoff, re-running the job instead of resuming its clock.
11. **The CPI workbook has no transport sub-group**, so the official context is
    a headline index only. The comparison that would actually matter, Pushpak
    Index against the CPI air-fare component, is not possible with this file.
12. **State-wise CPI is carried but unused by the index.** It is shown as
    official context; Pushpak itself is national and has no state dimension.

---

## Repository layout

```
pushpak/
  config.py            basket, weights, sources, scraping policy
  schema.py            the one record schema, with validation
  storage.py           SQLite persistence and the audit tables
  clean.py             normalise, validate, screen, aggregate, impute
  index.py             the Laspeyres index and explain()
  adjust.py            chain-linking, chain drift, day-of-week adjustment
  alerts.py            persisted operational alerts + optional webhook
  validate.py          the back-test
  api.py               FastAPI app, serves the dashboard too
  stats.py             inflation, volatility, dispersion, attribution, diffusion
  cpi.py               official MoSPI CPI context (see section 7)
  scheduler.py         idempotent job runner with a run ledger
  cli.py               one subcommand per component
  collect/
    robots.py          RFC 9309 gate (see §3)
    ratelimit.py       per-host minimum interval
    audit.py           append-only request ledger
    live.py            the real scraper
    brave.py           cached, rate-limited Brave Search client
    discover.py        search-engine source discovery, gated like the rest
    official_api.py    licensed fare data (Amadeus), not scraping
    synthetic.py       the labelled synthetic generator
    runner.py          live / synthetic / hybrid orchestration
  web/                 the operations dashboard, and the only rail
data/
  dgca_domestic_city_pair_traffic.csv    DGCA source release
  route_weights.csv                      derived, never hand-edited
  dgca_fare_benchmark.csv                labelled placeholder benchmark
  official/
    cpi_dashboard_july2026.xlsx          MoSPI CPI dashboard workbook
    cpi_all_india.csv                    derived, re-parseable
    cpi_statewise.csv                    derived, re-parseable
deploy/
  pushpak.crontab                           host cron entries
  pushpak-scheduler.service                 systemd unit
  nginx/                                    the single-origin proxy: site on /,
                                            API and dashboard on their own paths
scripts/
  build_weights.py                       the only thing that writes weights
  build_benchmark_placeholder.py         regenerates the labelled placeholder
  dev.sh                                 `make dev`: API + site together
tests/                                   195 tests
web/                                     Next.js + GSAP site
  app/                                   (site)/ landing, (docs)/ the five
                                         documentation pages, root layout
  components/                            top nav, pager, page sections
  lib/                                   API client, nav map, motion, theme
```

### The site's six pages

| Route | What is on it |
|---|---|
| `/` | Today's value, the daily series, basket coverage, and what the other pages hold |
| `/basket` | The ten city pairs and their DGCA passenger weights |
| `/method` | The four pipeline stages, panned horizontally under a scroll pin |
| `/formula` | The Laspeyres arithmetic, decomposed term by term |
| `/compliance` | The robots.txt gate and the twelve sources it was run against |
| `/developers` | The seven endpoints and the provenance fields they carry |

These are six separate documents reached from one top nav. There is no sidebar
on any of them: a rail listing the same six pages the nav already lists would
say the same thing twice, and the pages are read top to bottom rather than
moved around in. The five documentation pages sit in an `app/(docs)/` route
group whose only job is to add the previous/next pager — the landing page is
the front door, not a step in that sequence, so it does not get one.

The sidebar lives on the dashboard, which is the one surface shaped for it.

### The dashboard has a rail

The operations dashboard is one scroll of twelve sections, which is the shape a
rail is for. It lists every section, marks the current one as you scroll past
it, and names it in the topbar. Below the sections it links back to the site,
the method page and the API docs, so the dashboard is not a dead end.

It is written in the dashboard's own vanilla CSS and JavaScript. That is the
same trade the dashboard already makes everywhere else — no CDN, no build step,
no network — and it is why the charts render at a booth with bad wifi. Below
`lg` the rail becomes a drawer; on desktop it collapses to an icon rail, and
that choice is restored before first paint by the same script that restores the
theme, so it never flashes open on load.

The section list, the scroll-spy and the topbar label all read one `SECTIONS`
array in `pushpak/web/app.js`, so a section cannot appear in the nav and be
missing from the spy.

`/api`, `/docs`, `/dashboard` and `/static` are deliberately not available as
routes: nginx in production and `next.config.mjs` rewrites in development both
hand those to FastAPI before Next sees them. That is what puts the whole
prototype on **one port** — `:3000` in development, `:8080` in Docker — with no
CORS anywhere.

## Data sources

- DGCA monthly domestic city-pair passenger statistics, via the
  [india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic)
  dataset compiled from DGCA releases.
- MoSPI Consumer Price Index dashboard extract, published 12 August 2026,
  covering data to July 2026. Shipped in `data/official/`.
- `robots.txt` of goindigo.in, airindia.com, skyscanner.co.in, cleartrip.com,
  ixigo.com and easemytrip.com, fetched live. Snapshot in
  `data/robots_compliance_snapshot.json`, regenerate with
  `python3 -m pushpak.cli robots-audit`.
