"""Component 1 — collection orchestration.

Three modes, and the difference between them is the whole point:

  ``live``      Attempt real scraping only. If sources are robots-blocked the
                run yields zero priced quotes. That is a valid, honest outcome.
  ``api``       Licensed API only. Real fares, no scraping. Requires
                credentials; without them the run yields nothing rather than
                quietly substituting something else.
  ``synthetic`` Generate labelled synthetic quotes only.
  ``hybrid``    Attempt live first; fill the cells live collection could not
                price with synthetic quotes. **The synthetic rows are still
                labelled SYNTHETIC** — hybrid never launders synthetic data as
                live. The run summary reports exactly how many of each.

The brief's rule — "never silently substitute synthetic data for real data" —
is enforced structurally: the substitution decision lives here, in one function,
and the label travels with the row from :mod:`pushpak.schema`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from .. import config, storage
from ..schema import CollectionMode, FareQuote, PRICED_STATUSES, QuoteStatus
from .audit import AuditLog
from .live import LiveCollector
from .ratelimit import RateLimiter
from .robots import RobotsGate
from .synthetic import generate_quotes


@dataclass
class RunSummary:
    run_id: str
    mode: str
    observation_dates: list[str] = field(default_factory=list)
    total_quotes: int = 0
    live_quotes: int = 0
    live_priced: int = 0
    api_quotes: int = 0
    api_priced: int = 0
    synthetic_quotes: int = 0
    blocked_by_robots: int = 0
    fetch_failed: int = 0
    parse_failed: int = 0
    audit_requests: int = 0
    cells_filled_synthetically: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["synthetic_share"] = (
            self.synthetic_quotes / self.total_quotes if self.total_quotes else 0.0
        )
        return d


def _cell_key(q: FareQuote) -> tuple[str, str, int]:
    return (q.observation_date, q.route_id, q.advance_purchase_days)


def run_collection(
    mode: str = "hybrid",
    observation_date: Optional[date] = None,
    days: int = 1,
    routes: Optional[list[str]] = None,
    ap_windows: tuple[int, ...] = config.ADVANCE_PURCHASE_WINDOWS,
    db_url=None,
    seed: int = 20260826,
    persist: bool = True,
    dry_run: bool = False,
) -> tuple[list[FareQuote], RunSummary]:
    """Collect quotes for ``days`` observation dates ending at ``observation_date``."""
    if mode not in ("live", "api", "synthetic", "hybrid"):
        raise ValueError(f"unknown mode {mode!r}")

    from ..index import load_route_weights
    routes = routes or list(load_route_weights()["route_id"])
    observation_date = observation_date or date.today()
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    storage.init_db(db_url)
    storage.start_run(run_id, mode, {
        "days": days, "routes": routes, "ap_windows": list(ap_windows),
        "end_date": observation_date.isoformat(), "seed": seed, "dry_run": dry_run,
    }, db_url)

    audit = AuditLog(run_id, db_url=db_url)
    summary = RunSummary(run_id=run_id, mode=mode)
    all_quotes: list[FareQuote] = []

    obs_dates = [observation_date - timedelta(days=i) for i in range(days - 1, -1, -1)]
    summary.observation_dates = [d.isoformat() for d in obs_dates]

    for obs in obs_dates:
        day_quotes: list[FareQuote] = []

        # ---- live -------------------------------------------------------
        if mode in ("live", "hybrid"):
            gate = RobotsGate()
            limiter = RateLimiter()
            with LiveCollector(audit, gate, limiter, dry_run=dry_run) as lc:
                live_q = list(lc.collect(obs, routes, ap_windows))
            day_quotes.extend(live_q)
            summary.live_quotes += len(live_q)
            summary.live_priced += sum(
                1 for q in live_q if q.quote_status in PRICED_STATUSES
            )
            summary.blocked_by_robots += sum(
                1 for q in live_q if q.quote_status is QuoteStatus.BLOCKED_BY_ROBOTS
            )
            summary.fetch_failed += sum(
                1 for q in live_q if q.quote_status is QuoteStatus.FETCH_FAILED
            )
            summary.parse_failed += sum(
                1 for q in live_q if q.quote_status is QuoteStatus.PARSE_FAILED
            )

        # ---- licensed API ------------------------------------------------
        if mode in ("api", "hybrid"):
            from .official_api import OfficialApiCollector
            with OfficialApiCollector(audit) as ac:
                if ac.configured:
                    api_q = list(ac.collect(obs, routes, ap_windows))
                    day_quotes.extend(api_q)
                    summary.api_quotes += len(api_q)
                    summary.api_priced += sum(
                        1 for q in api_q if q.quote_status in PRICED_STATUSES
                    )
                    if api_q:
                        summary.notes.append(
                            f"{obs.isoformat()}: {len(api_q)} quotes from the "
                            f"licensed API, labelled OFFICIAL_API"
                        )
                elif mode == "api":
                    summary.notes.append(
                        "Licensed API token not set (TRAVELPAYOUTS_TOKEN). "
                        "No quotes collected; nothing was substituted."
                    )

        # ---- synthetic --------------------------------------------------
        if mode == "synthetic":
            syn = list(generate_quotes(obs, routes, ap_windows, seed=seed))
            day_quotes.extend(syn)
            summary.synthetic_quotes += len(syn)

        elif mode == "hybrid":
            # Only fill cells that live collection failed to price. A cell that
            # got even one real quote is left alone.
            priced_cells = {
                _cell_key(q) for q in day_quotes
                if q.quote_status in PRICED_STATUSES
            }
            wanted = {(obs.isoformat(), r, a) for r in routes for a in ap_windows}
            gaps = wanted - priced_cells
            if gaps:
                syn = [
                    q for q in generate_quotes(obs, routes, ap_windows, seed=seed)
                    if _cell_key(q) in gaps
                ]
                day_quotes.extend(syn)
                summary.synthetic_quotes += len(syn)
                summary.cells_filled_synthetically += len(gaps)
                summary.notes.append(
                    f"{obs.isoformat()}: {len(gaps)}/{len(wanted)} cells had no live "
                    f"price; filled with SYNTHETIC-labelled quotes"
                )

        all_quotes.extend(day_quotes)

    summary.total_quotes = len(all_quotes)
    summary.audit_requests = len(audit._buffer)

    if persist:
        audit.flush()
        storage.upsert_quotes(all_quotes, db_url)
    storage.finish_run(run_id, summary.as_dict(), db_url)
    return all_quotes, summary
