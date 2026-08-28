# Pushpak Index — one command per demoable component.
PY ?= python3

.PHONY: help install dev weights robots discover scrape collect seed index explain stats cpi validate schedule serve test demo docker docker-down clean

help:      ## show this help
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install:   ## install python and node dependencies
	$(PY) -m pip install -r requirements.txt
	cd web && npm install

dev:       ## run API (reload) + the Next.js dev server together
	./scripts/dev.sh

# ---- component 1: collection -------------------------------------------
weights:   ## [1] re-derive route weights from the DGCA release
	$(PY) scripts/build_weights.py

robots:    ## [1] show the robots.txt gate verdict for every source
	$(PY) -m pushpak.cli robots-audit

discover:  ## [1] let Brave Search pick fare sources, then gate every one
	$(PY) -m pushpak.cli discover

scrape:    ## [1] live scraping only, no synthetic fill
	$(PY) -m pushpak.cli scrape

scrape-dry:## [1] check robots and log intent, issue no requests
	$(PY) -m pushpak.cli scrape --dry-run

collect-api: ## [1] licensed API collection (needs AMADEUS_CLIENT_ID/SECRET)
	$(PY) -m pushpak.cli collect --mode api

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
serve:     ## [4][5] run the dashboard + API on :8000
	$(PY) -m pushpak.cli serve

test:      ## [8] run the test suite
	$(PY) -m pytest

# ---- convenience -------------------------------------------------------
demo:      ## full end-to-end slice, then serve
	$(MAKE) weights && $(MAKE) robots && $(MAKE) seed && $(MAKE) index \
	  && $(MAKE) explain && $(MAKE) stats && $(MAKE) cpi && $(MAKE) validate && $(MAKE) serve

docker:    ## build and run the whole stack (api + scheduler + web)
	docker compose up --build

docker-down: ## stop the stack
	docker compose down

clean:     ## remove generated data (keeps source releases)
	rm -f data/pushpak.db data/pushpak.db-wal data/pushpak.db-shm data/scrape_audit.jsonl
