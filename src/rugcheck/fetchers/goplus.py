"""GoPlus Security API fetcher — contract security data for Solana tokens."""

from __future__ import annotations

from rugcheck.fetchers.base import BaseFetcher
from rugcheck.models import FetcherResult

GOPLUS_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"


class GoPlusFetcher(BaseFetcher):
    source_name = "GoPlus"

    def __init__(self, client, timeout: float = 5.0, app_key: str = "", app_secret: str = ""):
        super().__init__(client, timeout=timeout)
        self._app_key = app_key
        self._app_secret = app_secret

    async def _do_fetch(self, mint_address: str) -> FetcherResult:
        headers = {}
        if self._app_key and self._app_secret:
            headers["app_key"] = self._app_key
            headers["app_secret"] = self._app_secret

        resp = await self.client.get(
            GOPLUS_URL,
            params={"contract_addresses": mint_address},
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()

        code = body.get("code")
        if code not in (1, 2):
            return FetcherResult(source=self.source_name, success=False, error=f"goplus_code_{code}")

        result_data = body.get("result", {})
        # GoPlus keys results by lowercase address
        token_data = result_data.get(mint_address) or result_data.get(mint_address.lower()) or {}
        if not token_data:
            # Try first key as fallback
            for v in result_data.values():
                token_data = v
                break

        if not token_data:
            return FetcherResult(source=self.source_name, success=False, error="no_data")

        parsed = _parse_goplus(token_data)
        parsed["_partial"] = code == 2
        return FetcherResult(source=self.source_name, success=True, data=parsed)


def _parse_goplus(raw: dict) -> dict:
    """Extract the fields we care about from GoPlus response."""
    data: dict = {}

    # Token identity
    meta = raw.get("metadata") or {}
    data["token_name"] = meta.get("name")
    data["token_symbol"] = meta.get("symbol")

    # Authority flags — status "1" means the capability IS available (risky)
    data["is_mintable"] = _status_bool(raw.get("mintable"))
    data["is_freezable"] = _status_bool(raw.get("freezable"))
    data["is_closable"] = _status_bool(raw.get("closable"))
    data["is_metadata_mutable"] = _status_bool(raw.get("metadata_mutable"))

    # Holder count
    data["holder_count"] = _safe_int(raw.get("holder_count"))

    # Top 10 holders percentage
    holders = raw.get("holders") or []
    if holders:
        total_pct = sum(_safe_float(h.get("percent")) or 0.0 for h in holders[:10])
        data["top10_holder_pct"] = round(total_pct * 100, 2)  # GoPlus returns as decimal

    # DEX / liquidity — take the pool with highest TVL for liquidity,
    # and the highest burn_percent across ALL pools for LP burned status
    dex_list = raw.get("dex") or []
    if dex_list:
        best_pool = max(dex_list, key=lambda d: float(d.get("tvl") or 0))
        data["liquidity_usd"] = _safe_float(best_pool.get("tvl"))
        # burn_percent can vary per pool; take the maximum across all pools
        burn_pcts = [_safe_float(d.get("burn_percent")) for d in dex_list]
        burn_pcts = [b for b in burn_pcts if b is not None]
        if burn_pcts:
            data["lp_burned_pct"] = max(burn_pcts)

    return data


def _status_bool(obj: dict | None) -> bool | None:
    """Convert GoPlus {status: "0"/"1"} to bool."""
    if obj is None:
        return None
    status = obj.get("status")
    if status is None:
        return None
    return status == "1"


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
