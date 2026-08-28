"""Component 8 — tests for the scheduler's retry queue and the alert sink.

The failure this guards against is specific: a statistics pipeline that stops
collecting but keeps publishing. Recording a failure in a table nobody reads is
not enough, so these tests check that a transient failure is retried, that a
permanent one escalates to a CRITICAL alert exactly once, and that an
unreachable alert webhook can never take down the job it was reporting on.
"""
from datetime import date, datetime, timedelta

import pytest

from pushpak import alerts, config, scheduler, storage
from pushpak.scheduler import Job, Scheduler


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sched.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    storage.init_db(path)
    scheduler.init(path)
    return path


def _job(fn, name="probe", **kw):
    return Job(name, fn, at="00:00", **kw)


# ---------------------------------------------------------------------------
# retries
# ---------------------------------------------------------------------------
def test_a_job_that_succeeds_first_time_is_not_retried(db):
    calls = []
    s = Scheduler(jobs=(_job(lambda d: calls.append(1) or {"ok": True}),), db_path=db)
    out = s.run_due(force=True)
    assert [r["status"] for r in out] == ["ok"]
    assert out[0]["attempt"] == 1
    assert s.pending_retries() == []
    assert len(calls) == 1


def test_a_failing_job_is_queued_for_retry_not_abandoned(db):
    def boom(_d):
        raise RuntimeError("network unreachable")

    s = Scheduler(jobs=(_job(boom, max_attempts=3, retry_delays=(120, 600)),), db_path=db)
    out = s.run_due(force=True)

    assert out[0]["status"] == "retrying"
    assert out[0]["retry_in_seconds"] == 120
    pending = s.pending_retries()
    assert len(pending) == 1
    assert pending[0]["next_attempt"] == 2
    assert "network unreachable" in pending[0]["last_error"]


def test_a_queued_retry_waits_for_its_backoff(db):
    def boom(_d):
        raise RuntimeError("still down")

    now = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(boom, max_attempts=3, retry_delays=(300,)),), db_path=db)
    s.run_due(now=now)

    # One minute later the backoff has not elapsed, so nothing runs.
    assert s.run_due(now=now + timedelta(seconds=60)) == []
    assert len(s.pending_retries()) == 1

    # Six minutes later it does.
    out = s.run_due(now=now + timedelta(seconds=360))
    assert out and out[0]["attempt"] == 2


def test_a_transient_failure_recovers_on_the_retry(db):
    attempts = {"n": 0}

    def flaky(_d):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return {"recovered_on_attempt": attempts["n"]}

    now = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(flaky, max_attempts=3, retry_delays=(60,)),), db_path=db)
    assert s.run_due(now=now)[0]["status"] == "retrying"

    out = s.run_due(now=now + timedelta(seconds=120))
    assert out[0]["status"] == "ok"
    assert out[0]["detail"] == {"recovered_on_attempt": 2}
    assert s.pending_retries() == []


def test_every_attempt_is_its_own_row_in_the_ledger(db):
    attempts = {"n": 0}

    def flaky(_d):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("nope")
        return {"ok": True}

    now = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(flaky, max_attempts=3, retry_delays=(10, 10)),), db_path=db)
    for offset in (0, 60, 120):
        s.run_due(now=now + timedelta(seconds=offset))

    rows = [r for r in scheduler.recent(db_path=db) if r["job"] == "probe"]
    assert sorted(r["attempt"] for r in rows) == [1, 2, 3]
    assert {r["status"] for r in rows} == {"retrying", "ok"}
    assert all(r["max_attempts"] == 3 for r in rows)


# ---------------------------------------------------------------------------
# escalation
# ---------------------------------------------------------------------------
def test_exhausting_every_attempt_raises_exactly_one_critical_alert(db):
    def boom(_d):
        raise RuntimeError("permanently broken")

    now = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(boom, max_attempts=2, retry_delays=(30,)),), db_path=db)
    s.run_due(now=now)
    out = s.run_due(now=now + timedelta(seconds=60))

    assert out[0]["status"] == "failed"
    critical = alerts.recent(db_path=db, severity="CRITICAL")
    assert len(critical) == 1
    assert "probe" in critical[0]["title"]
    assert critical[0]["detail"]["attempts"] == 2
    assert "permanently broken" in critical[0]["detail"]["last_error"]


def test_an_exhausted_job_does_not_run_again_that_day(db):
    calls = {"n": 0}

    def boom(_d):
        calls["n"] += 1
        raise RuntimeError("broken")

    now = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(boom, max_attempts=2, retry_delays=(30,)),), db_path=db)
    s.run_due(now=now)
    s.run_due(now=now + timedelta(seconds=60))
    before = calls["n"]

    s.run_due(now=now + timedelta(seconds=600))
    assert calls["n"] == before               # no further attempts today
    assert len(alerts.recent(db_path=db, severity="CRITICAL")) == 1   # alerted once


def test_a_new_day_clears_the_exhausted_state(db):
    calls = {"n": 0}

    def boom(_d):
        calls["n"] += 1
        raise RuntimeError("broken")

    day1 = datetime(2026, 8, 26, 2, 0, 0)
    s = Scheduler(jobs=(_job(boom, max_attempts=1, retry_delays=()),), db_path=db)
    s.run_due(now=day1)
    assert calls["n"] == 1

    s.run_due(now=day1 + timedelta(days=1))
    assert calls["n"] == 2, "a bad day must not disable the job forever"


