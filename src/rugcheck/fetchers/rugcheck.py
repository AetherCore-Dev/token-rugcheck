"""RugCheck.xyz API fetcher — Solana-native token risk assessment.

Uses the full /report endpoint (not /report/summary) to get complete data
including markets, topHolders, and tokenMeta.
"""

from __future__ import annotations

from rugcheck.fetchers.base import BaseFetcher
from rugcheck.models import FetcherResult

RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens"


class RugCheckFetcher(BaseFetcher):
    source_name = "RugCheck"

    async def _do_fetch(self, mint_address: str) -> FetcherResult:
        resp = await self.client.get(
            f"{RUGCHECK_URL}/{mint_address}/report",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()

        parsed = _parse_rugcheck(body)
        return FetcherResult(source=self.source_name, success=True, data=parsed)


def _parse_rugcheck(raw: dict) -> dict:
    """Extract fields from RugCheck full report response.

    Real API structure (validated 2026-02 against BONK):
        token: {mintAuthority, freezeAuthority, supply, decimals, ...}
        tokenMeta: {name, symbol, uri, mutable, updateAuthority}
        score: int (raw, lower = safer)
        score_normalised: int (0-100, lower = safer)
        risks: [{name, value, description, score, level}]
        markets: [{lp: {quoteUSD, lpLockedPct, lpLocked, ...}, ...}]
        topHolders: [{pct, owner, ...}] — pct is ALREADY percentage (7.64 = 7.64%)
        totalMarketLiquidity: float
        totalHolders: int (may be 0 for large-cap tokens)
    """
    data: dict = {}

    # Token identity
    meta = raw.get("tokenMeta") or {}
    data["token_name"] = meta.get("name")
    data["token_symbol"] = meta.get("symbol")

    # RugCheck normalised score (0-100, lower = safer)
    data["rugcheck_score"] = raw.get("score_normalised")

    # Authority flags — nested under "token" object, NOT top-level
    token_obj = raw.get("token") or {}
    data["is_mintable"] = token_obj.get("mintAuthority") is not None and token_obj.get("mintAuthority") != ""
    data["is_freezable"] = token_obj.get("freezeAuthority") is not None and token_obj.get("freezeAuthority") != ""
    data["is_metadata_mutable"] = meta.get("mutable") is True

    # Individual risk items
    risks = raw.get("risks") or []
    data["rugcheck_risks"] = [r.get("name", "") for r in risks if r.get("name")]

    # Total market liquidity (aggregated across all pools)
    data["liquidity_usd"] = _safe_float(raw.get("totalMarketLiquidity"))

    # LP locked percentage — from the highest-liquidity market
    # lpLockedPct is ALREADY a percentage (99.59 = 99.59% locked). Do NOT multiply by 100.
    markets = raw.get("markets") or []
    if markets:
        best = max(markets, key=lambda m: float((m.get("lp") or {}).get("quoteUSD") or 0))
        lp_info = best.get("lp") or {}
        lp_locked_pct = _safe_float(lp_info.get("lpLockedPct"))
        if lp_locked_pct is not None:
            data["lp_locked_pct"] = round(lp_locked_pct, 2)

    # Top holders — pct is ALREADY a percentage (7.64 = 7.64%), do NOT multiply by 100
    top_holders = raw.get("topHolders") or []
    if top_holders:
        total_pct = sum(float(h.get("pct", 0)) for h in top_holders[:10])
        data["top10_holder_pct"] = round(total_pct, 2)

    # Total holders (may be 0 for large-cap tokens — treat 0 as None)
    raw_holders = raw.get("totalHolders")
    data["holder_count"] = raw_holders if raw_holders and raw_holders > 0 else None

    return data


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
