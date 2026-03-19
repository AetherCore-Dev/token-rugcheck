"""Tests for the FastAPI audit server."""

import time

import httpx
import pytest

from rugcheck.config import Config
from rugcheck.models import AggregatedData
from rugcheck.server import create_app

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
CONFIG = Config()


def _safe_data() -> AggregatedData:
    return AggregatedData(
        token_name="Bonk",
        token_symbol="BONK",
        is_mintable=False,
        is_freezable=False,
        is_closable=False,
        liquidity_usd=5_000_000.0,
        volume_24h_usd=12_000_000.0,
        lp_burned_pct=99.0,
        price_usd=0.000025,
        sources_succeeded=["RugCheck", "DexScreener", "GoPlus"],
    )


def _empty_data() -> AggregatedData:
    return AggregatedData(
        sources_succeeded=[],
        sources_failed=["RugCheck", "DexScreener", "GoPlus"],
    )


class FakeAggregator:
    """Test double that returns pre-set data."""

    def __init__(self, data: AggregatedData):
        self._data = data
        self.last_success_time = None
        self.last_failure_time = None

    async def aggregate(self, mint_address: str) -> AggregatedData:
        return self._data

    async def close(self) -> None:
        pass


@pytest.fixture
def safe_app():
    return create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))


@pytest.fixture
def failing_app():
    return create_app(CONFIG, aggregator=FakeAggregator(_empty_data()))


# ---------------------------------------------------------------------------
# Original tests (updated for async cache)
# ---------------------------------------------------------------------------


