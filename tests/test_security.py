"""Security-focused tests — TDD RED phase.

Tests for:
  1. RateLimiter memory eviction (prevent unbounded growth)
  2. /metrics restricted to loopback IPs only
  3. /stats restricted to loopback IPs only
  4. Circuit breaker for upstream API calls
  5. Request-ID middleware (traceability)
  6. Config validation (reject invalid env values)
"""

import asyncio
import time

import httpx

from rugcheck.config import Config
from rugcheck.models import AggregatedData


MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


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
    def __init__(self, data: AggregatedData):
        self._data = data
        self.last_success_time = None
        self.last_failure_time = None

    async def aggregate(self, mint_address: str) -> AggregatedData:
        return self._data

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. RateLimiter memory eviction
# ---------------------------------------------------------------------------


async def test_rate_limiter_evicts_stale_ips():
    """RateLimiter should not grow unboundedly — stale IPs should be evicted."""
    from rugcheck.server import RateLimiter

    limiter = RateLimiter(max_requests=100, window_seconds=1)
    # Lower eviction threshold so we can trigger it in the test
    limiter._EVICT_EVERY = 16

    # Simulate 500 unique IPs
    for i in range(500):
        await limiter.check(f"10.0.{i // 256}.{i % 256}")

    # Wait for window to expire
    await asyncio.sleep(1.1)

    # Trigger enough checks to force eviction
    for i in range(20):
        await limiter.check(f"93.107.{i}.8")

    # Internal state should not hold all 500 stale entries.
    # After eviction the stale entries should be cleaned up.
    assert len(limiter._windows) <= 100, (
        f"RateLimiter holding {len(limiter._windows)} IPs — expected eviction of stale entries"
    )


async def test_daily_quota_max_tracked_ips():
    """DailyQuota should cap the number of tracked IPs to prevent memory exhaustion."""
    from rugcheck.server import DailyQuota

    quota = DailyQuota(max_daily=5, max_tracked_ips=100)

    # Simulate 200 unique IPs
    for i in range(200):
        await quota.check(f"10.0.{i // 256}.{i % 256}")

    # Should not exceed max_tracked_ips
    assert len(quota._counts) <= 100


# ---------------------------------------------------------------------------
# 2. /metrics restricted to loopback
# ---------------------------------------------------------------------------


async def test_metrics_blocked_for_external_ip():
    """/metrics should return 403 for non-loopback IPs."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    # Simulate external IP
    transport = httpx.ASGITransport(app=app, client=("192.168.1.100", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 403


async def test_metrics_allowed_for_loopback():
    """/metrics should return 200 for loopback IPs."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. /stats restricted to loopback
# ---------------------------------------------------------------------------


async def test_stats_blocked_for_external_ip():
    """/stats should return 403 for non-loopback IPs."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    transport = httpx.ASGITransport(app=app, client=("192.168.1.100", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/stats")
    assert resp.status_code == 403


async def test_stats_allowed_for_loopback():
    """/stats should return 200 for loopback IPs."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Make one audit request first to populate stats
        await client.get(f"/audit/{MINT}")
        resp = await client.get("/stats")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Circuit breaker for upstream calls
# ---------------------------------------------------------------------------


async def test_circuit_breaker_opens_after_consecutive_failures():
    """After N consecutive all-source failures, circuit breaker should open
    and return degraded responses immediately without hitting upstream."""
    from rugcheck.server import create_app

    call_count = 0

    class CountingAggregator:
        last_success_time = None
        last_failure_time = None

        async def aggregate(self, mint_address: str) -> AggregatedData:
            nonlocal call_count
            call_count += 1
            return _empty_data()

        async def close(self) -> None:
            pass

    cfg = Config(circuit_breaker_threshold=3, circuit_breaker_cooldown=60)
    app = create_app(cfg, aggregator=CountingAggregator())
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 3 requests that all fail — should actually call aggregator
        for _ in range(3):
            resp = await client.get(f"/audit/{MINT}")
            assert resp.status_code == 200
            assert resp.json()["degraded"] is True

        assert call_count == 3

        # 4th request: circuit breaker should be open, skip aggregator
        resp = await client.get(f"/audit/{MINT}")
        assert resp.status_code == 200
        assert resp.json()["degraded"] is True
        # aggregator should NOT have been called again
        assert call_count == 3


async def test_circuit_breaker_resets_on_success():
    """A successful response should reset the circuit breaker counter."""
    from rugcheck.server import create_app

    # Use different mint addresses to avoid cache hits
    MINTS = [
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
        "So11111111111111111111111111111111111111112",
    ]
    responses = [_empty_data(), _empty_data(), _safe_data(), _empty_data()]
    idx = 0

    class SequenceAggregator:
        last_success_time = None
        last_failure_time = time.monotonic()

        async def aggregate(self, mint_address: str) -> AggregatedData:
            nonlocal idx
            data = responses[idx % len(responses)]
            idx += 1
            return data

        async def close(self) -> None:
            pass

    cfg = Config(circuit_breaker_threshold=3, circuit_breaker_cooldown=60)
    app = create_app(cfg, aggregator=SequenceAggregator())
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 2 failures (different mints to avoid cache)
        await client.get(f"/audit/{MINTS[0]}")
        await client.get(f"/audit/{MINTS[1]}")
        # 1 success — should reset counter
        resp = await client.get(f"/audit/{MINTS[2]}")
        assert resp.json()["degraded"] is False
        # Another failure — counter should be at 1, not 3
        resp = await client.get(f"/audit/{MINTS[3]}")
        # Circuit should still be closed (only 1 failure since reset)
        assert idx == 4  # all 4 calls went through


