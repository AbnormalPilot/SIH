"""Component 1 — per-host rate limiting.

A simple, auditable minimum-interval limiter with jitter. Deliberately not a
token bucket: bursts are exactly what we must not send to an airline's booking
engine, so the limiter enforces a *floor between consecutive requests* rather
than an average rate.

The effective interval is ``max(source floor, robots.txt Crawl-delay)`` — the
site's own instruction always wins when it is stricter.
"""
from __future__ import annotations

import random
import threading
import time
from collections import defaultdict

from .. import config


class RateLimiter:
    def __init__(self, default_interval: float = config.DEFAULT_CRAWL_DELAY,
                 jitter: float = 0.25, seed: int | None = None):
        self.default_interval = default_interval
        self.jitter = jitter
        self._last: dict[str, float] = defaultdict(float)
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self._clock = time.monotonic
        self._sleep = time.sleep

    def wait(self, host: str, interval: float | None = None) -> float:
        """Block until it is polite to hit ``host``. Returns seconds waited."""
        with self._lock:
            base = float(interval if interval is not None else self.default_interval)
            base *= 1.0 + self._rng.uniform(0.0, self.jitter)
            now = self._clock()
            due = self._last[host] + base
            waited = max(0.0, due - now)
            if waited > 0:
                self._sleep(waited)
            self._last[host] = self._clock()
            self._counts[host] += 1
            return waited

    def count(self, host: str) -> int:
        return self._counts[host]

    def budget_exhausted(self, host: str,
                         cap: int = config.MAX_REQUESTS_PER_SOURCE_PER_RUN) -> bool:
        """Hard per-run cap so a bug cannot turn into a hammering incident."""
        return self._counts[host] >= cap
