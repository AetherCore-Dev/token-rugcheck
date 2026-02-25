"""In-memory TTL cache for audit reports."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from rugcheck.models import AuditReport


class TTLCache:
    """Simple in-memory cache with per-entry TTL and LRU eviction.

    All mutating operations are guarded by an asyncio.Lock to prevent
    race conditions when multiple coroutines access the cache concurrently.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 10_000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[str, tuple[float, AuditReport]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[AuditReport | None, float]:
        """Return (report, data_age_seconds) or (None, 0) on miss."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None, 0

            ts, report = entry
            age = time.monotonic() - ts
            if age > self.ttl:
                del self._store[key]
                self._misses += 1
                return None, 0

            # Move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return report, age

    async def set(self, key: str, report: AuditReport) -> None:
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic(), report)

            # Evict oldest if over capacity
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(self._hits + self._misses, 1) * 100, 1),
        }
