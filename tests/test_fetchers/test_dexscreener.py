"""Tests for DexScreener fetcher."""

import httpx
import pytest

from rugcheck.fetchers.dexscreener import DexScreenerFetcher

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


@pytest.fixture
def dex_ok_response():
    return [
        {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "pair1",
            "baseToken": {"address": MINT, "name": "Bonk", "symbol": "BONK"},
            "quoteToken": {"address": "SOL", "name": "Wrapped SOL", "symbol": "SOL"},
            "priceUsd": "0.000025",
            "liquidity": {"usd": 5000000, "base": 1000000, "quote": 50000},
            "volume": {"h24": 12000000, "h6": 3000000, "h1": 500000, "m5": 50000},
            "txns": {"h24": {"buys": 15000, "sells": 12000}, "h1": {"buys": 800, "sells": 600}},
            "priceChange": {"h24": 5.2, "h6": 1.1, "h1": -0.3, "m5": 0.1},
            "pairCreatedAt": 1700000000000,
        },
        {
            "chainId": "solana",
            "dexId": "orca",
            "pairAddress": "pair2",
            "baseToken": {"address": MINT, "name": "Bonk", "symbol": "BONK"},
            "quoteToken": {"address": "USDC", "name": "USDC", "symbol": "USDC"},
            "priceUsd": "0.000025",
            "liquidity": {"usd": 100000, "base": 50000, "quote": 10000},
            "volume": {"h24": 500000},
            "txns": {"h24": {"buys": 500, "sells": 400}},
            "pairCreatedAt": 1700000000000,
        },
    ]


async def test_dexscreener_success(httpx_mock, dex_ok_response):
    httpx_mock.add_response(json=dex_ok_response)
    async with httpx.AsyncClient() as client:
        fetcher = DexScreenerFetcher(client, timeout=3.0)
        result = await fetcher.fetch(MINT)

    assert result.success is True
    assert result.source == "DexScreener"
    assert result.data["token_name"] == "Bonk"
    assert result.data["token_symbol"] == "BONK"
    assert result.data["price_usd"] == 0.000025
    assert result.data["liquidity_usd"] == 5000000  # picks highest-liquidity pair
    assert result.data["volume_24h_usd"] == 12000000
    assert result.data["buy_count_24h"] == 15000
    assert result.data["sell_count_24h"] == 12000
    assert result.data["pair_created_at"] is not None


async def test_dexscreener_no_pairs(httpx_mock):
    httpx_mock.add_response(json=[])
    async with httpx.AsyncClient() as client:
        fetcher = DexScreenerFetcher(client, timeout=3.0)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert result.error == "no_pairs"


async def test_dexscreener_timeout(httpx_mock):
    # Register 2 timeouts: 1 initial + 1 retry (timeout is retryable)
    for _ in range(2):
        httpx_mock.add_exception(httpx.ReadTimeout("timeout"))
    async with httpx.AsyncClient() as client:
        fetcher = DexScreenerFetcher(client, timeout=0.1)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert result.error == "timeout"


async def test_dexscreener_http_error(httpx_mock):
    # Register 2 responses: 1 initial + 1 retry (503 is retryable)
    for _ in range(2):
        httpx_mock.add_response(status_code=503)
    async with httpx.AsyncClient() as client:
        fetcher = DexScreenerFetcher(client, timeout=3.0)
        result = await fetcher.fetch(MINT)

    assert result.success is False
    assert "503" in result.error