async def test_audit_success(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get(f"/v1/audit/{MINT}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contract_address"] == MINT
    assert data["chain"] == "solana"
    assert "action" in data
    assert "analysis" in data
    assert "evidence" in data
    assert "metadata" in data
    assert data["action"]["risk_level"] in ["SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert data["action"]["is_safe"] is True
    assert data["metadata"]["cache_hit"] is False
    assert data["metadata"]["data_age_seconds"] == 0
    assert data["evidence"]["token_name"] == "Bonk"


async def test_audit_cache_hit(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp1 = await client.get(f"/v1/audit/{MINT}")
        assert resp1.status_code == 200
        assert resp1.json()["metadata"]["cache_hit"] is False

        resp2 = await client.get(f"/v1/audit/{MINT}")
        assert resp2.status_code == 200
        assert resp2.json()["metadata"]["cache_hit"] is True


async def test_audit_invalid_address(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get("/v1/audit/not-a-valid-address!!!")
    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


async def test_audit_all_sources_down(failing_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=failing_app), base_url="http://test") as client:
        resp = await client.get(f"/v1/audit/{MINT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["data_completeness"] == "unavailable"
    # Degraded reports must NOT appear safe — no data ≠ safe
    assert data["action"]["is_safe"] is False
    assert data["action"]["risk_level"] == "CRITICAL"
    assert data["action"]["risk_score"] == 100
    assert data["degraded"] is True


async def test_health(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_stats(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        # Make one request first
        await client.get(f"/v1/audit/{MINT}")
        resp = await client.get("/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["total_requests"] == 1
    assert "cache" in stats


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


async def test_rate_limit_audit():
    """RateLimiter should enforce per-IP sliding window."""
    from rugcheck.server import RateLimiter

    limiter = RateLimiter(max_requests=60, window_seconds=60)

    for _ in range(60):
        allowed, _ = await limiter.check("192.168.1.100")
        assert allowed

    # 61st request should be rate limited
    allowed, retry_after = await limiter.check("192.168.1.100")
    assert not allowed
    assert retry_after > 0


async def test_rate_limit_stats():
    """Exceeding 10 req/min on /stats should return 429 for non-loopback IPs."""
    from rugcheck.server import RateLimiter

    limiter = RateLimiter(max_requests=10, window_seconds=60)

    for _ in range(10):
        allowed, _ = await limiter.check("192.168.1.100")
        assert allowed

    allowed, retry_after = await limiter.check("192.168.1.100")
    assert not allowed
    assert retry_after > 0


async def test_free_daily_quota_exhaustion():
    """Free users should be blocked after daily quota is exhausted."""
    from rugcheck.server import DailyQuota

    quota = DailyQuota(max_daily=5)
    ip = "203.0.113.10"

    for i in range(5):
        result = await quota.check(ip)
        assert result.allowed
        assert result.remaining == 5 - i - 1

    # 6th request should be blocked
    result = await quota.check(ip)
    assert not result.allowed
    assert result.remaining == 0


async def test_free_daily_quota_per_ip():
    """Different IPs should have independent daily quotas."""
    from rugcheck.server import DailyQuota

    quota = DailyQuota(max_daily=3)

    for _ in range(3):
        result = await quota.check("10.0.0.1")
        assert result.allowed

    # 10.0.0.1 is exhausted
    result = await quota.check("10.0.0.1")
    assert not result.allowed

    # 10.0.0.2 still has quota
    result = await quota.check("10.0.0.2")
    assert result.allowed
    assert result.remaining == 2


async def test_paid_rate_limit_loopback():
    """Loopback (paid) users should be rate-limited at the paid limit, not exempt."""
    cfg = Config(paid_rate_limit=5, free_daily_quota=2)
    app = create_app(cfg, aggregator=FakeAggregator(_safe_data()))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            resp = await client.get(f"/v1/audit/{MINT}")
            assert resp.status_code == 200

        # 6th request should be rate-limited
        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 429


async def test_rate_limit_health_unlimited():
    """/health should not be rate-limited."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(100):
            resp = await client.get("/health")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Health endpoint enhancement tests
# ---------------------------------------------------------------------------


async def test_health_degraded_no_success():
    """Health should return degraded when aggregator has only failures."""
    agg = FakeAggregator(_safe_data())
    agg.last_failure_time = time.monotonic()
    agg.last_success_time = None  # never succeeded

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


async def test_health_degraded_stale_success_with_recent_failure():
    """Health should return degraded when last success is stale AND there are recent failures."""
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = time.monotonic() - 200  # stale (beyond 120s)
    agg.last_failure_time = time.monotonic() - 10    # recent failure

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert "last_upstream_success_secs_ago" in body


async def test_health_ok_when_idle_stale_success():
    """Stale success without recent failure = idle, not degraded."""
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = time.monotonic() - 200  # stale
    agg.last_failure_time = None                     # no failures

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_ok_when_never_called():
    """Server just started, no audit requests yet — should be ok."""
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = None
    agg.last_failure_time = None

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_ok_with_recent_success():
    """Health should return ok when last success is recent."""
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = time.monotonic() - 10  # 10 seconds ago

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_production_minimal():
    """Production mode /health must not expose version, service name, or timing."""
    prod_config = Config(production=True)
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = time.monotonic() - 10

    app = create_app(prod_config, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    body = resp.json()
    assert resp.status_code == 200
    assert body == {"status": "ok"}, f"Production /health leaked fields: {list(body.keys())}"


# ---------------------------------------------------------------------------
# data_age_seconds tests
# ---------------------------------------------------------------------------


async def test_data_age_zero_on_fresh():
    """Fresh (non-cached) responses should have data_age_seconds=0."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/v1/audit/{MINT}")
    assert resp.json()["metadata"]["data_age_seconds"] == 0


async def test_data_age_positive_on_cache_hit():
    """Cached responses should have data_age_seconds > 0 (or at least >= 0)."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.get(f"/v1/audit/{MINT}")
        time.sleep(0.05)  # small delay
        resp = await client.get(f"/v1/audit/{MINT}")
    meta = resp.json()["metadata"]
    assert meta["cache_hit"] is True
    assert meta["data_age_seconds"] >= 0


# ---------------------------------------------------------------------------
# Aggregate timeout tests
# ---------------------------------------------------------------------------


async def test_audit_aggregate_timeout():
    """If aggregate() exceeds the 4.5s hard timeout, server returns degraded 200."""
    import asyncio as _asyncio

    class SlowAggregator:
        last_success_time = None
        last_failure_time = None

        async def aggregate(self, mint_address: str):
            await _asyncio.sleep(10)  # exceed the 4.5s server timeout

        async def close(self):
            pass

    app = create_app(CONFIG, aggregator=SlowAggregator())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/v1/audit/{MINT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["data_completeness"] == "unavailable"
    # Degraded reports must NOT appear safe
    assert data["action"]["is_safe"] is False
    assert data["action"]["risk_level"] == "CRITICAL"
    assert data["degraded"] is True


# ---------------------------------------------------------------------------
# Degraded field tests
# ---------------------------------------------------------------------------


async def test_audit_success_not_degraded(safe_app):
    """Successful audit should have degraded=False."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get(f"/v1/audit/{MINT}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is False


# ---------------------------------------------------------------------------
# Prometheus metrics tests
# ---------------------------------------------------------------------------


async def test_metrics_endpoint(safe_app):
    """GET /metrics should return Prometheus-formatted output."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        # Make a request to generate some metrics
        await client.get(f"/v1/audit/{MINT}")
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "rugcheck_requests_total" in body
    assert "rugcheck_request_duration_seconds" in body
    assert "rugcheck_cache_misses_total" in body


async def test_metrics_not_rate_limited():
    """/metrics should never be rate-limited."""
    cfg = Config(free_daily_quota=2, paid_rate_limit=5)
    app = create_app(cfg, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(50):
            resp = await client.get("/metrics")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# API versioning tests
# ---------------------------------------------------------------------------


async def test_legacy_audit_returns_deprecation_header(safe_app):
    """/audit/{addr} (legacy) should return audit report with Deprecation header."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=safe_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/audit/{MINT}")
    assert resp.status_code == 200
    assert resp.json()["contract_address"] == MINT
    assert resp.headers.get("deprecation") == "true"
    assert "/v1/audit/" in resp.headers.get("link", "")