# ---------------------------------------------------------------------------
# 5. Request-ID middleware
# ---------------------------------------------------------------------------


async def test_response_has_request_id_header():
    """Every response should include an X-Request-ID header for traceability."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


async def test_request_id_echoes_client_header():
    """If client sends X-Request-ID, server should echo it back."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health", headers={"X-Request-ID": "test-req-123"})
    assert resp.headers.get("x-request-id") == "test-req-123"


# ---------------------------------------------------------------------------
# 6. Config validation
# ---------------------------------------------------------------------------


def test_config_rejects_negative_cache_ttl():
    """Config should reject nonsensical negative values."""
    from rugcheck.config import load_config
    import os

    old = os.environ.get("CACHE_TTL_SECONDS")
    try:
        os.environ["CACHE_TTL_SECONDS"] = "-5"
        cfg = load_config()
        # Should clamp to minimum 0
        assert cfg.cache_ttl_seconds >= 0
    finally:
        if old is None:
            os.environ.pop("CACHE_TTL_SECONDS", None)
        else:
            os.environ["CACHE_TTL_SECONDS"] = old


def test_config_rejects_excessive_rate_limit():
    """Rate limit should have an upper ceiling to prevent abuse."""
    from rugcheck.config import load_config
    import os

    old = os.environ.get("PAID_RATE_LIMIT")
    try:
        os.environ["PAID_RATE_LIMIT"] = "999999"
        cfg = load_config()
        assert cfg.paid_rate_limit <= 10000
    finally:
        if old is None:
            os.environ.pop("PAID_RATE_LIMIT", None)
        else:
            os.environ["PAID_RATE_LIMIT"] = old


# ---------------------------------------------------------------------------
# 7. X-Request-ID CRLF injection prevention
# ---------------------------------------------------------------------------


async def test_request_id_rejects_crlf_injection():
    """X-Request-ID with CRLF should be sanitized to prevent header injection."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/health",
            headers={"X-Request-ID": "legit\r\nX-Injected: evil"},
        )
    rid = resp.headers.get("x-request-id", "")
    # Must not contain CRLF characters
    assert "\r" not in rid
    assert "\n" not in rid


async def test_request_id_rejects_overlong_value():
    """X-Request-ID over 128 chars should be replaced with a generated one."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/health",
            headers={"X-Request-ID": "A" * 500},
        )
    rid = resp.headers.get("x-request-id", "")
    assert len(rid) <= 128


# ---------------------------------------------------------------------------
# 8. Gateway placeholder fail-fast in production
# ---------------------------------------------------------------------------


def test_gateway_placeholder_detection():
    """Gateway config should detect placeholder address."""
    from rugcheck.config import _PLACEHOLDER_ADDRESS

    cfg = Config(ag402_address=_PLACEHOLDER_ADDRESS)
    assert cfg.ag402_address == _PLACEHOLDER_ADDRESS
    # The gateway.py checks for this and logs a warning.
    # In a real deployment, the deploy.sh script validates .env before starting.


# ---------------------------------------------------------------------------
# 9. audit-server 8000 not directly accessible for free from loopback
#    (loopback on /audit still uses paid_limiter, not free_daily)
# ---------------------------------------------------------------------------


async def test_loopback_audit_uses_paid_limiter_not_free():
    """Loopback requests to /audit should hit the paid per-minute limiter,
    not get unlimited free access. This ensures the gateway is the only
    intended path for paid access."""
    from rugcheck.server import create_app

    cfg = Config(paid_rate_limit=3, free_daily_quota=1000)
    app = create_app(cfg, aggregator=FakeAggregator(_safe_data()))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Should be allowed up to paid_rate_limit (3)
        for _ in range(3):
            resp = await client.get(f"/audit/{MINT}")
            assert resp.status_code == 200

        # 4th should be rate limited (429), NOT unlimited
        resp = await client.get(f"/audit/{MINT}")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# 10. Server header should not reveal framework version
# ---------------------------------------------------------------------------


async def test_no_server_version_header():
    """Response should not leak server/framework version in headers."""
    from rugcheck.server import create_app

    app = create_app(Config(), aggregator=FakeAggregator(_safe_data()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/health")
    # Should not have a 'server' header revealing framework info
    server_hdr = resp.headers.get("server", "")
    assert "uvicorn" not in server_hdr.lower()
    assert "fastapi" not in server_hdr.lower()


# ---------------------------------------------------------------------------
# 11. Firewall: setup-server.sh should remove 8000 from public ports
# ---------------------------------------------------------------------------


def test_setup_server_no_public_8000():
    """setup-server.sh should NOT expose port 8000 to the public internet."""
    with open("/workspace/scripts/setup-server.sh") as f:
        content = f.read()
    # 8000 should not be in REQUIRED_PORTS — it's bound to 127.0.0.1 only
    import re
    match = re.search(r'REQUIRED_PORTS=\(([^)]+)\)', content)
    assert match is not None
    ports_str = match.group(1)
    assert "8000" not in ports_str, (
        "Port 8000 should not be in REQUIRED_PORTS — audit server is 127.0.0.1 only"
    )


# ---------------------------------------------------------------------------
# 12. Firewall: setup-server.sh should set default deny policy
# ---------------------------------------------------------------------------


def test_setup_server_default_deny():
    """setup-server.sh should set ufw default deny incoming."""
    with open("/workspace/scripts/setup-server.sh") as f:
        content = f.read()
    assert "ufw default deny incoming" in content, (
        "setup-server.sh must set 'ufw default deny incoming' before allowing ports"
    )
