"""Tests for GoPlus fetcher."""

import httpx
import pytest

from rugcheck.fetchers.goplus import GoPlusFetcher

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


@pytest.fixture
def goplus_ok_response():
    return {
        "code": 1,
        "message": "ok",
        "result": {
            MINT: {
                "metadata": {"name": "Bonk", "symbol": "BONK"},
                "total_supply": "1000000",
                "holder_count": "5000",
                "mintable": {"status": "0", "authority": []},
                "freezable": {"status": "1", "authority": [{"address": "abc", "malicious_address": 0}]},
                "closable": {"status": "0", "authority": []},
                "metadata_mutable": {"status": "1", "metadata_upgrade_authority": []},
                "holders": [
                    {"account": "a1", "percent": "0.15", "is_locked": 0},
                    {"account": "a2", "percent": "0.10", "is_locked": 0},
                ],
                "dex": [
                    {"dex_name": "raydium", "tvl": 50000.0, "burn_percent": 95.5},
                    {"dex_name": "raydium", "tvl": 1000.0, "burn_percent": 0},
                ],
            }
        },
    }


async def test_goplus_success(httpx_mock, goplus_ok_response):
    httpx_mock.add_response(json=goplus_ok_response)
    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert result.source == "GoPlus"
    assert result.data["token_name"] == "Bonk"
    assert result.data["token_symbol"] == "BONK"
    assert result.data["is_mintable"] is False
    assert result.data["is_freezable"] is True
    assert result.data["is_closable"] is False
    assert result.data["is_metadata_mutable"] is True
    assert result.data["holder_count"] == 5000
    assert result.data["top10_holder_pct"] == 25.0  # (0.15 + 0.10) * 100
    assert result.data["liquidity_usd"] == 50000.0  # picks highest TVL pool
    assert result.data["lp_burned_pct"] == 95.5


async def test_goplus_partial_data(httpx_mock):
    httpx_mock.add_response(json={"code": 2, "message": "pending", "result": {MINT: {"metadata": {"name": "New"}}}})
    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert result.data["_partial"] is True
    assert result.data["token_name"] == "New"


async def test_goplus_timeout(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=0.1)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert result.error == "timeout"


async def test_goplus_error_code(httpx_mock):
    httpx_mock.add_response(json={"code": 4029, "message": "rate limited", "result": None})
    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert "4029" in result.error


async def test_goplus_http_500(httpx_mock):
    httpx_mock.add_response(status_code=500)
    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert "500" in result.error


# ---------------------------------------------------------------------------
# GoPlus authentication header tests
# ---------------------------------------------------------------------------


async def test_goplus_sends_auth_headers(httpx_mock, goplus_ok_response):
    """When app_key/app_secret are provided, they should be sent as headers."""
    captured_headers = {}

    def _capture(request):
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json=goplus_ok_response)

    httpx_mock.add_callback(_capture)

    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0, app_key="test-key", app_secret="test-secret")
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert captured_headers.get("app_key") == "test-key"
    assert captured_headers.get("app_secret") == "test-secret"


async def test_goplus_no_auth_headers_when_empty(httpx_mock, goplus_ok_response):
    """When credentials are empty, no auth headers should be sent."""
    captured_headers = {}

    def _capture(request):
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json=goplus_ok_response)

    httpx_mock.add_callback(_capture)

    async with httpx.AsyncClient() as client:
        fetcher = GoPlusFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert "app_key" not in captured_headers
    assert "app_secret" not in captured_headers
