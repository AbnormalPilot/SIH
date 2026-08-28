"""Component 1 — a thin, cached client for the Brave Search API.

Brave is used here for exactly one thing: finding *addresses to audit*. It
returns titles, URLs and snippets, not structured fares, so nothing it says can
reach the index. The fare path remains the licensed API in
``collect/official_api.py``.

Three properties matter more than features:

  * **It is optional.** With no ``BRAVE_SEARCH_API_KEY`` the client reports
    itself unconfigured and every caller degrades to the curated source list.
    ``pushpak.cli robots-audit`` and the whole test suite must keep working on
    a machine with no key and no network.
  * **It is cheap.** The free tier is one query per second and 2,000 a month.
    Responses are cached on disk by query, so re-running discovery during a
    demo costs nothing and the rate limiter enforces the published floor.
  * **It is auditable.** Every query issued is recorded with its cache status,
    so the discovery snapshot says where its domains came from.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from .. import config
from .ratelimit import RateLimiter


@dataclass(frozen=True)
class BraveResult:
    """One web result. Only the fields discovery actually reads."""
    title: str
    url: str
    description: str
    query: str


class BraveUnconfigured(RuntimeError):
    """Raised when a caller demands Brave and no API key is set."""


def api_key() -> Optional[str]:
    key = os.environ.get(config.BRAVE_API_KEY_ENV, "").strip()
    return key or None


def is_configured() -> bool:
    return api_key() is not None


def status() -> dict:
    """Reported by /api/v1/sources so the UI can say why discovery is empty."""
    return {
        "configured": is_configured(),
        "env_var": config.BRAVE_API_KEY_ENV,
        "endpoint": config.BRAVE_ENDPOINT,
        "role": (
            "Finds fare pages to run the robots.txt gate against. Brave returns "
            "web results, not fares, so it can never be a source of prices."
        ),
        "free_tier": "1 query/second, 2000 queries/month",
    }


class BraveClient:
    """Cached, rate-limited Brave Web Search."""

    def __init__(
        self,
        key: Optional[str] = None,
        cache_dir: Path | None = None,
        ttl: float = config.BRAVE_CACHE_TTL_SECONDS,
        timeout: float = 15.0,
        limiter: RateLimiter | None = None,
    ):
        self._key = key if key is not None else api_key()
        self.cache_dir = Path(cache_dir or config.BRAVE_CACHE_DIR)
        self.ttl = ttl
        self.timeout = timeout
        self._limiter = limiter or RateLimiter(
            default_interval=config.BRAVE_MIN_SECONDS_BETWEEN_QUERIES, jitter=0.1
        )
        # Recorded for the discovery snapshot: one entry per query issued.
        self.log: list[dict] = []

    # ------------------------------------------------------------------
    def _cache_path(self, query: str, count: int) -> Path:
        digest = hashlib.sha256(
            f"{query}|{count}|{config.BRAVE_COUNTRY}".encode()
        ).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        if self.ttl and (time.time() - path.stat().st_mtime) > self.ttl:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            # A truncated cache file is not worth a traceback; refetch instead.
            return None

    def _write_cache(self, path: Path, payload: dict) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        except OSError:
            pass  # a read-only data dir costs a cache hit, not a run

    # ------------------------------------------------------------------
    def _fetch(self, query: str, count: int) -> dict:
        """One live call. Separated so tests can replace exactly this."""
        self._limiter.wait("api.search.brave.com")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._key or "",
            "User-Agent": config.USER_AGENT,
        }
        params = {
            "q": query,
            "count": count,
            "country": config.BRAVE_COUNTRY,
            "safesearch": "moderate",
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(config.BRAVE_ENDPOINT, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    def search(
        self, query: str, count: int = config.BRAVE_RESULTS_PER_QUERY
    ) -> list[BraveResult]:
        if not self._key:
            raise BraveUnconfigured(
                f"{config.BRAVE_API_KEY_ENV} is not set. "
                "Get a free key at https://api-dashboard.search.brave.com/"
            )

        path = self._cache_path(query, count)
        payload = self._read_cache(path)
        cached = payload is not None
        error: Optional[str] = None

        if not cached:
            try:
                payload = self._fetch(query, count)
                self._write_cache(path, payload)
            except Exception as exc:  # noqa: BLE001 — a failed query is not fatal
                error = f"{type(exc).__name__}: {exc}"
                payload = {}

        results = [
            BraveResult(
                title=(r.get("title") or "").strip(),
                url=(r.get("url") or "").strip(),
                description=(r.get("description") or "").strip(),
                query=query,
            )
            for r in ((payload or {}).get("web") or {}).get("results", [])
            if r.get("url")
        ]

        self.log.append({
            "query": query,
            "cached": cached,
            "n_results": len(results),
            "error": error,
        })
        return results
