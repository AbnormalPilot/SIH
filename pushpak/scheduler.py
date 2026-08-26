"""Scheduled collection (component 1, operationalised).

A daily statistical product needs an unattended runner. This module provides
one with no new dependencies: a loop that wakes every 30 seconds, runs jobs
whose time has come, and records every attempt in the database.

Three properties matter for a statistics pipeline and are implemented here
rather than left to the operator:

  * **Idempotent.** A job that already succeeded for a given date is skipped,
    so a restart, a duplicate cron entry, or a container respawn cannot
    double-collect and inflate ``n_quotes``.
  * **Every run is recorded**, including failures and skips, in the
    ``scheduled_runs`` table and at ``/api/v1/schedule``. A silent scheduler
    is indistinguishable from a stopped one.
  * **Catch-up on start.** If the process was down when a job was due, it runs
    once on the next tick rather than waiting a full day.

Two ways to run it, both documented in the README:

    python3 -m pushpak.cli schedule            # the built-in daemon
    deploy/pushpak.crontab                     # host cron, calling the CLI directly

The daemon is what the docker-compose ``cron`` service runs.
"""
from __future__ import annotations

import json
import signal
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import config, storage
from .schema import utc_now_iso

SCHEDULE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job           TEXT NOT NULL,
    run_date      TEXT NOT NULL,      -- the logical date the job ran FOR
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,      -- running | ok | failed | skipped
    detail_json   TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS ix_sched_job ON scheduled_runs(job, run_date);
"""


def init(db_path=None) -> None:
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        conn.executescript(SCHEDULE_SQL)


def already_succeeded(job: str, run_date: str, db_path=None) -> bool:
    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM scheduled_runs WHERE job=? AND run_date=? AND status='ok' LIMIT 1",
            (job, run_date),
        ).fetchone()
    return row is not None


def _start(job: str, run_date: str, db_path=None) -> int:
    with storage.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_runs (job, run_date, started_at, status) VALUES (?,?,?,?)",
            (job, run_date, utc_now_iso(), "running"),
        )
        return cur.lastrowid


def _finish(row_id: int, status: str, detail=None, error=None, db_path=None) -> None:
    with storage.connect(db_path) as conn:
        conn.execute(
            "UPDATE scheduled_runs SET finished_at=?, status=?, detail_json=?, error=? WHERE id=?",
            (utc_now_iso(), status, json.dumps(detail, default=str) if detail else None,
             error, row_id),
        )


def recent(limit: int = 50, db_path=None) -> list[dict]:
    with storage.connect(db_path) as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM scheduled_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        except Exception:              # noqa: BLE001 — table not created yet
            return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# The jobs
# ---------------------------------------------------------------------------
def job_collect(run_date: date, mode: str = "hybrid") -> dict:
    """Attempt live collection for one observation date; label any gap-fill."""
    from .collect.runner import run_collection
    _quotes, summary = run_collection(mode=mode, observation_date=run_date, days=1)
    return summary.as_dict()


def job_index(run_date: date) -> dict:
    """Recompute the index from everything collected so far and store it."""
    from .index import compute_index
    df = storage.load_quotes()
    if not len(df):
        return {"skipped": "no quotes"}
    res = compute_index(df)
    storage.save_index(res.daily, "daily")
    storage.save_route_index(res.route_daily)
    for freq in ("weekly", "monthly"):
        storage.save_index(res.resample(freq), freq)
    latest = res.daily.iloc[-1] if len(res.daily) else None
    return {
        "n_days": len(res.daily),
        "latest_period": None if latest is None else latest["period"],
        "latest_index": None if latest is None else round(float(latest["index_value"]), 4),
        "quality_flag": None if latest is None else latest["quality_flag"],
    }


def job_robots_audit(run_date: date) -> dict:
    """Refresh the robots.txt compliance snapshot."""
    from .collect.robots import RobotsGate
    rows = RobotsGate().snapshot()
    out = config.DATA_DIR / "robots_compliance_snapshot.json"
    out.write_text(json.dumps({
        "generated_at": utc_now_iso(),
        "user_agent": config.USER_AGENT,
        "policy": "fail-closed: unreachable robots.txt is treated as DISALLOWED",
        "results": rows,
    }, indent=2))
    return {
        "sources_checked": len(rows),
        "sources_permitted": sum(1 for r in rows if r["allowed"]),
    }


@dataclass
class Job:
    name: str
    fn: Callable[[date], dict]
    at: str                       # "HH:MM" local time
    weekday: Optional[int] = None  # 0=Monday; None means every day
    catch_up: bool = True

    def due_on(self, now: datetime) -> bool:
        if self.weekday is not None and now.weekday() != self.weekday:
            return False
        hh, mm = (int(x) for x in self.at.split(":"))
        return (now.hour, now.minute) >= (hh, mm)


DEFAULT_JOBS: tuple[Job, ...] = (
    # Collect early, before the day's fare movements, then index.
    Job("collect", lambda d: job_collect(d), at="02:00"),
    Job("index", job_index, at="02:30"),
    # Sites change robots.txt. Re-check weekly so the compliance claim stays true.
    Job("robots-audit", job_robots_audit, at="03:00", weekday=0),
)


class Scheduler:
    def __init__(self, jobs=DEFAULT_JOBS, db_path=None, tick_seconds: int = 30,
                 mode: str = "hybrid"):
        self.jobs = jobs
        self.db_path = db_path
        self.tick_seconds = tick_seconds
        self.mode = mode
        self._stop = False
        init(db_path)

    def request_stop(self, *_a) -> None:
        self._stop = True

    def run_due(self, now: Optional[datetime] = None, force: bool = False) -> list[dict]:
        """Run every job that is due and has not already succeeded today."""
        now = now or datetime.now()
        run_date = now.date()
        results = []

        for job in self.jobs:
            if not force and not job.due_on(now):
                continue
            if not force and already_succeeded(job.name, run_date.isoformat(), self.db_path):
                continue

            row_id = _start(job.name, run_date.isoformat(), self.db_path)
            try:
                detail = job.fn(run_date)
                _finish(row_id, "ok", detail, db_path=self.db_path)
                results.append({"job": job.name, "status": "ok", "detail": detail})
                print(f"[scheduler] {job.name} ok: {detail}", flush=True)
            except Exception as exc:                      # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                _finish(row_id, "failed", error=err, db_path=self.db_path)
                results.append({"job": job.name, "status": "failed", "error": err})
                print(f"[scheduler] {job.name} FAILED: {err}", flush=True)
                traceback.print_exc()
        return results

    def serve_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        print(
            "[scheduler] started. jobs: "
            + ", ".join(f"{j.name}@{j.at}" for j in self.jobs),
            flush=True,
        )
        while not self._stop:
            try:
                self.run_due()
            except Exception:                             # noqa: BLE001
                traceback.print_exc()                     # never let the loop die
            for _ in range(self.tick_seconds):
                if self._stop:
                    break
                time.sleep(1)
        print("[scheduler] stopped.", flush=True)
