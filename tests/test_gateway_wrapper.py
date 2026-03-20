"""Tests for the QuotaAwareGateway wrapper (HTTP proxy architecture).

The gateway proxies free-tier requests to the audit-server via HTTP and
delegates paid requests to the X402Gateway ASGI app in-process.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rugcheck.gateway_wrapper import QuotaAwareGateway, FREE_REQUESTS_TOTAL, FREE_QUOTA_EXHAUSTED_TOTAL
from rugcheck.quota import DailyQuota


# ---------------------------------------------------------------------------
# Helpers: fake audit server + fake 402 gateway
# ---------------------------------------------------------------------------

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _make_fake_audit_app() -> FastAPI:
    """Minimal FastAPI app that mimics the audit-server."""
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/audit/{mint_address}")
    async def audit(mint_address: str):
        return JSONResponse(content={
            "contract_address": mint_address,
            "action": {"risk_score": 25, "risk_level": "LOW", "is_safe": True},
        })

    @app.get("/audit/{mint_address}")
    async def audit_legacy(mint_address: str):
        return JSONResponse(
            content={
                "contract_address": mint_address,
                "action": {"risk_score": 25, "risk_level": "LOW", "is_safe": True},
            },
            headers={"deprecation": "true"},
        )

    return app


def _make_fake_gateway_app() -> FastAPI:
    """Minimal FastAPI app that mimics X402Gateway (always returns 402)."""
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catch_all(path: str):
        return JSONResponse(
            status_code=402,
            content={
                "error": "Payment Required",
                "protocol": "x402",
                "chain": "solana",
                "token": "USDC",
                "amount": "0.02",
            },
        )

    return app


def _build_proxy_client(audit_app: FastAPI) -> httpx.AsyncClient:
    """Build an httpx client that proxies to a fake audit ASGI app."""
    transport = httpx.ASGITransport(app=audit_app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://fake-audit",
        timeout=httpx.Timeout(5.0),
    )


def _build_wrapper_app(
    *,
    quota: int = 3,
    enabled: bool = True,
    audit_app: FastAPI | None = None,
) -> FastAPI:
    """Build a QuotaAwareGateway with a fake gateway + fake audit server."""
    if audit_app is None:
        audit_app = _make_fake_audit_app()
    gateway_app = _make_fake_gateway_app()
    daily_quota = DailyQuota(max_daily=quota)
    proxy_client = _build_proxy_client(audit_app)

    wrapper = QuotaAwareGateway(
        gateway_app=gateway_app,
        target_url="http://fake-audit:8000",
        daily_quota=daily_quota,
        free_quota_enabled=enabled,
        _proxy_client=proxy_client,
    )
    return wrapper.create_app()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wrapper_app():
    return _build_wrapper_app(quota=3, enabled=True)


@pytest.fixture
def disabled_wrapper_app():
    return _build_wrapper_app(quota=3, enabled=False)


# ---------------------------------------------------------------------------
# Free tier: allowed
# ---------------------------------------------------------------------------


async def test_free_request_returns_200_with_remaining_header(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/v1/audit/{MINT}")

    assert resp.status_code == 200
    assert resp.headers.get("x-free-remaining") == "2"
    assert resp.json()["contract_address"] == MINT


async def test_free_request_legacy_path(wrapper_app):
    """Legacy /audit/ path should also get free quota."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/audit/{MINT}")

    assert resp.status_code == 200
    assert "x-free-remaining" in resp.headers


