"""Tests for the shared quota module (extracted from server.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rugcheck.quota import DailyQuota, QuotaResult, resolve_client_ip, TRUSTED_PROXY_NETWORKS


# ---------------------------------------------------------------------------
# QuotaResult (frozen dataclass)
# ---------------------------------------------------------------------------


def test_quota_result_is_immutable():
    result = QuotaResult(allowed=True, remaining=5)
    assert result.allowed is True
    assert result.remaining == 5
    with pytest.raises(AttributeError):
        result.allowed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DailyQuota — basic behavior
# ---------------------------------------------------------------------------


async def test_quota_allows_within_limit():
    quota = DailyQuota(max_daily=5)
    for i in range(5):
        result = await quota.check("10.0.0.1")
        assert result.allowed
        assert result.remaining == 5 - i - 1


async def test_quota_blocks_after_exhaustion():
    quota = DailyQuota(max_daily=3)
    for _ in range(3):
        result = await quota.check("10.0.0.1")
        assert result.allowed

    result = await quota.check("10.0.0.1")
    assert not result.allowed
    assert result.remaining == 0


async def test_quota_independent_per_ip():
    quota = DailyQuota(max_daily=2)

    # Exhaust IP A
    for _ in range(2):
        await quota.check("1.1.1.1")
    result_a = await quota.check("1.1.1.1")
    assert not result_a.allowed

    # IP B still has quota
    result_b = await quota.check("2.2.2.2")
    assert result_b.allowed
    assert result_b.remaining == 1


async def test_quota_resets_on_new_day():
    quota = DailyQuota(max_daily=1)

    result = await quota.check("10.0.0.1")
    assert result.allowed

    result = await quota.check("10.0.0.1")
    assert not result.allowed

    # Simulate next day
    with patch("rugcheck.quota._today", return_value="2099-01-02"):
        result = await quota.check("10.0.0.1")
        assert result.allowed


async def test_quota_max_tracked_ips_cap():
    quota = DailyQuota(max_daily=100, max_tracked_ips=5)

    # Fill up 5 IPs
    for i in range(5):
        await quota.check(f"10.0.0.{i}")

    # 6th IP should still work (eviction kicks in)
    result = await quota.check("10.0.0.99")
    assert result.allowed


async def test_quota_evict_stale_entries():
    quota = DailyQuota(max_daily=10)

    # Add entries for "today"
    await quota.check("10.0.0.1")
    await quota.check("10.0.0.2")

    # Evict with a different "today" — all entries become stale
    with patch("rugcheck.quota._today", return_value="2099-12-31"):
        evicted = await quota.evict_stale()
        assert evicted == 2


async def test_quota_unknown_ip_treated_as_single_bucket():
    quota = DailyQuota(max_daily=1)

    result = await quota.check("")
    assert result.allowed

    result = await quota.check("unknown")
    assert not result.allowed  # Same bucket as ""


async def test_quota_zero_max_daily_blocks_immediately():
    """When max_daily=0, no requests should ever be allowed."""
    quota = DailyQuota(max_daily=0)
    result = await quota.check("10.0.0.1")
    assert not result.allowed
    assert result.remaining == 0


async def test_quota_concurrent_access_does_not_over_grant():
    """Concurrent checkers must not grant more than max_daily allows."""
    import asyncio
    quota = DailyQuota(max_daily=5)
    results = await asyncio.gather(*[quota.check("10.0.0.1") for _ in range(20)])
    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == 5


# ---------------------------------------------------------------------------
# resolve_client_ip
# ---------------------------------------------------------------------------


def _make_request(socket_ip: str, headers: dict | None = None):
    """Build a minimal mock Request for resolve_client_ip."""
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client.host = socket_ip
    req.headers = headers or {}
    return req


def test_resolve_ip_from_cf_header_trusted_proxy():
    """CF-Connecting-IP should be used when socket peer is loopback (trusted)."""
    req = _make_request("127.0.0.1", {"cf-connecting-ip": "203.0.113.50"})
    assert resolve_client_ip(req) == "203.0.113.50"


def test_resolve_ip_from_xff_trusted_proxy():
    """X-Forwarded-For leftmost should be used when CF header absent."""
    req = _make_request("127.0.0.1", {"x-forwarded-for": "198.51.100.1, 10.0.0.1"})
    assert resolve_client_ip(req) == "198.51.100.1"


def test_resolve_ip_ignores_untrusted_proxy():
    """Proxy headers from untrusted peers should be ignored."""
    req = _make_request("192.168.1.100", {"cf-connecting-ip": "1.2.3.4"})
    assert resolve_client_ip(req) == "192.168.1.100"


def test_resolve_ip_direct_connection():
    """No proxy headers → return socket IP."""
    req = _make_request("203.0.113.99")
    assert resolve_client_ip(req) == "203.0.113.99"


def test_resolve_ip_no_client():
    """request.client is None → return 'unknown'."""
    from unittest.mock import MagicMock

    req = MagicMock()
    req.client = None
    req.headers = {}
    assert resolve_client_ip(req) == "unknown"


def test_resolve_ip_malformed_cf_header_falls_through_to_xff():
    """A garbage CF-Connecting-IP should fall through to X-Forwarded-For."""
    req = _make_request("127.0.0.1", {
        "cf-connecting-ip": "not-an-ip",
        "x-forwarded-for": "203.0.113.10",
    })
    assert resolve_client_ip(req) == "203.0.113.10"


def test_resolve_ip_malformed_xff_falls_through_to_socket():
    """A garbage XFF leftmost entry should fall through to the socket IP."""
    req = _make_request("127.0.0.1", {
        "x-forwarded-for": "not-an-ip, 1.2.3.4",
    })
    assert resolve_client_ip(req) == "127.0.0.1"


def test_resolve_ip_malformed_both_falls_through_to_socket():
    """Both headers garbage → socket IP is the last resort."""
    req = _make_request("127.0.0.1", {
        "cf-connecting-ip": "garbage",
        "x-forwarded-for": "also-garbage",
    })
    assert resolve_client_ip(req) == "127.0.0.1"
