"""Tests for TTL cache."""

import asyncio
import time

from rugcheck.cache import TTLCache
from rugcheck.models import (
    ActionLayer,
    AnalysisLayer,
    AuditMetadata,
    AuditReport,
    EvidenceLayer,
    RiskLevel,
)


def _make_report(mint: str = "TESTMINT") -> AuditReport:
    return AuditReport(
        contract_address=mint,
        action=ActionLayer(is_safe=True, risk_level=RiskLevel.SAFE, risk_score=0),
        analysis=AnalysisLayer(summary="Safe"),
        evidence=EvidenceLayer(),
        metadata=AuditMetadata(data_sources=["RugCheck"]),
    )


async def test_set_and_get():
    cache = TTLCache(ttl_seconds=60)
    report = _make_report()
    await cache.set("key1", report)
    result, age = await cache.get("key1")
    assert result is not None
    assert result.contract_address == "TESTMINT"
    assert age >= 0


async def test_miss_returns_none():
    cache = TTLCache()
    result, age = await cache.get("nonexistent")
    assert result is None
    assert age == 0


async def test_ttl_expiry():
    cache = TTLCache(ttl_seconds=0)  # expires immediately
    await cache.set("key1", _make_report())
    time.sleep(0.01)
    result, _ = await cache.get("key1")
    assert result is None


async def test_max_size_eviction():
    cache = TTLCache(ttl_seconds=60, max_size=2)
    await cache.set("a", _make_report("A"))
    await cache.set("b", _make_report("B"))
    await cache.set("c", _make_report("C"))

    # "a" should be evicted
    result_a, _ = await cache.get("a")
    result_b, _ = await cache.get("b")
    result_c, _ = await cache.get("c")
    assert result_a is None
    assert result_b is not None
    assert result_c is not None


async def test_stats():
    cache = TTLCache(ttl_seconds=60)
    await cache.set("k", _make_report())

    await cache.get("k")       # hit
    await cache.get("missing")  # miss

    s = cache.stats
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 50.0
    assert s["size"] == 1


async def test_data_age_increases():
    """Cache hit should report increasing data age."""
    cache = TTLCache(ttl_seconds=60)
    await cache.set("k", _make_report())
    time.sleep(0.05)
    _, age = await cache.get("k")
    assert age >= 0.04  # at least ~50ms of age


async def test_concurrent_access():
    """Multiple coroutines reading/writing concurrently should not raise."""
    cache = TTLCache(ttl_seconds=60, max_size=100)

    async def writer(n: int):
        for i in range(20):
            await cache.set(f"key-{n}-{i}", _make_report(f"M{n}{i}"))

    async def reader(n: int):
        for i in range(20):
            await cache.get(f"key-{n}-{i}")

    tasks = []
    for n in range(5):
        tasks.append(asyncio.create_task(writer(n)))
        tasks.append(asyncio.create_task(reader(n)))

    await asyncio.gather(*tasks)

    # No exception = success; verify stats are sane
    s = cache.stats
    assert s["size"] <= 100
    assert s["hits"] + s["misses"] > 0
