"""Component 1 — the scheduler's retry policy.

A failed job used to be re-run on the very next tick. At tick_seconds=30 that
is ~2,880 attempts a day against third-party hosts, which contradicts the
politeness this project claims everywhere else. These pin the backoff so the
old behaviour cannot come back.
"""
from datetime import date, datetime, timedelta

import pytest

from pushpak import scheduler, storage


@pytest.fixture
def db(db_url):
    """A scheduled_runs table with no rows in it.

    Postgres has no throwaway file per test the way SQLite did, so each test
    truncates instead of getting a fresh database.
    """
    scheduler.init(db_url)
    with storage.connect(db_url) as conn:
        conn.execute("TRUNCATE scheduled_runs RESTART IDENTITY")
    return db_url


def test_backoff_grows_exponentially_and_is_capped():
    w = [scheduler.retry_wait_seconds(n) for n in range(0, 8)]
    assert w[0] == 0                                   # no failures, no wait
    assert w[1] == scheduler.RETRY_BASE_SECONDS        # 5 min
    assert w[2] == 2 * scheduler.RETRY_BASE_SECONDS    # 10 min
    assert w[3] == 4 * scheduler.RETRY_BASE_SECONDS    # 20 min
    assert all(a <= b for a, b in zip(w, w[1:])), "must be monotonic"
    assert w[-1] == scheduler.RETRY_MAX_SECONDS, "must be capped, not unbounded"


def test_a_failing_job_is_not_retried_on_the_very_next_tick(db):
    """The regression this policy exists for."""
    run_date = date(2026, 8, 26)
    calls = {"n": 0}

    def always_fails(_d):
        calls["n"] += 1
        raise RuntimeError("upstream down")

    s = scheduler.Scheduler(
        jobs=(scheduler.Job("collect", always_fails, at="00:00"),),
        db_url=db, tick_seconds=30,
    )
    t0 = datetime(2026, 8, 26, 2, 0, 0)
    s.run_due(now=t0)
    assert calls["n"] == 1

    # the next tick, 30 seconds later, must NOT call the job again
    r = s.run_due(now=t0 + timedelta(seconds=30))
    assert calls["n"] == 1, "a failure must not be retried on the next tick"
    assert r and r[0]["status"] == "backoff"
    assert r[0]["retry_in_seconds"] > 0


def test_the_job_does_run_again_once_the_backoff_has_elapsed(db):
    run_date = date(2026, 8, 26)
    calls = {"n": 0}

    def always_fails(_d):
        calls["n"] += 1
        raise RuntimeError("upstream down")

    s = scheduler.Scheduler(
        jobs=(scheduler.Job("collect", always_fails, at="00:00"),),
        db_url=db, tick_seconds=30,
    )
    t0 = datetime(2026, 8, 26, 2, 0, 0)
    s.run_due(now=t0)
    later = t0 + timedelta(seconds=scheduler.RETRY_BASE_SECONDS + 5)
    s.run_due(now=later)
    assert calls["n"] == 2, "backoff must expire, not block for the whole day"


def test_backoff_is_recorded_rather_than_silent(db):
    """A scheduler that says nothing is indistinguishable from a stopped one."""
    def always_fails(_d):
        raise RuntimeError("upstream down")

    s = scheduler.Scheduler(
        jobs=(scheduler.Job("collect", always_fails, at="00:00"),),
        db_url=db,
    )
    t0 = datetime(2026, 8, 26, 2, 0, 0)
    s.run_due(now=t0)
    rows = scheduler.recent(db_url=db)
    assert any(r["status"] == "failed" and r["error"] for r in rows), \
        "the failure itself must be on the ledger"
    r = s.run_due(now=t0 + timedelta(seconds=30))
    assert r[0]["status"] == "backoff", "the wait must be reported to the caller"


def test_a_succeeded_job_is_still_never_re_run(db):
    """Idempotence must survive the new retry path."""
    calls = {"n": 0}

    def ok(_d):
        calls["n"] += 1
        return {"fine": True}

    s = scheduler.Scheduler(
        jobs=(scheduler.Job("index", ok, at="00:00"),), db_url=db,
    )
    t0 = datetime(2026, 8, 26, 3, 0, 0)
    s.run_due(now=t0)
    s.run_due(now=t0 + timedelta(hours=5))
    assert calls["n"] == 1
