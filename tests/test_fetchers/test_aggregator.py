"""Tests for the concurrent aggregator."""

import asyncio
import re

import httpx

from rugcheck.config import Config
from rugcheck.fetchers.aggregator import Aggregator


MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
CONFIG = Config()

# --- Mock responses ---

RUGCHECK_RESP = {
    "token": {"mintAuthority": None, "freezeAuthority": None, "supply": 1000000, "decimals": 5},
    "tokenMeta": {"name": "Bonk", "symbol": "BONK", "mutable": False},
    "score": 250,
    "score_normalised": 25,
    "totalMarketLiquidity": 80000.0,
    "totalHolders": 5000,
    "risks": [],
    "topHolders": [{"owner": "a1", "pct": 5.0}],  # pct is already percentage
    "markets": [{"lp": {"lpCurrentSupply": 100, "lpTotalSupply": 200, "lpLockedPct": 99.0, "quoteUSD": 80000}}],
}

DEXSCREENER_RESP = [
    {
        "chainId": "solana",
        "baseToken": {"address": MINT, "name": "Bonk", "symbol": "BONK"},
        "quoteToken": {"address": "SOL", "name": "SOL", "symbol": "SOL"},
        "priceUsd": "0.000025",
        "liquidity": {"usd": 5000000},
        "volume": {"h24": 12000000},
        "txns": {"h24": {"buys": 15000, "sells": 12000}},
        "pairCreatedAt": 1700000000000,
    }
]

GOPLUS_RESP = {
    "code": 1,
    "message": "ok",
    "result": {
        MINT: {
            "metadata": {"name": "Bonk", "symbol": "BONK"},
            "holder_count": "5000",
            "mintable": {"status": "0"},
            "freezable": {"status": "0"},
            "closable": {"status": "0"},
            "metadata_mutable": {"status": "0"},
            "holders": [{"account": "a1", "percent": "0.08"}],
            "dex": [{"tvl": 60000, "burn_percent": 99}],
        }
    },
}


async def test_all_sources_succeed(httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*rugcheck.*"), json=RUGCHECK_RESP)
    httpx_mock.add_response(url=re.compile(r".*dexscreener.*"), json=DEXSCREENER_RESP)
    httpx_mock.add_response(url=re.compile(r".*gopluslabs.*"), json=GOPLUS_RESP)

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        result = await agg.aggregate(MINT)

    assert len(result.sources_succeeded) == 3
    assert len(result.sources_failed) == 0
    assert result.token_name == "Bonk"
    assert result.price_usd == 0.000025
    assert result.rugcheck_score == 25


async def test_one_source_fails(httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*rugcheck.*"), json=RUGCHECK_RESP)
    httpx_mock.add_response(url=re.compile(r".*dexscreener.*"), json=DEXSCREENER_RESP)
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*gopluslabs.*"))

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        result = await agg.aggregate(MINT)

    assert len(result.sources_succeeded) == 2
    assert "GoPlus" in result.sources_failed
    assert result.token_name == "Bonk"


async def test_all_sources_fail(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*rugcheck.*"))
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*dexscreener.*"))
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*gopluslabs.*"))

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        result = await agg.aggregate(MINT)

    assert len(result.sources_succeeded) == 0
    assert len(result.sources_failed) == 3
    assert result.token_name is None


async def test_priority_rugcheck_over_goplus(httpx_mock):
    """RugCheck data should take priority over GoPlus when both succeed."""
    rugcheck_data = dict(RUGCHECK_RESP)
    rugcheck_data["tokenMeta"] = {"name": "RugCheckName", "symbol": "RC"}

    goplus_data = dict(GOPLUS_RESP)
    goplus_data["result"] = {MINT: {**GOPLUS_RESP["result"][MINT], "metadata": {"name": "GoPlusName", "symbol": "GP"}}}

    httpx_mock.add_response(url=re.compile(r".*rugcheck.*"), json=rugcheck_data)
    httpx_mock.add_response(url=re.compile(r".*dexscreener.*"), json=DEXSCREENER_RESP)
    httpx_mock.add_response(url=re.compile(r".*gopluslabs.*"), json=goplus_data)

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        result = await agg.aggregate(MINT)

    assert result.token_name == "RugCheckName"


# ---------------------------------------------------------------------------
# Upstream health tracking
# ---------------------------------------------------------------------------


async def test_upstream_health_tracked_on_success(httpx_mock):
    """Aggregator should track last_success_time after a successful call."""
    httpx_mock.add_response(url=re.compile(r".*rugcheck.*"), json=RUGCHECK_RESP)
    httpx_mock.add_response(url=re.compile(r".*dexscreener.*"), json=DEXSCREENER_RESP)
    httpx_mock.add_response(url=re.compile(r".*gopluslabs.*"), json=GOPLUS_RESP)

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        assert agg.last_success_time is None
        await agg.aggregate(MINT)
        assert agg.last_success_time is not None


async def test_upstream_health_tracked_on_failure(httpx_mock):
    """Aggregator should track last_failure_time when all sources fail."""
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*rugcheck.*"))
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*dexscreener.*"))
    httpx_mock.add_exception(httpx.ReadTimeout("timeout"), url=re.compile(r".*gopluslabs.*"))

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        assert agg.last_failure_time is None
        await agg.aggregate(MINT)
        assert agg.last_failure_time is not None
        assert agg.last_success_time is None


# ---------------------------------------------------------------------------
# Semaphore concurrency limiting
# ---------------------------------------------------------------------------


async def test_semaphore_limits_concurrency():
    """Verify that the semaphore actually limits concurrent upstream calls."""
    max_concurrent = 0
    current_concurrent = 0
    lock = asyncio.Lock()

    async def _slow_fetch(mint_address: str):
        nonlocal max_concurrent, current_concurrent
        async with lock:
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent

        await asyncio.sleep(0.02)

        async with lock:
            current_concurrent -= 1

        from rugcheck.models import FetcherResult
        return FetcherResult(source="Mock", success=True, data={"token_name": "Test"})

    async with httpx.AsyncClient() as client:
        agg = Aggregator(CONFIG, client=client)
        agg._semaphore = asyncio.Semaphore(2)  # limit to 2 concurrent

        # Patch all fetchers to use slow mock
        agg.rugcheck.fetch = _slow_fetch
        agg.dexscreener.fetch = _slow_fetch
        agg.goplus.fetch = _slow_fetch

        # Run multiple aggregations concurrently (6 upstream calls total)
        await asyncio.gather(
            agg.aggregate(MINT),
            agg.aggregate(MINT),
        )

    # With semaphore=2, we should never exceed 2 concurrent upstream calls
    assert max_concurrent <= 2
