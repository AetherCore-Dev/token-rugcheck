"""Configuration management — loads from environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Placeholder that must be replaced by the user before production use.
_PLACEHOLDER_ADDRESS = "<YOUR_SOLANA_WALLET_ADDRESS>"


@dataclass(frozen=True)
class Config:
    """Immutable service configuration."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Cache
    cache_ttl_seconds: int = 3
    cache_max_size: int = 5_000

    # Upstream timeouts — tuned for 4.5s agent response budget.
    # DexScreener (CDN): fastest, strictest deadline.
    # GoPlus (commercial API): moderate tolerance.
    # RugCheck (community API): most generous.
    goplus_timeout: float = 2.5
    rugcheck_timeout: float = 3.5
    dexscreener_timeout: float = 1.5

    # GoPlus auth (optional)
    goplus_app_key: str = ""
    goplus_app_secret: str = ""

    # ag402 Payment Gateway
    ag402_price: str = "0.05"
    ag402_address: str = _PLACEHOLDER_ADDRESS
    ag402_chain: str = "solana"
    ag402_token: str = "USDC"
    ag402_network: str = "devnet"
    ag402_gateway_port: int = 8001

    # Rate limiting
    free_daily_quota: int = 20
    paid_rate_limit: int = 120


def load_config() -> Config:
    """Build Config from environment variables with sensible defaults."""
    cfg = Config(
        host=os.getenv("RUGCHECK_HOST", "0.0.0.0"),
        port=int(os.getenv("RUGCHECK_PORT", "8000")),
        log_level=os.getenv("RUGCHECK_LOG_LEVEL", "info"),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3")),
        cache_max_size=int(os.getenv("CACHE_MAX_SIZE", "5000")),
        goplus_timeout=float(os.getenv("GOPLUS_TIMEOUT_SECONDS", "2.5")),
        rugcheck_timeout=float(os.getenv("RUGCHECK_API_TIMEOUT_SECONDS", "3.5")),
        dexscreener_timeout=float(os.getenv("DEXSCREENER_TIMEOUT_SECONDS", "1.5")),
        goplus_app_key=os.getenv("GOPLUS_APP_KEY", ""),
        goplus_app_secret=os.getenv("GOPLUS_APP_SECRET", ""),
        ag402_price=os.getenv("AG402_PRICE", "0.05"),
        ag402_address=os.getenv("AG402_ADDRESS", _PLACEHOLDER_ADDRESS),
        ag402_chain=os.getenv("AG402_CHAIN", "solana"),
        ag402_token=os.getenv("AG402_TOKEN", "USDC"),
        ag402_network=os.getenv("X402_NETWORK", "devnet"),
        ag402_gateway_port=int(os.getenv("AG402_GATEWAY_PORT", "8001")),
        free_daily_quota=int(os.getenv("FREE_DAILY_QUOTA", "20")),
        paid_rate_limit=int(os.getenv("PAID_RATE_LIMIT", "120")),
    )

    if cfg.ag402_address == _PLACEHOLDER_ADDRESS:
        logger.warning(
            "[CONFIG] AG402_ADDRESS is not set! Using placeholder '%s'. "
            "Set AG402_ADDRESS in your .env file before deploying.",
            _PLACEHOLDER_ADDRESS,
        )

    return cfg
