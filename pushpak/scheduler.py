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
  * **Retries with explicit backoff, then an alert.** A transient network
    failure should not cost a day of the series, and a permanent one should
    not fail silently. Each job declares ``max_attempts`` and the exact
    ``retry_delays`` between them; when the last attempt fails, a CRITICAL
    alert is raised through :mod:`pushpak.alerts` rather than logged and
    forgotten. Every attempt is a separate ``scheduled_runs`` row carrying its
    attempt number, so the history shows what was retried and how often.

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

from . import alerts, config, storage
from .schema import utc_now_iso

SCHEDULE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job           TEXT NOT NULL,
    run_date      TEXT NOT NULL,      -- the logical date the job ran FOR
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,      -- running | ok | failed | retrying | skipped
    detail_json   TEXT,
    error         TEXT,
    attempt       INTEGER NOT NULL DEFAULT 1,
    max_attempts  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_sched_job ON scheduled_runs(job, run_date);
"""


def init(db_path=None) -> None:
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        conn.executescript(SCHEDULE_SQL)
        # scheduled_runs is created here, not in storage.SCHEMA_SQL, so its
        # migrations have to run after the table exists.
        storage.apply_migrations(conn)
    alerts.init(db_path)


def already_succeeded(job: str, run_date: str, db_path=None) -> bool:
    with storage.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM scheduled_runs WHERE job=? AND run_date=? AND status='ok' LIMIT 1",
            (job, run_date),
        ).fetchone()
    return row is not None


def _start(job: str, run_date: str, db_path=None, attempt: int = 1,
           max_attempts: int = 1) -> int:
    with storage.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_runs (job, run_date, started_at, status, "
            "attempt, max_attempts) VALUES (?,?,?,?,?,?)",
            (job, run_date, utc_now_iso(), "running", attempt, max_attempts),
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
    d = summary.as_dict()
    # A run that succeeds but returns nothing is the failure mode that a plain
    # exit-code check misses entirely, so it is alerted on explicitly.
    if not d.get("total_quotes"):
        alerts.raise_alert(
            "WARNING", "scheduler.collect",
            f"collection for {run_date.isoformat()} stored zero quotes",
            {"mode": mode, "summary": d},
        )
    return d


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
    """Refresh the robots.txt compliance snapshot and alert on any change.

    The risk register's top entry is "a site changes robots.txt and revokes
    permission". The gate already fails closed on the next run, so nothing
    unlawful happens -- but the operator still needs to be told, because a
    source silently dropping out changes what the index is measuring.
    """
    from .collect.robots import RobotsGate
    out = config.DATA_DIR / "robots_compliance_snapshot.json"

    previous: dict[str, bool] = {}
    if out.exists():
        try:
            previous = {
                r["source"]: bool(r["allowed"])
                for r in json.loads(out.read_text()).get("results", [])
            }
        except (ValueError, KeyError, TypeError):
            previous = {}          # unreadable snapshot: treat as no baseline

    rows = RobotsGate().snapshot()
    out.write_text(json.dumps({
        "generated_at": utc_now_iso(),
        "user_agent": config.USER_AGENT,
        "policy": "fail-closed: unreachable robots.txt is treated as DISALLOWED",
        "results": rows,
    }, indent=2))

    revoked, granted = [], []
    for r in rows:
        was = previous.get(r["source"])
        if was is None:
            continue
        if was and not r["allowed"]:
            revoked.append({"source": r["source"],
                            "reason": r.get("reason"), "rule": r.get("rule")})
        elif not was and r["allowed"]:
            granted.append({"source": r["source"]})

    if revoked:
        alerts.raise_alert(
            "CRITICAL", "scheduler.robots-audit",
            f"{len(revoked)} source(s) revoked crawl permission",
            {"revoked": revoked,
             "action": "the gate already refuses them; update the README's "
                       "source table and the compliance claim"},
        )
    if granted:
        alerts.raise_alert(
            "INFO", "scheduler.robots-audit",
            f"{len(granted)} source(s) now permit crawling",
            {"granted": granted},
        )

    return {
        "sources_checked": len(rows),
        "sources_permitted": sum(1 for r in rows if r["allowed"]),
        "permission_revoked": revoked,
        "permission_granted": granted,
        "had_baseline": bool(previous),
    }


@dataclass
class Job:
    name: str
    fn: Callable[[date], dict]
    at: str                       # "HH:MM" local time
    weekday: Optional[int] = None  # 0=Monday; None means every day
    catch_up: bool = True
    #: Total attempts allowed per logical date, first try included.
    max_attempts: int = 3
    #: Seconds to wait before each retry. Index i is the wait before attempt
    #: i+2. Backoff is explicit rather than computed so an operator can read
    #: the retry schedule off the job definition.
    retry_delays: tuple[int, ...] = (120, 600)

    def due_on(self, now: datetime) -> bool:
        if self.weekday is not None and now.weekday() != self.weekday:
            return False
        hh, mm = (int(x) for x in self.at.split(":"))
        return (now.hour, now.minute) >= (hh, mm)

    def delay_before(self, attempt: int) -> int:
        """Seconds to wait before ``attempt`` (2-based). Clamps to the last."""
        if not self.retry_delays:
            return 0
        return self.retry_delays[min(attempt - 2, len(self.retry_delays) - 1)]


DEFAULT_JOBS: tuple[Job, ...] = (
    # Collect early, before the day's fare movements, then index.
    # Collection touches the network, so it gets the full retry ladder.
    Job("collect", lambda d: job_collect(d), at="02:00",
        max_attempts=3, retry_delays=(120, 600)),
    # Indexing is pure computation over the local database. If it fails twice
    # it will fail a third time, so it does not get a long ladder.
    Job("index", job_index, at="02:30", max_attempts=2, retry_delays=(60,)),
    # Sites change robots.txt. Re-check weekly so the compliance claim stays true.
    Job("robots-audit", job_robots_audit, at="03:00", weekday=0,
        max_attempts=2, retry_delays=(300,)),
)


@dataclass
class _Retry:
    """One job's pending retry. In-memory: a restart re-runs from attempt 1."""
    run_date: date
    attempt: int                  # the attempt number about to be made
    due_at: datetime
    last_error: str


