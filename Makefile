# Pushpak Index — one command per demoable component.

# Prefer the project venv when there is one, so `make demo` works without
# activating it first. psycopg lives there, not in the system python.
PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# Every target talks to Postgres through this. An exported PUSHPAK_DB_URL from
# your shell wins; otherwise the demo cluster below is the default.
PGDATA_DIR ?= $(HOME)/.pushpak-pg
PGPORT     ?= 5433
PUSHPAK_DB_URL ?= postgresql://pushpak@127.0.0.1:$(PGPORT)/pushpak
export PUSHPAK_DB_URL

.PHONY: help install dev db db-stop web-build weights robots scrape collect probe-api seed index explain stats cpi validate schedule serve test demo docker docker-down clean

help:      ## show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install:   ## install python and node dependencies
	$(PY) -m pip install -r requirements.txt
	cd web && npm install

dev:       ## run API (reload) + landing dev server together
	$(MAKE) db && ./scripts/dev.sh

# ---- the database ------------------------------------------------------
db:        ## ensure PUSHPAK_DB_URL is reachable, starting a local Postgres if not
	@if $(PY) -c "from pushpak import storage; storage.init_db()" 2>/dev/null; then \
	  echo "==> database ready: $$PUSHPAK_DB_URL"; \
	else \
	  echo "==> no database at $$PUSHPAK_DB_URL — starting one in $(PGDATA_DIR)"; \
	  [ -d "$(PGDATA_DIR)" ] || initdb -D "$(PGDATA_DIR)" -U pushpak --auth=trust >/dev/null; \
	  pg_ctl -D "$(PGDATA_DIR)" -o "-p $(PGPORT) -k /tmp" -l "$(PGDATA_DIR)/server.log" start >/dev/null 2>&1 || true; \
	  createdb -h 127.0.0.1 -p $(PGPORT) -U pushpak pushpak 2>/dev/null || true; \
	  $(PY) -c "from pushpak import storage; storage.init_db()"; \
	  echo "==> database ready: $$PUSHPAK_DB_URL"; \
	fi

db-stop:   ## stop the local Postgres this Makefile started
	@pg_ctl -D "$(PGDATA_DIR)" stop -m fast || true

# ---- the web tier ------------------------------------------------------
web-build: ## build the React landing page into web/dist (served at :8000/)
	@if [ -d web/dist ]; then \
	  echo "==> landing page already built (web/dist)"; \
	elif command -v npm >/dev/null 2>&1; then \
	  echo "==> building the landing page"; \
	  cd web && npm install --silent && npm run build; \
	else \
	  echo "==> npm not found — skipping; / will serve the dashboard instead"; \
	fi

# ---- component 1: collection -------------------------------------------
weights:   ## [1] re-derive route weights from the DGCA release
	$(PY) scripts/build_weights.py

robots:    ## [1] show the robots.txt gate verdict for every source
	$(PY) -m pushpak.cli robots-audit

scrape:    ## [1] live scraping only, no synthetic fill
	$(PY) -m pushpak.cli scrape

scrape-dry:## [1] check robots and log intent, issue no requests
	$(PY) -m pushpak.cli scrape --dry-run

collect-api: ## [1] licensed API collection (needs TRAVELPAYOUTS_TOKEN)
	$(PY) -m pushpak.cli collect --mode api

probe-api: ## [1] measure what the licensed API actually covers, store nothing
	$(PY) -m pushpak.cli collect --mode api --probe

sources:   ## [1] every source, its robots verdict, the licensed channel
	@curl -s http://127.0.0.1:8000/api/v1/sources | $(PY) -m json.tool || \
	  echo "start the API first: make serve"

collect:   ## [1] hybrid collection: live first, synthetic fills the gaps
	$(PY) -m pushpak.cli collect --mode hybrid

seed:      ## [1] backfill 30 days of labelled synthetic quotes
	$(PY) -m pushpak.cli seed --days 30

schedule:  ## [1] run the scheduler daemon (collection + indexing)
	$(PY) -m pushpak.cli schedule

schedule-once: ## [1] run any due scheduled jobs once and exit
	$(PY) -m pushpak.cli schedule --once --force

# ---- component 3: index and statistics ---------------------------------
index:     ## [3] compute and store the index
	$(PY) -m pushpak.cli index

explain:   ## [3] reconstruct the latest index number by hand
	$(PY) -m pushpak.cli explain

stats:     ## [3] inflation, volatility, dispersion, attribution, diffusion
	$(PY) -m pushpak.cli stats

# ---- component 6: validation and official context ----------------------
cpi:       ## [6] official MoSPI CPI context and the frequency gap
	$(PY) -m pushpak.cli cpi

validate:  ## [6] back-test against the benchmark
	$(PY) -m pushpak.cli validate

# ---- components 4, 5, 8 ------------------------------------------------
serve:     ## [4][5] landing page + dashboard + API, all on :8000
	$(PY) -m pushpak.cli serve

test:      ## [8] run the test suite
	$(PY) -m pytest

# ---- convenience -------------------------------------------------------
demo:      ## THE one command: database, full end-to-end slice, then serve
	$(MAKE) db && $(MAKE) web-build && $(MAKE) weights && $(MAKE) robots && $(MAKE) seed && $(MAKE) index \
	  && $(MAKE) explain && $(MAKE) stats && $(MAKE) cpi && $(MAKE) validate && $(MAKE) serve

docker:    ## build and run the whole stack (api + scheduler + web)
	docker compose up --build

docker-down: ## stop the stack
	docker compose down

clean:     ## remove generated data (keeps source releases)
	$(PY) -c "from pushpak import storage; storage.drop_all()"
	rm -f data/scrape_audit.jsonl