async def test_x_free_remaining_decrements_correctly(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        r1 = await client.get(f"/v1/audit/{MINT}")
        assert r1.headers["x-free-remaining"] == "2"

        r2 = await client.get(f"/v1/audit/{MINT}")
        assert r2.headers["x-free-remaining"] == "1"

        r3 = await client.get(f"/v1/audit/{MINT}")
        assert r3.headers["x-free-remaining"] == "0"


async def test_free_tier_includes_quota_tier_header(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/v1/audit/{MINT}")
    assert resp.headers.get("x-quota-tier") == "free"


# ---------------------------------------------------------------------------
# Free tier: exhausted → falls through to 402
# ---------------------------------------------------------------------------


async def test_free_quota_exhausted_returns_402(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        for _ in range(3):
            resp = await client.get(f"/v1/audit/{MINT}")
            assert resp.status_code == 200

        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 402
        assert resp.json()["error"] == "Payment Required"


# ---------------------------------------------------------------------------
# Health endpoint: always bypasses quota
# ---------------------------------------------------------------------------


async def test_health_always_bypasses_quota(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        for _ in range(3):
            await client.get(f"/v1/audit/{MINT}")

        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Free quota disabled → always 402
# ---------------------------------------------------------------------------


async def test_free_quota_disabled_always_402(disabled_wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=disabled_wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 402


# ---------------------------------------------------------------------------
# Different IPs: independent quotas (unit test on DailyQuota)
# ---------------------------------------------------------------------------


async def test_different_ips_independent_quota():
    quota = DailyQuota(max_daily=2)

    for _ in range(2):
        r = await quota.check("10.0.0.1")
        assert r.allowed

    r = await quota.check("10.0.0.1")
    assert not r.allowed

    r = await quota.check("10.0.0.2")
    assert r.allowed
    assert r.remaining == 1


# ---------------------------------------------------------------------------
# Paid path accessible after free exhaustion
# ---------------------------------------------------------------------------


async def test_paid_path_still_accessible_after_free_exhausted(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        for _ in range(3):
            await client.get(f"/v1/audit/{MINT}")

        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 402
        body = resp.json()
        assert body["protocol"] == "x402"
        assert body["chain"] == "solana"


# ---------------------------------------------------------------------------
# max_daily=0: no free requests at all
# ---------------------------------------------------------------------------


async def test_zero_quota_blocks_immediately():
    quota = DailyQuota(max_daily=0)
    result = await quota.check("10.0.0.1")
    assert not result.allowed
    assert result.remaining == 0


async def test_zero_quota_wrapper_always_402():
    """When quota=0, even the first request should go to payment."""
    app = _build_wrapper_app(quota=0, enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 402


# ---------------------------------------------------------------------------
# Catch-all route
# ---------------------------------------------------------------------------


async def test_catch_all_returns_404_for_unknown_paths(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get("/some/unknown/path")
        assert resp.status_code == 404


async def test_catch_all_returns_404_for_non_audit_paths(wrapper_app):
    """Non-audit paths should return 404, not be forwarded to the payment gateway."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        resp = await client.get("/v2/something")
        assert resp.status_code == 404
        resp2 = await client.get("/random")
        assert resp2.status_code == 404


async def test_catch_all_does_not_consume_quota(wrapper_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        for _ in range(10):
            await client.get("/other/path")

        resp = await client.get(f"/v1/audit/{MINT}")
        assert resp.status_code == 200
        assert resp.headers["x-free-remaining"] == "2"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def test_metrics_counters_increment(wrapper_app):
    before_free = FREE_REQUESTS_TOTAL._value.get()
    before_exhausted = FREE_QUOTA_EXHAUSTED_TOTAL._value.get()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapper_app), base_url="http://test"
    ) as client:
        for _ in range(3):
            await client.get(f"/v1/audit/{MINT}")
        await client.get(f"/v1/audit/{MINT}")

    after_free = FREE_REQUESTS_TOTAL._value.get()
    after_exhausted = FREE_QUOTA_EXHAUSTED_TOTAL._value.get()

    assert after_free - before_free == 3
    assert after_exhausted - before_exhausted == 1


async def test_metrics_not_incremented_when_quota_disabled(disabled_wrapper_app):
    before_free = FREE_REQUESTS_TOTAL._value.get()
    before_exhausted = FREE_QUOTA_EXHAUSTED_TOTAL._value.get()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=disabled_wrapper_app), base_url="http://test"
    ) as client:
        await client.get(f"/v1/audit/{MINT}")

    assert FREE_REQUESTS_TOTAL._value.get() == before_free
    assert FREE_QUOTA_EXHAUSTED_TOTAL._value.get() == before_exhausted


# ---------------------------------------------------------------------------
# Concurrent access safety
# ---------------------------------------------------------------------------


async def test_quota_concurrent_access_does_not_over_grant():
    quota = DailyQuota(max_daily=5)
    results = await asyncio.gather(*[quota.check("10.0.0.1") for _ in range(20)])
    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == 5