class Scheduler:
    """The daemon. Owns the retry queue; see :meth:`run_due`."""

    def __init__(self, jobs=DEFAULT_JOBS, db_path=None, tick_seconds: int = 30,
                 mode: str = "hybrid"):
        self.jobs = jobs
        self.db_path = db_path
        self.tick_seconds = tick_seconds
        self.mode = mode
        self._stop = False
        #: job name -> pending retry. Held in memory on purpose: after a
        #: restart the job is simply due again, and replaying a persisted
        #: backoff clock across a restart is more surprising than useful.
        self._retries: dict[str, _Retry] = {}
        #: Jobs that exhausted their attempts, per logical date. Cleared when
        #: the date rolls over, so a bad day does not disable the job forever.
        self._exhausted: set[tuple[str, str]] = set()
        init(db_path)

    def request_stop(self, *_a) -> None:
        self._stop = True

    # -- retry queue --------------------------------------------------------
    def pending_retries(self) -> list[dict]:
        """The retry queue, for /api/v1/schedule and the dashboard."""
        return [
            {
                "job": name, "run_date": r.run_date.isoformat(),
                "next_attempt": r.attempt, "due_at": r.due_at.isoformat(timespec="seconds"),
                "last_error": r.last_error,
            }
            for name, r in sorted(self._retries.items())
        ]

    def _schedule_retry(self, job: Job, run_date: date, attempt: int,
                        err: str, now: datetime) -> int:
        """Queue the next attempt. Returns the delay used, in seconds."""
        delay = job.delay_before(attempt)
        self._retries[job.name] = _Retry(
            run_date=run_date, attempt=attempt,
            due_at=now + timedelta(seconds=delay), last_error=err,
        )
        return delay

    def _attempt(self, job: Job, run_date: date, attempt: int,
                 now: datetime) -> dict:
        """Run one attempt of one job and record the outcome."""
        row_id = _start(job.name, run_date.isoformat(), self.db_path,
                        attempt=attempt, max_attempts=job.max_attempts)
        try:
            detail = job.fn(run_date)
        except Exception as exc:                          # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            if attempt < job.max_attempts:
                delay = self._schedule_retry(job, run_date, attempt + 1, err, now)
                _finish(row_id, "retrying", error=err, db_path=self.db_path)
                print(f"[scheduler] {job.name} attempt {attempt}/{job.max_attempts} "
                      f"failed: {err} -- retrying in {delay}s", flush=True)
                return {"job": job.name, "status": "retrying", "attempt": attempt,
                        "error": err, "retry_in_seconds": delay}

            _finish(row_id, "failed", error=err, db_path=self.db_path)
            self._retries.pop(job.name, None)
            self._exhausted.add((job.name, run_date.isoformat()))
            alerts.raise_alert(
                "CRITICAL", f"scheduler.{job.name}",
                f"job '{job.name}' failed all {job.max_attempts} attempts "
                f"for {run_date.isoformat()}",
                {"job": job.name, "run_date": run_date.isoformat(),
                 "attempts": job.max_attempts, "last_error": err},
                db_path=self.db_path,
            )
            print(f"[scheduler] {job.name} FAILED after {job.max_attempts} "
                  f"attempts: {err}", flush=True)
            return {"job": job.name, "status": "failed", "attempt": attempt,
                    "error": err}

        _finish(row_id, "ok", detail, db_path=self.db_path)
        self._retries.pop(job.name, None)
        print(f"[scheduler] {job.name} ok (attempt {attempt}): {detail}", flush=True)
        return {"job": job.name, "status": "ok", "attempt": attempt,
                "detail": detail}

    # -- the tick -----------------------------------------------------------
    def run_due(self, now: Optional[datetime] = None, force: bool = False) -> list[dict]:
        """Run every due job, plus any retry whose backoff has elapsed.

        Retries are processed first: a job waiting to be re-attempted is more
        urgent than one that has not started, and doing so keeps a single job
        from being both retried and freshly started in the same tick.
        """
        now = now or datetime.now()
        run_date = now.date()
        results = []

        # Yesterday's exhausted jobs must not block today's run.
        self._exhausted = {
            (j, d) for (j, d) in self._exhausted if d == run_date.isoformat()
        }

        by_name = {j.name: j for j in self.jobs}
        for name, retry in list(self._retries.items()):
            if name not in by_name:
                self._retries.pop(name, None)
                continue
            if not force and now < retry.due_at:
                continue
            self._retries.pop(name, None)
            results.append(self._attempt(by_name[name], retry.run_date,
                                         retry.attempt, now))

        for job in self.jobs:
            if job.name in self._retries:
                continue                       # already queued for a later tick
            if (job.name, run_date.isoformat()) in self._exhausted:
                continue                       # gave up on this date; alerted
            if any(r["job"] == job.name for r in results):
                continue                       # ran as a retry this tick
            if not force and not job.due_on(now):
                continue
            if not force and already_succeeded(job.name, run_date.isoformat(), self.db_path):
                continue
            results.append(self._attempt(job, run_date, 1, now))

        return results

    def serve_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        print(
            "[scheduler] started. jobs: "
            + ", ".join(
                f"{j.name}@{j.at} (<={j.max_attempts} attempts)" for j in self.jobs
            ),
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
