"""Tests for RugCheck fetcher."""

import httpx
import pytest

from rugcheck.fetchers.rugcheck import RugCheckFetcher

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


@pytest.fixture
def rugcheck_ok_response():
    """Mock /report (full) endpoint response — validated against real API 2026-02."""
    return {
        "token": {
            "mintAuthority": None,
            "freezeAuthority": None,
            "supply": 1000000,
            "decimals": 5,
        },
        "tokenMeta": {"name": "Bonk", "symbol": "BONK", "mutable": True},
        "score": 750,  # raw score (not used by our code)
        "score_normalised": 75,  # 0-100, lower = safer
        "totalMarketLiquidity": 45000.0,
        "totalHolders": 8000,
        "risks": [
            {"name": "Mutable Metadata", "level": "warn"},
            {"name": "Low Liquidity", "level": "danger"},
        ],
        "topHolders": [
            {"owner": "a1", "pct": 8.0},   # already percentage (8.0 = 8%)
            {"owner": "a2", "pct": 5.0},   # already percentage (5.0 = 5%)
        ],
        "markets": [
            {
                "lp": {
                    "lpCurrentSupply": 1000,
                    "lpTotalSupply": 2000,
                    "lpLockedPct": 95.0,  # already a percentage (95.0 = 95%)
                    "quoteUSD": 45000.0,
                }
            }
        ],
    }


async def test_rugcheck_success(httpx_mock, rugcheck_ok_response):
    httpx_mock.add_response(json=rugcheck_ok_response)
    async with httpx.AsyncClient() as client:
        fetcher = RugCheckFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert result.source == "RugCheck"
    assert result.data["token_name"] == "Bonk"
    assert result.data["rugcheck_score"] == 75
    assert result.data["is_mintable"] is False
    assert result.data["is_freezable"] is False
    assert result.data["is_metadata_mutable"] is True
    assert "Mutable Metadata" in result.data["rugcheck_risks"]
    assert "Low Liquidity" in result.data["rugcheck_risks"]
    assert result.data["top10_holder_pct"] == 13.0  # 8.0 + 5.0 = 13% (pct is already percentage)
    assert result.data["liquidity_usd"] == 45000.0
    assert result.data["lp_locked_pct"] == 95.0  # lpLockedPct is already a percentage
    assert result.data["holder_count"] == 8000


async def test_rugcheck_empty_response(httpx_mock):
    httpx_mock.add_response(json={})
    async with httpx.AsyncClient() as client:
        fetcher = RugCheckFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert result.data["rugcheck_score"] is None


async def test_rugcheck_timeout(httpx_mock):
    # Register 2 timeouts: 1 initial + 1 retry (timeout is retryable)
    for _ in range(2):
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    async with httpx.AsyncClient() as client:
        fetcher = RugCheckFetcher(client, timeout=0.1)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert result.error == "timeout"


async def test_rugcheck_429(httpx_mock):
    # Register 2 responses: 1 initial + 1 retry (429 is retryable)
    for _ in range(2):
        httpx_mock.add_response(status_code=429)
    async with httpx.AsyncClient() as client:
        fetcher = RugCheckFetcher(client, timeout=5.0)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert "429" in result.error
