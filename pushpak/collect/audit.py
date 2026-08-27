"""Component 1 — the append-only request ledger.

Every outbound HTTP request, *including the ones we refuse to make*, is written
here with: source, timestamp, host, URL pattern, the robots verdict and rule
that applied, the delay we waited, and the outcome. Blocked requests are the
most important rows in the table — they are the proof the ethical gate fires.

Written to two places on purpose:
  * Postgres -> queryable, joins to fare_quotes via audit_request_id
  * JSONL   -> append-only text, survives DB loss, greppable in a demo
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional

from .. import config, storage
from ..schema import utc_now_iso

AUDIT_COLUMNS = (
    "request_id", "ts", "source", "host", "url", "url_pattern", "method",
    "user_agent", "robots_checked", "robots_allowed", "robots_rule",
    "crawl_delay_s", "waited_s", "http_status", "response_bytes",
    "latency_ms", "outcome", "error", "run_id",
)


class AuditLog:
    def __init__(self, run_id: str, db_url: Optional[str] = None,
                 jsonl_path: Optional[Path] = None):
        self.run_id = run_id
        self.db_url = db_url or config.DB_URL
        self.jsonl_path = Path(jsonl_path or config.AUDIT_LOG_PATH)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._buffer: list[dict[str, Any]] = []

    def record(
        self, *, source: str, host: str, url_pattern: str, outcome: str,
        url: str | None = None, method: str = "GET",
        robots_checked: bool = True, robots_allowed: bool | None = None,
        robots_rule: str | None = None, crawl_delay_s: float | None = None,
        waited_s: float | None = None, http_status: int | None = None,
        response_bytes: int | None = None, latency_ms: float | None = None,
        error: str | None = None,
    ) -> str:
        rec = {
            "request_id": uuid.uuid4().hex[:16],
            "ts": utc_now_iso(),
            "source": source,
            "host": host,
            "url": url,
            "url_pattern": url_pattern,
            "method": method,
            "user_agent": config.USER_AGENT,
            "robots_checked": int(robots_checked),
            "robots_allowed": None if robots_allowed is None else int(robots_allowed),
            "robots_rule": robots_rule,
            "crawl_delay_s": crawl_delay_s,
            "waited_s": waited_s,
            "http_status": http_status,
            "response_bytes": response_bytes,
            "latency_ms": latency_ms,
            "outcome": outcome,
            "error": error,
            "run_id": self.run_id,
        }
        self._buffer.append(rec)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        return rec["request_id"]

    def flush(self) -> int:
        n = storage.write_audit(self._buffer, self.db_url)
        self._buffer.clear()
        return n

    def summary(self) -> dict[str, Any]:
        by_outcome: dict[str, int] = {}
        for r in self._buffer:
            by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        return {"requests": len(self._buffer), "by_outcome": by_outcome}
