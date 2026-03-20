"""Concurrent aggregator — fetches from all sources in parallel with graceful degradation."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import httpx

from rugcheck.config import Config
from rugcheck.fetchers.dexscreener import DexScreenerFetcher
from rugcheck.fetchers.goplus import GoPlusFetcher
from rugcheck.fetchers.rugcheck import RugCheckFetcher
from rugcheck.models import AggregatedData, FetcherResult

logger = logging.getLogger(__name__)


class Aggregator:
    """Fetches from GoPlus, RugCheck, and DexScreener in parallel, merges results."""

    # Limit concurrent outbound requests to protect upstream APIs.
    MAX_UPSTREAM_CONCURRENCY: int = 20

    def __init__(self, config: Config, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._owns_client = client is None
        self.goplus = GoPlusFetcher(
            self._client,
            timeout=config.goplus_timeout,
            app_key=config.goplus_app_key,
            app_secret=config.goplus_app_secret,
        )
        self.rugcheck = RugCheckFetcher(self._client, timeout=config.rugcheck_timeout)
        self.dexscreener = DexScreenerFetcher(self._client, timeout=config.dexscreener_timeout)
        self._semaphore = asyncio.Semaphore(self.MAX_UPSTREAM_CONCURRENCY)
        # Track upstream health for /health endpoint
        self.last_success_time: float | None = None
        self.last_failure_time: float | None = None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # Hard cap on the total time we spend fetching from all upstream APIs.
    # Must fit within the server's wait_for (4.5s) with margin for
    # build_report + JSON serialization.
    AGGREGATE_TIMEOUT: float = 4.0

    async def aggregate(self, mint_address: str) -> AggregatedData:
        """Fetch all sources concurrently and merge into AggregatedData.

        An overall ``AGGREGATE_TIMEOUT`` guard ensures the call always returns
        within a bounded time even if individual fetcher timeouts are not
        honoured (e.g. DNS resolution hangs).

        A semaphore limits concurrent outbound requests to protect upstream APIs
        from being overwhelmed by a burst of inbound traffic.
        """

        async def _guarded_fetch(fetcher):
            async with self._semaphore:
                return await fetcher.fetch(mint_address)

        try:
            results: list[FetcherResult] = await asyncio.wait_for(
                asyncio.gather(
                    _guarded_fetch(self.rugcheck),
                    _guarded_fetch(self.dexscreener),
                    _guarded_fetch(self.goplus),
                ),
                timeout=self.AGGREGATE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("[AGG] aggregate() timed out after %.1fs", self.AGGREGATE_TIMEOUT)
            results = [
                FetcherResult(source="RugCheck", success=False, error="aggregate_timeout"),
                FetcherResult(source="DexScreener", success=False, error="aggregate_timeout"),
                FetcherResult(source="GoPlus", success=False, error="aggregate_timeout"),
            ]

        now = time.monotonic()
        any_success = False
        any_failure = False
        for r in results:
            if r.success:
                logger.info("[AGG] %s: OK", r.source)
                any_success = True
            else:
                logger.warning("[AGG] %s: FAILED (%s)", r.source, r.error)
                any_failure = True

        if any_success:
            self.last_success_time = now
        if any_failure:
            self.last_failure_time = now

        return _merge(results)


def _merge(results: list[FetcherResult]) -> AggregatedData:
    """Merge fetcher results with priority: RugCheck > DexScreener > GoPlus."""
    data = AggregatedData()
    sources_ok: list[str] = []
    sources_fail: list[str] = []

    # Collect all successful data dicts (order = priority for conflict resolution)
    layers: list[tuple[str, dict]] = []
    for r in results:
        if r.success:
            sources_ok.append(r.source)
            layers.append((r.source, r.data))
        else:
            sources_fail.append(r.source)

    data.sources_succeeded = sources_ok
    data.sources_failed = sources_fail

    # Merge: later sources fill in gaps but don't overwrite earlier values
    merged: dict = {}
    for _source, d in layers:
        for key, val in d.items():
            if key.startswith("_"):
                continue
            if val is None:
                continue
            if key not in merged or merged[key] is None:
                merged[key] = val

    # Map merged dict to AggregatedData fields
    data.token_name = merged.get("token_name")
    data.token_symbol = merged.get("token_symbol")
    data.is_mintable = merged.get("is_mintable")
    data.is_freezable = merged.get("is_freezable")
    data.is_closable = merged.get("is_closable")
    data.is_metadata_mutable = merged.get("is_metadata_mutable")
    data.top10_holder_pct = merged.get("top10_holder_pct")
    data.holder_count = merged.get("holder_count")
    data.liquidity_usd = merged.get("liquidity_usd")
    data.lp_burned_pct = merged.get("lp_burned_pct")
    data.lp_locked_pct = merged.get("lp_locked_pct")
    data.price_usd = merged.get("price_usd")
    data.volume_24h_usd = merged.get("volume_24h_usd")
    data.buy_count_24h = merged.get("buy_count_24h")
    data.sell_count_24h = merged.get("sell_count_24h")
    data.rugcheck_score = merged.get("rugcheck_score")
    data.rugcheck_risks = merged.get("rugcheck_risks") or []

    pair_ts = merged.get("pair_created_at")
    if pair_ts and isinstance(pair_ts, str):
        try:
            data.pair_created_at = datetime.fromisoformat(pair_ts)
        except ValueError:
            pass

    return data