def test_idempotency_still_holds_with_retries_enabled(db):
    calls = {"n": 0}
    s = Scheduler(jobs=(_job(lambda d: calls.__setitem__("n", calls["n"] + 1) or {}),),
                  db_path=db)
    now = datetime(2026, 8, 26, 2, 0, 0)
    s.run_due(now=now)
    s.run_due(now=now + timedelta(seconds=60))
    assert calls["n"] == 1, "a job that already succeeded today must be skipped"


def test_retry_delays_clamp_to_the_last_configured_value():
    j = _job(lambda d: {}, max_attempts=5, retry_delays=(60, 300))
    assert j.delay_before(2) == 60
    assert j.delay_before(3) == 300
    assert j.delay_before(4) == 300      # clamped, never IndexError
    assert j.delay_before(5) == 300


def test_a_job_with_no_retry_delays_reports_zero():
    assert _job(lambda d: {}, retry_delays=()).delay_before(2) == 0


# ---------------------------------------------------------------------------
# the alert sink
# ---------------------------------------------------------------------------
def test_alerts_persist_and_come_back_newest_first(db):
    alerts.raise_alert("INFO", "test", "first", db_path=db)
    alerts.raise_alert("WARNING", "test", "second", {"k": "v"}, db_path=db)
    rows = alerts.recent(db_path=db)
    assert [r["title"] for r in rows] == ["second", "first"]
    assert rows[0]["detail"] == {"k": "v"}


def test_alerts_filter_by_severity(db):
    alerts.raise_alert("INFO", "test", "chatter", db_path=db)
    alerts.raise_alert("CRITICAL", "test", "fire", db_path=db)
    assert [r["title"] for r in alerts.recent(db_path=db, severity="CRITICAL")] == ["fire"]


def test_an_unknown_severity_is_refused(db):
    with pytest.raises(ValueError, match="severity"):
        alerts.raise_alert("PANIC", "test", "nope", db_path=db)


def test_a_broken_webhook_never_breaks_the_alert(db, monkeypatch):
    """An unreachable on-call endpoint must not take down the pipeline."""
    monkeypatch.setenv(alerts.WEBHOOK_ENV, "http://127.0.0.1:1/hook")
    row = alerts.raise_alert("CRITICAL", "test", "delivery will fail", db_path=db)
    assert row["delivered"] is False
    assert row["delivery_error"]
    assert len(alerts.recent(db_path=db)) == 1, "the alert is still recorded"


def test_the_webhook_url_is_never_stored_in_the_error(db, monkeypatch):
    """The URL may carry a secret token, so it must not leak into the DB."""
    secret = "http://127.0.0.1:1/hook/s3cr3t-token"
    monkeypatch.setenv(alerts.WEBHOOK_ENV, secret)
    row = alerts.raise_alert("CRITICAL", "test", "boom", db_path=db)
    assert "s3cr3t-token" not in (row["delivery_error"] or "")


def test_summary_counts_by_severity(db):
    alerts.raise_alert("INFO", "test", "a", db_path=db)
    alerts.raise_alert("CRITICAL", "test", "b", db_path=db)
    s = alerts.summary(db_path=db)
    assert s["counts"] == {"INFO": 1, "WARNING": 0, "CRITICAL": 1}
    assert s["latest_critical"]["title"] == "b"


def test_summary_is_clean_on_a_fresh_database(db):
    s = alerts.summary(db_path=db)
    assert s["counts"] == {"INFO": 0, "WARNING": 0, "CRITICAL": 0}
    assert s["latest_critical"] is None


# ---------------------------------------------------------------------------
# robots.txt change detection — the top entry in the risk register
# ---------------------------------------------------------------------------
def test_a_source_revoking_permission_raises_a_critical_alert(db, tmp_path, monkeypatch):
    snapshot = tmp_path / "robots_compliance_snapshot.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db)
    import json
    snapshot.write_text(json.dumps({"results": [
        {"source": "example.com", "allowed": True},
        {"source": "other.com", "allowed": False},
    ]}))

    class FakeGate:
        def snapshot(self):
            return [
                {"source": "example.com", "allowed": False,
                 "reason": "DISALLOWED", "rule": "Disallow: /flights"},
                {"source": "other.com", "allowed": True, "reason": "ALLOWED",
                 "rule": None},
            ]

    monkeypatch.setattr("pushpak.collect.robots.RobotsGate", FakeGate)
    out = scheduler.job_robots_audit(date(2026, 8, 26))

    assert [r["source"] for r in out["permission_revoked"]] == ["example.com"]
    assert [r["source"] for r in out["permission_granted"]] == ["other.com"]
    critical = alerts.recent(db_path=db, severity="CRITICAL")
    assert len(critical) == 1
    assert "revoked" in critical[0]["title"]


def test_the_first_audit_has_no_baseline_and_alerts_on_nothing(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db)

    class FakeGate:
        def snapshot(self):
            return [{"source": "example.com", "allowed": True, "reason": "ALLOWED",
                     "rule": None}]

    monkeypatch.setattr("pushpak.collect.robots.RobotsGate", FakeGate)
    out = scheduler.job_robots_audit(date(2026, 8, 26))
    assert out["had_baseline"] is False
    assert out["permission_revoked"] == []
    assert alerts.recent(db_path=db) == []
