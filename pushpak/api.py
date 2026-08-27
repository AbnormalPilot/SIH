"""Components 4 & 5 — the public API and the dashboard it serves.

Designed for a downstream consumer such as NSO or RBI polling on a schedule.
Three properties matter more than breadth of endpoints:

  * **Provenance travels with every number.** No index value is returned
    without ``synthetic_share``, ``imputed_share``, ``soldout_share`` and a
    ``quality_flag``. A consumer can gate on those fields without reading docs.
  * **The methodology is an endpoint**, not a PDF: ``/api/v1/methodology``
    returns the formula, the weights and their provenance;
    ``/api/v1/index/explain`` reconstructs any published number term by term.
  * **Nothing is presented as more validated than it is.**
    ``/api/v1/validation`` returns the back-test verdict verbatim, including
    ``MACHINERY_ONLY`` when the benchmark is a placeholder.

Interactive OpenAPI docs are at /docs, the schema at /openapi.json.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, storage
from .index import (
    IndexResult, ap_weights, compute_index, explain, load_route_weights,
)
from .schema import SCHEMA_VERSION, COLUMNS, CollectionMode

#: Built from the enum so a new collection mode cannot be silently
#: unfilterable. A hard-coded alternation drifted once already.
_COLLECTION_MODE_PATTERN = "^(" + "|".join(m.value for m in CollectionMode) + ")$"
from .validate import backtest, load_benchmark

app = FastAPI(
    title="Pushpak: Real-time Airfare Price Index",
    version="0.1.0",
    description=(
        "Prototype high-frequency airfare price index for MoSPI's Data "
        "Informatics & Innovation Division.\n\n"
        "**Every index value carries its own provenance.** Check "
        "`quality_flag`, `synthetic_share` and `imputed_share` before use. "
        "In this prototype the underlying quotes are predominantly SYNTHETIC "
        "because every configured fare source is robots-disallowed — see "
        "`/api/v1/provenance`."
    ),
    docs_url="/docs",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)


# A fresh Postgres has no tables, where a fresh SQLite file at least existed.
# Create them on import so the API does not 503 forever against a blank
# database. A database that is simply down is not fatal here: /api/v1/health
# reports degraded, which is what an operator needs to see.
#
# ponytail: idempotent CREATE IF NOT EXISTS, not a migration tool. Reach for
# Alembic when a column has to change shape under live data.
try:
    storage.init_db()
except Exception as exc:                           # noqa: BLE001
    print(f"[pushpak] schema init failed, API will report degraded: {exc}")

WEB_DIR = Path(__file__).parent / "web"

# The React landing page, built by `npm run build` in web/. In the Docker stack
# nginx serves this and proxies the rest; locally there is no nginx, so the API
# serves it too and the whole prototype lives on one origin at :8000. Absent in
# the container image and in a fresh checkout — `/` falls back to the dashboard
# rather than 500ing, so the API never depends on a node build having happened.
LANDING_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"

# ---------------------------------------------------------------------------
# Cached computation, invalidated on DB mtime
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {"mtime": None, "result": None}


def get_result() -> IndexResult:
    try:
        version = storage.db_version()
    except Exception as exc:                       # noqa: BLE001
        raise HTTPException(503, f"Database unreachable: {exc}") from exc
    if _cache["mtime"] != version or _cache["result"] is None:
        df = storage.load_quotes()
        if not len(df):
            raise HTTPException(503, "No quotes collected yet. Run: pushpak.cli seed")
        _cache["result"] = compute_index(df)
        _cache["mtime"] = version
    return _cache["result"]


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso"))


# ---------------------------------------------------------------------------
@app.get("/api/v1/health", tags=["meta"])
def health() -> dict:
    """Liveness plus a data inventory. Safe to poll."""
    try:
        s = storage.stats()
    except Exception as exc:                       # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}
    ready = s["total_quotes"] > 0
    return {
        "status": "ok" if ready else "no_data",
        "schema_version": SCHEMA_VERSION,
        "api_version": app.version,
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "data": s,
    }


@app.get("/api/v1/methodology", tags=["meta"])
def methodology() -> dict:
    """The index formula, weights and their provenance — machine readable."""
    rw = load_route_weights()
    return {
        "index_name": "Pushpak Index",
        "family": "Fixed-basket Laspeyres-style price index",
        "formula": (
            "Pushpak(t) = 100 x SUM over cells c of [ w(c) x P(c,t) / P(c,0) ], "
            "where a cell c is one (route x advance-purchase window), "
            "w(c) = route_weight x ap_window_weight (renormalised over the "
            "cells present on day t), P(c,t) is the arithmetic mean of priced "
            "quotes in the cell, and P(c,0) is its base-period mean."
        ),
        "base_index_value": config.BASE_INDEX_VALUE,
        "base_period_days": config.BASE_PERIOD_DAYS,
        "elementary_aggregate": "Dutot (ratio of arithmetic mean cell prices)",
        "elementary_aggregate_alternative": {
            "name": "Jevons (weighted geometric mean of the same relatives)",
            "field": "index_value_jevons",
            "published": "beside every index value, at every frequency",
            "why_not_headline": (
                "The arithmetic form is reconstructable by adding the 20 terms "
                "returned by /api/v1/index/explain, which the brief asked for. "
                "Jevons is arguably better behaved for airfares, so it is "
                "published alongside rather than chosen silently. By AM-GM it "
                "is never above the headline, and the two coincide exactly "
                "when every cell moves by the same factor."
            ),
        },
        "route_weights": {
            "provenance": config.ROUTE_WEIGHT_PROVENANCE,
            "source": "DGCA monthly domestic city-pair passenger statistics",
            "source_file": config.DGCA_TRAFFIC_CSV.name,
            "reproduce_with": "python3 scripts/build_weights.py",
            "traffic_window": rw["traffic_window"].iloc[0] if len(rw) else None,
            "national_coverage_share": float(rw["national_share"].sum()) if len(rw) else None,
            "weights": _records(rw[["route_id", "annual_pax", "weight", "national_share"]]),
        },
        "advance_purchase_weights": {
            "provenance": config.AP_WINDOW_WEIGHT_PROVENANCE,
            "warning": (
                "ASSUMED_PRIOR — a documented modelling assumption approximating "
                "the domestic booking curve, NOT an official statistic. A "
                "production Pushpak must replace these with booking-date "
                "distributions obtained under a data-sharing agreement."
            ),
            "weights": ap_weights(),
        },
        "imputation": {
            "ladder": [
                {"rule": "CARRY_FORWARD",
                 "description": "Last observed cell price, if not older than "
                                f"{config.MAX_CARRY_FORWARD_DAYS} days."},
                {"rule": "CLASS_MEAN",
                 "description": "Impute the movement, not the level: base price x "
                                "mean relative change of the route's other observed "
                                "cells that day."},
                {"rule": "BASE_CARRY",
                 "description": "Hold at base (relative = 1.0). Last resort; damps "
                                "the index toward no-change, so counted separately."},
            ],
            "sold_out_policy": (
                "SOLD_OUT quotes are retained as unpriced observations, never "
                "dropped. Dropping them biases an airfare index downward because "
                "inventory sells out when fares are high."
            ),
            "outlier_policy": (
                f"Quotes outside {config.OUTLIER_MULTIPLE}x the median of their own "
                "cell are re-flagged OUTLIER_REJECTED and retained for audit."
            ),
        },
        "not_implemented": [
            "chain-linking", "seasonal adjustment", "quality adjustment",
            "geometric (Jevons) elementary aggregation",
        ],
        "quality_thresholds": config.QUALITY_THRESHOLDS,
    }


@app.get("/api/v1/provenance", tags=["meta"])
def provenance() -> dict:
    """Where the data actually came from, and where it could not come from."""
    snap_path = config.DATA_DIR / "robots_compliance_snapshot.json"
    snapshot = json.loads(snap_path.read_text()) if snap_path.exists() else None
    s = storage.stats()
    total = s["total_quotes"] or 1
    return {
        "quote_counts": s,
        "synthetic_share": s["synthetic_quotes"] / total,
        "live_share": s["live_quotes"] / total,
        "official_api_share": s.get("official_api_quotes", 0) / total,
        "collection_modes": {
            "LIVE_SCRAPE": "a real HTTP response from a scraped page",
            "OFFICIAL_API": "licensed API content, not scraping",
            "SYNTHETIC": "generated by pushpak.collect.synthetic",
        },
        "warning": (
            "Quotes are labelled at row level by collection_mode, and the share "
            "of each is reported with every index value. Where synthetic_share "
            "is high, the figures demonstrate the pipeline rather than measuring "
            "real airfares."
        ),
        "robots_compliance": snapshot,
        "official_data_channels": config.OFFICIAL_DATA_CHANNELS,
        "sources_configured": [
            {"name": s_.name, "type": s_.source_type, "base_url": s_.base_url,
             "min_seconds_between_requests": s_.min_seconds_between_requests}
            for s_ in config.SOURCES
        ],
        "pii_collected": config.COLLECT_PII,
    }


# ---------------------------------------------------------------------------
@app.get("/api/v1/index", tags=["index"])
def get_index(
    frequency: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
) -> dict:
    """The Pushpak Index series. Primary endpoint for a downstream consumer."""
    res = get_result()
    df = res.daily if frequency == "daily" else res.resample(frequency)
    if date_from:
        df = df[df["period"] >= date_from]
    if date_to:
        df = df[df["period"] <= date_to]

    cols = ["period", "index_value", "index_value_jevons",
            "n_quotes", "n_cells", "n_cells_imputed",
            "synthetic_share", "imputed_share", "soldout_share", "quality_flag"]
    cols = [c for c in cols if c in df.columns]
    return {
        "index_name": "Pushpak Index",
        "frequency": frequency,
        "base_index_value": config.BASE_INDEX_VALUE,
        "base_period": res.base_dates,
        "n_periods": len(df),
        "methodology_url": "/api/v1/methodology",
        "provenance_url": "/api/v1/provenance",
        "data_warning": (
            "Check synthetic_share and quality_flag on every row before use."
        ),
        "series": _records(df[cols]),
    }


@app.get("/api/v1/index/latest", tags=["index"])
def latest_index() -> dict:
    res = get_result()
    if not len(res.daily):
        raise HTTPException(404, "no index computed")
    row = res.daily.iloc[-1]
    prev = res.daily.iloc[-2] if len(res.daily) > 1 else None
    return {
        "period": row["period"],
        "index_value": round(float(row["index_value"]), 4),
        "index_value_jevons": (
            round(float(row["index_value_jevons"]), 4)
            if row["index_value_jevons"] is not None
            and not pd.isna(row["index_value_jevons"]) else None
        ),
        "change_vs_previous_day": (
            round(float(row["index_value"] - prev["index_value"]), 4)
            if prev is not None else None
        ),
        "change_pct": (
            round(float((row["index_value"] / prev["index_value"] - 1) * 100), 4)
            if prev is not None else None
        ),
        "base_index_value": config.BASE_INDEX_VALUE,
        "base_period": res.base_dates,
        "n_quotes": int(row["n_quotes"]),
        "synthetic_share": round(float(row["synthetic_share"]), 4),
        "imputed_share": round(float(row["imputed_share"]), 4),
        "soldout_share": round(float(row["soldout_share"]), 4),
        "quality_flag": row["quality_flag"],
    }


@app.get("/api/v1/index/explain", tags=["index"])
def explain_index(date_: Optional[str] = Query(None, alias="date")) -> dict:
    """Reconstruct one published index number term by term. Auditability."""
    res = get_result()
    period = date_ or (res.daily["period"].iloc[-1] if len(res.daily) else None)
    if not period:
        raise HTTPException(404, "no index computed")
    try:
        return explain(res, period)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------------------
@app.get("/api/v1/routes", tags=["routes"])
def routes() -> dict:
    rw = load_route_weights()
    return {
        "basket_size": len(rw),
        "weight_provenance": config.ROUTE_WEIGHT_PROVENANCE,
        "traffic_window": rw["traffic_window"].iloc[0] if len(rw) else None,
        "national_coverage_share": float(rw["national_share"].sum()),
        "advance_purchase_windows": list(config.ADVANCE_PURCHASE_WINDOWS),
        "routes": _records(rw),
    }


@app.get("/api/v1/routes/{route_id}/series", tags=["routes"])
def route_series(route_id: str) -> dict:
    res = get_result()
    df = res.route_daily[res.route_daily["route_id"] == route_id.upper()]
    if not len(df):
        raise HTTPException(404, f"unknown route {route_id!r}")
    return {
        "route_id": route_id.upper(),
        "n_periods": len(df),
        "series": _records(
            df[["period", "index_value", "mean_fare", "weight", "n_quotes",
                "imputed_share"]]
        ),
    }


@app.get("/api/v1/leadtime/{route_id}", tags=["routes"])
def leadtime(route_id: str) -> dict:
    """Fare vs days-to-departure — the lead-time elasticity view."""
    res = get_result()
    cells = res.cells[res.cells["route_id"] == route_id.upper()]
    if not len(cells):
        raise HTTPException(404, f"unknown route {route_id!r}")

    obs = cells[cells["cell_price"].notna()]
    curve = (
        obs.groupby("advance_purchase_days")
        .agg(mean_fare=("cell_price", "mean"),
             median_fare=("cell_price", "median"),
             p10=("cell_price", lambda s: float(s.quantile(0.10))),
             p90=("cell_price", lambda s: float(s.quantile(0.90))),
             n_days=("cell_price", "count"),
             n_quotes=("n_quotes", "sum"),
             soldout_share=("soldout_share", "mean"))
        .reset_index()
        .sort_values("advance_purchase_days", ascending=False)
    )

    # Elasticity of fare w.r.t. lead time: d(ln fare)/d(ln days)
    elasticity = None
    if len(curve) > 1:
        import numpy as np
        x = np.log(curve["advance_purchase_days"].to_numpy(float))
        y = np.log(curve["mean_fare"].to_numpy(float))
        if np.isfinite(x).all() and np.isfinite(y).all() and x.std() > 0:
            elasticity = float(np.polyfit(x, y, 1)[0])

    return {
        "route_id": route_id.upper(),
        "lead_time_elasticity": None if elasticity is None else round(elasticity, 4),
        "elasticity_interpretation": (
            "d(ln mean fare) / d(ln days to departure). Negative means fares "
            "rise as departure approaches."
        ),
        "curve": _records(curve),
    }


@app.get("/api/v1/heatmap", tags=["routes"])
def heatmap(metric: str = Query("index", pattern="^(index|fare|volatility)$")) -> dict:
    """Route x date grid for the dashboard heatmap."""
    res = get_result()
    rd = res.route_daily
    if not len(rd):
        raise HTTPException(404, "no route series")

    if metric == "volatility":
        piv = rd.pivot_table(index="route_id", columns="period",
                             values="index_value", aggfunc="mean")
        # Transpose so the rolling window runs down the (time) index —
        # rolling(axis=1) is deprecated in pandas 2.x.
        vals = (piv.T.pct_change().rolling(7, min_periods=2).std() * 100).T
    else:
        col = "index_value" if metric == "index" else "mean_fare"
        vals = rd.pivot_table(index="route_id", columns="period",
                              values=col, aggfunc="mean")

    return {
        "metric": metric,
        "unit": {"index": "index points (base=100)", "fare": "INR",
                 "volatility": "% std-dev of daily change, 7-day window"}[metric],
        "routes": list(vals.index),
        "periods": [str(c) for c in vals.columns],
        "values": [[None if pd.isna(v) else round(float(v), 4) for v in row]
                   for row in vals.to_numpy()],
    }


# ---------------------------------------------------------------------------
@app.get("/api/v1/fares", tags=["data"])
def fares(
    route_id: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    collection_mode: Optional[str] = Query(None, pattern=_COLLECTION_MODE_PATTERN),
    limit: int = Query(500, le=5000),
) -> dict:
    """Raw normalised quotes, including unpriced and sold-out rows."""
    df = storage.load_quotes(
        route_id=route_id.upper() if route_id else None,
        date_from=date_from, date_to=date_to, collection_mode=collection_mode,
    )
    total = len(df)
    return {
        "n_returned": min(total, limit),
        "n_matching": total,
        "schema_version": SCHEMA_VERSION,
        "columns": list(COLUMNS),
        "note": (
            "Rows with quote_status != 'OK' carry NULL fares by design "
            "(sold out, cancelled, robots-blocked, or failed). They are "
            "retained, not dropped."
        ),
        "quotes": _records(df.head(limit)),
    }


@app.get("/api/v1/audit", tags=["data"])
def audit(limit: int = Query(200, le=2000)) -> dict:
    """The scraping request ledger. Every request, including refusals."""
    df = storage.load_audit(limit=limit)
    by_outcome = (
        df["outcome"].value_counts().to_dict() if len(df) else {}
    )
    return {
        "n_returned": len(df),
        "by_outcome": by_outcome,
        "user_agent": config.USER_AGENT,
        "policy": {
            "respect_robots": config.RESPECT_ROBOTS,
            "fail_closed": True,
            "default_crawl_delay_seconds": config.DEFAULT_CRAWL_DELAY,
            "max_requests_per_source_per_run": config.MAX_REQUESTS_PER_SOURCE_PER_RUN,
            "pii_collected": config.COLLECT_PII,
        },
        "requests": _records(df),
    }


@app.get("/api/v1/validation", tags=["validation"])
def validation() -> dict:
    """Back-test against the external benchmark. Verdict returned verbatim."""
    res = get_result()
    try:
        return backtest(res)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------------------
# Collection control (the "run a scrape" surface the dashboard drives)
# ---------------------------------------------------------------------------
#: In-memory job register. Deliberately not persisted: these are interactive,
#: operator-triggered runs, and the durable record of what they did already
#: lives in the audit ledger and the fare_quotes table.
_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()
MAX_JOB_HISTORY = 20


def _run_collection_job(job_id: str, mode: str, days: int, dry_run: bool) -> None:
    from .collect.runner import run_collection
    try:
        _quotes, summary = run_collection(mode=mode, days=days, dry_run=dry_run)
        d = summary.as_dict()
        with _job_lock:
            _jobs[job_id].update(
                status="done", finished_at=datetime.now().isoformat(timespec="seconds"),
                summary=d,
            )
        # New quotes invalidate the cached index.
        _cache["mtime"] = None
    except Exception as exc:                          # noqa: BLE001
        with _job_lock:
            _jobs[job_id].update(
                status="failed", finished_at=datetime.now().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}",
            )


@app.post("/api/v1/collect", tags=["collection"])
def start_collection(
    background: BackgroundTasks,
    mode: str = Query("live", pattern="^(live|api|synthetic|hybrid)$"),
    days: int = Query(1, ge=1, le=7),
    dry_run: bool = False,
) -> dict:
    """Start a collection run and return a job id to poll.

    Runs in the background because a live run waits on robots.txt fetches and
    per-host rate limits, which takes longer than a request should.

    ``live`` scrapes only, and on this basket that means most sources are
    refused before a socket opens. ``api`` uses the licensed fare-API channel.
    ``hybrid`` tries both and fills the remaining gaps with quotes labelled
    SYNTHETIC.
    """
    job_id = uuid.uuid4().hex[:12]
    with _job_lock:
        if len(_jobs) >= MAX_JOB_HISTORY:
            for old_id in sorted(_jobs, key=lambda k: _jobs[k]["started_at"])[:5]:
                _jobs.pop(old_id, None)
        _jobs[job_id] = {
            "job_id": job_id, "mode": mode, "days": days, "dry_run": dry_run,
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None, "summary": None, "error": None,
        }
    background.add_task(_run_collection_job, job_id, mode, days, dry_run)
    return {
        "job_id": job_id, "status": "running", "mode": mode,
        "poll": f"/api/v1/collect/{job_id}",
    }


@app.get("/api/v1/collect/{job_id}", tags=["collection"])
def collection_job(job_id: str) -> dict:
    with _job_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"unknown job {job_id}")
    return job


@app.get("/api/v1/collect", tags=["collection"])
def collection_jobs() -> dict:
    with _job_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)
    return {"n_jobs": len(jobs), "jobs": jobs}


@app.get("/api/v1/sources", tags=["collection"])
def sources() -> dict:
    """Every configured source, its robots verdict, and the licensed channel."""
    from .collect.official_api import status as api_status
    snap_path = config.DATA_DIR / "robots_compliance_snapshot.json"
    verdicts = {}
    if snap_path.exists():
        for r in json.loads(snap_path.read_text()).get("results", []):
            verdicts[r["source"]] = {
                "allowed": r["allowed"], "reason": r["reason"], "rule": r.get("rule"),
                "crawl_delay": r.get("crawl_delay"),
            }
    rows = [
        {
            "name": s_.name,
            "type": s_.source_type,
            "base_url": s_.base_url,
            "min_seconds_between_requests": s_.min_seconds_between_requests,
            "robots": verdicts.get(s_.name),
            "official_channel": config.OFFICIAL_DATA_CHANNELS.get(s_.name),
        }
        for s_ in config.SOURCES
    ]
    permitted = sum(1 for r in rows if (r["robots"] or {}).get("allowed"))
    return {
        "n_sources": len(rows),
        "n_permitted": permitted,
        "note": (
            "Permitted means robots.txt allows the request. It does not mean "
            "the page yields fares: every fare grid in this basket is rendered "
            "client-side, so a compliant static client still gets no prices."
        ),
        "sources": rows,
        "licensed_api": api_status(),
        "refresh_verdicts_with": "python3 -m pushpak.cli robots-audit",
    }


# ---------------------------------------------------------------------------
# Derived statistics
# ---------------------------------------------------------------------------
@app.get("/api/v1/stats", tags=["statistics"])
def statistics() -> dict:
    """Rates of change, volatility, dispersion, attribution and diffusion.

    Year-on-year change is returned as ``null`` with a reason rather than
    approximated, and every annualised figure is flagged as a projection from
    a short window.
    """
    from .stats import summary as stats_summary
    res = get_result()
    return stats_summary(res.daily, res.cells)


@app.get("/api/v1/stats/attribution", tags=["statistics"])
def attribution(
    date_: Optional[str] = Query(None, alias="date"),
    previous: Optional[str] = None,
) -> dict:
    """Which routes drove the index change, in index points. Sums to the total."""
    from .stats import route_contributions
    res = get_result()
    period = date_ or (res.daily["period"].iloc[-1] if len(res.daily) else None)
    if not period:
        raise HTTPException(404, "no index computed")
    return route_contributions(res.cells, period, previous)


@app.get("/api/v1/stats/volatility", tags=["statistics"])
def vol(window: int = Query(7, ge=2, le=90)) -> dict:
    from .stats import volatility
    return volatility(get_result().daily, window)


# ---------------------------------------------------------------------------
# Official CPI context
# ---------------------------------------------------------------------------
@app.get("/api/v1/cpi", tags=["official context"])
def cpi_series(
    series: str = Query("combined", pattern="^(combined|rural|urban)$"),
) -> dict:
    """The official MoSPI CPI General Index, with month-on-month and YoY change.

    This is context, not a benchmark. The published workbook has no transport
    sub-group, so it cannot be used to validate an airfare index. See
    ``/api/v1/cpi/compare`` for the stated limits.
    """
    from .cpi import BASE_PERIOD_NOTE, SOURCE_NOTE, cpi_inflation
    try:
        df = cpi_inflation(series)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "name": "CPI General Index (All Groups), All India",
        "variant": series,
        "source": SOURCE_NOTE,
        "base_period": BASE_PERIOD_NOTE,
        "frequency": "monthly",
        "has_transport_subgroup": False,
        "n_observations": len(df),
        "series": _records(
            df[["period", "index_value", "mom_pct", "yoy_pct"]]
        ),
    }


@app.get("/api/v1/cpi/compare", tags=["official context"])
def cpi_compare(
    series: str = Query("combined", pattern="^(combined|rural|urban)$"),
) -> dict:
    """Pushpak Index beside the official CPI, with the comparison's limits stated.

    Returns ``comparable_as_level: false``. No correlation is computed, because
    a correlation between an airfare index and a food-and-housing-dominated
    headline index would have no interpretation.
    """
    from .cpi import compare_to_pushpak
    try:
        return compare_to_pushpak(get_result().daily, series)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/v1/cpi/statewise", tags=["official context"])
def cpi_statewise(
    measure: str = Query("inflation_pct", pattern="^(inflation_pct|index_value)$"),
) -> dict:
    """Latest state-wise official figures, all 36 states and union territories."""
    from .cpi import statewise_latest
    try:
        return statewise_latest(measure)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
@app.get("/api/v1/schedule", tags=["meta"])
def schedule(limit: int = Query(50, le=500)) -> dict:
    """The scheduled-run ledger. A silent scheduler looks like a stopped one."""
    from .scheduler import DEFAULT_JOBS, recent
    runs = recent(limit)
    return {
        "jobs": [
            {"name": j.name, "at": j.at,
             "weekday": j.weekday, "cadence": "weekly" if j.weekday is not None else "daily"}
            for j in DEFAULT_JOBS
        ],
        "idempotent": True,
        "note": (
            "A job that already succeeded for a given run_date is skipped, so a "
            "restart or a duplicate cron entry cannot double-collect."
        ),
        "n_runs": len(runs),
        "runs": runs,
    }


# ---------------------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    path = WEB_DIR / "index.html"
    if not path.exists():
        raise HTTPException(500, "dashboard assets missing")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing() -> HTMLResponse:
    """The landing page when it has been built, the dashboard when it has not."""
    index = LANDING_DIR / "index.html"
    if not index.is_file():
        return dashboard()
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/static/{name}", include_in_schema=False)
def static(name: str):
    path = (WEB_DIR / name).resolve()
    if not path.is_file() or WEB_DIR.resolve() not in path.parents:
        raise HTTPException(404, "not found")
    media = {"css": "text/css", "js": "application/javascript"}.get(
        path.suffix.lstrip("."), "text/plain"
    )
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type=media)


# Mounted last: a mount matches by prefix, so declaring it after every route
# above keeps /api, /docs and /dashboard ahead of it.
if (LANDING_DIR / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=LANDING_DIR / "assets"),
        name="landing-assets",
    )
