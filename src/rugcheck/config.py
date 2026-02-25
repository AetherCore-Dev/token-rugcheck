"""Configuration management — loads from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable service configuration."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Cache
    cache_ttl_seconds: int = 30
    cache_max_size: int = 10_000

    # Upstream timeouts
    goplus_timeout: float = 5.0
    rugcheck_timeout: float = 5.0
    dexscreener_timeout: float = 5.0

    # GoPlus auth (optional)
    goplus_app_key: str = ""
    goplus_app_secret: str = ""

    # ag402 Payment Gateway
    ag402_price: str = "0.05"
    ag402_address: str = "fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm"
    ag402_chain: str = "solana"
    ag402_token: str = "USDC"
    ag402_network: str = "devnet"
    ag402_gateway_port: int = 8001


def load_config() -> Config:
    """Build Config from environment variables with sensible defaults."""
    return Config(
        host=os.getenv("RUGCHECK_HOST", "0.0.0.0"),
        port=int(os.getenv("RUGCHECK_PORT", "8000")),
        log_level=os.getenv("RUGCHECK_LOG_LEVEL", "info"),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "30")),
        cache_max_size=int(os.getenv("CACHE_MAX_SIZE", "10000")),
        goplus_timeout=float(os.getenv("GOPLUS_TIMEOUT_SECONDS", "5")),
        rugcheck_timeout=float(os.getenv("RUGCHECK_API_TIMEOUT_SECONDS", "5")),
        dexscreener_timeout=float(os.getenv("DEXSCREENER_TIMEOUT_SECONDS", "5")),
        goplus_app_key=os.getenv("GOPLUS_APP_KEY", ""),
        goplus_app_secret=os.getenv("GOPLUS_APP_SECRET", ""),
        ag402_price=os.getenv("AG402_PRICE", "0.05"),
        ag402_address=os.getenv("AG402_ADDRESS", "fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm"),
        ag402_chain=os.getenv("AG402_CHAIN", "solana"),
        ag402_token=os.getenv("AG402_TOKEN", "USDC"),
        ag402_network=os.getenv("X402_NETWORK", "devnet"),
        ag402_gateway_port=int(os.getenv("AG402_GATEWAY_PORT", "8001")),
    )
