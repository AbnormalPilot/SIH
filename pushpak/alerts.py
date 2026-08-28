"""Operational alerting.

A statistics pipeline that fails quietly is worse than one that does not run:
the series keeps publishing, the numbers keep looking plausible, and nobody
learns that collection stopped a fortnight ago. The scheduler already records
every run, but a record nobody reads is not an alert.

This module is the missing half. It is deliberately small and dependency-free:

  * every alert is **persisted** to the ``alerts`` table, so the history
    survives a restart and is queryable at ``/api/v1/alerts``;
  * every alert is **printed** to stdout, which is what the docker-compose
    ``cron`` service's logs capture;
  * an alert is **POSTed to a webhook** when ``PUSHPAK_ALERT_WEBHOOK`` is set,
    which is how it would reach a real on-call channel. Delivery failure is
    itself recorded and never raises: an unreachable webhook must not take
    down the collection job it was meant to report on.

Alerts are raised for conditions an operator must act on, not for every
imperfection. Synthetic data and imputed cells are *expected* states of this
prototype and are reported through the index's quality flag instead; alerting
on them would train the reader to ignore alerts.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import storage
from .schema import utc_now_iso

WEBHOOK_ENV = "PUSHPAK_ALERT_WEBHOOK"
WEBHOOK_TIMEOUT_SECONDS = 5.0

#: Severities, in ascending order of "wake someone up".
SEVERITIES = ("INFO", "WARNING", "CRITICAL")

ALERTS_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    severity     TEXT NOT NULL,          -- INFO | WARNING | CRITICAL
    source       TEXT NOT NULL,          -- which subsystem raised it
    title        TEXT NOT NULL,
    detail_json  TEXT,
    delivered    INTEGER NOT NULL DEFAULT 0,   -- webhook accepted it
    delivery_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS ix_alerts_sev ON alerts(severity);
"""


def init(db_path=None) -> None:
    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        conn.executescript(ALERTS_SQL)


def _deliver(severity: str, source: str, title: str, detail: dict) -> tuple[bool, Optional[str]]:
    """POST the alert to the configured webhook. Never raises."""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url:
        return False, None
    try:
        import httpx

        r = httpx.post(
            url,
            json={
                "ts": utc_now_iso(), "severity": severity, "source": source,
                "title": title, "detail": detail, "product": "Pushpak",
            },
            timeout=WEBHOOK_TIMEOUT_SECONDS,
        )
        if r.status_code >= 400:
            return False, f"webhook returned HTTP {r.status_code}"
        return True, None
    except Exception as exc:                              # noqa: BLE001
        # The URL may contain a secret path token, so record the exception
        # type and message only -- never the target.
        return False, f"{type(exc).__name__}: {exc}"


def raise_alert(
    severity: str,
    source: str,
    title: str,
    detail: Optional[dict[str, Any]] = None,
    db_path=None,
) -> dict:
    """Record, print and (optionally) deliver one alert. Returns the row."""
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}; expected {SEVERITIES}")
    detail = detail or {}
    init(db_path)

    delivered, err = _deliver(severity, source, title, detail)
    ts = utc_now_iso()
    with storage.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO alerts (ts, severity, source, title, detail_json, "
            "delivered, delivery_error) VALUES (?,?,?,?,?,?,?)",
            (ts, severity, source, title, json.dumps(detail, default=str),
             int(delivered), err),
        )
        alert_id = cur.lastrowid

    print(f"[alert:{severity}] {source}: {title} {json.dumps(detail, default=str)}",
          flush=True)
    return {
        "id": alert_id, "ts": ts, "severity": severity, "source": source,
        "title": title, "detail": detail, "delivered": delivered,
        "delivery_error": err,
    }


def recent(limit: int = 50, severity: Optional[str] = None, db_path=None) -> list[dict]:
    init(db_path)
    sql = "SELECT * FROM alerts"
    params: list[Any] = []
    if severity:
        sql += " WHERE severity = ?"
        params.append(severity)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with storage.connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["detail"] = json.loads(r.pop("detail_json") or "{}")
        r["delivered"] = bool(r["delivered"])
    return rows


def summary(db_path=None) -> dict:
    """Counts by severity plus the newest CRITICAL, for the health endpoint."""
    init(db_path)
    with storage.connect(db_path) as conn:
        counts = {
            r["severity"]: r["n"] for r in conn.execute(
                "SELECT severity, COUNT(*) AS n FROM alerts GROUP BY severity"
            )
        }
        row = conn.execute(
            "SELECT ts, source, title FROM alerts WHERE severity='CRITICAL' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        "counts": {s: int(counts.get(s, 0)) for s in SEVERITIES},
        "webhook_configured": bool(os.environ.get(WEBHOOK_ENV, "").strip()),
        "latest_critical": dict(row) if row else None,
    }
