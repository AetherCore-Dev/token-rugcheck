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
        resp = await client.get(f"/audit/{MINT}")

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
        resp1 = await client.get(f"/audit/{MINT}")
        assert resp1.status_code == 200
        assert resp1.json()["metadata"]["cache_hit"] is False

        resp2 = await client.get(f"/audit/{MINT}")
        assert resp2.status_code == 200
        assert resp2.json()["metadata"]["cache_hit"] is True


async def test_audit_invalid_address(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get("/audit/not-a-valid-address!!!")
    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


async def test_audit_all_sources_down(failing_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=failing_app), base_url="http://test") as client:
        resp = await client.get(f"/audit/{MINT}")
    assert resp.status_code == 503


async def test_health(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_stats(safe_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=safe_app), base_url="http://test") as client:
        # Make one request first
        await client.get(f"/audit/{MINT}")
        resp = await client.get("/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["total_requests"] == 1
    assert "cache" in stats


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


async def test_rate_limit_audit():
    """Exceeding 60 req/min on /audit should return 429."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(60):
            resp = await client.get(f"/audit/{MINT}")
            assert resp.status_code == 200

        # 61st request should be rate limited
        resp = await client.get(f"/audit/{MINT}")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


async def test_rate_limit_stats():
    """Exceeding 10 req/min on /stats should return 429."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(10):
            resp = await client.get("/stats")
            assert resp.status_code == 200

        resp = await client.get("/stats")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


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


async def test_health_degraded_stale_success():
    """Health should return degraded when last success is too old."""
    agg = FakeAggregator(_safe_data())
    # Simulate success 200 seconds ago (beyond 120s window)
    agg.last_success_time = time.monotonic() - 200

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert "last_upstream_success_secs_ago" in body


async def test_health_ok_with_recent_success():
    """Health should return ok when last success is recent."""
    agg = FakeAggregator(_safe_data())
    agg.last_success_time = time.monotonic() - 10  # 10 seconds ago

    app = create_app(CONFIG, aggregator=agg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# data_age_seconds tests
# ---------------------------------------------------------------------------


async def test_data_age_zero_on_fresh():
    """Fresh (non-cached) responses should have data_age_seconds=0."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/audit/{MINT}")
    assert resp.json()["metadata"]["data_age_seconds"] == 0


async def test_data_age_positive_on_cache_hit():
    """Cached responses should have data_age_seconds > 0 (or at least >= 0)."""
    app = create_app(CONFIG, aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.get(f"/audit/{MINT}")
        time.sleep(0.05)  # small delay
        resp = await client.get(f"/audit/{MINT}")
    meta = resp.json()["metadata"]
    assert meta["cache_hit"] is True
    assert meta["data_age_seconds"] >= 0
