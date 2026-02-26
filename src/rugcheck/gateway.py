"""Custom ag402 gateway entry point.

Workaround for ag402 CLI issues:
  - ``ag402 serve`` hardcodes host=127.0.0.1 (unusable in containers)
  - uvloop/aiosqlite event loop conflict causes crash loop

This script uses the X402Gateway API directly, binds 0.0.0.0,
and avoids uvloop by running plain asyncio + uvicorn.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from rugcheck.config import load_config

# Block uvloop before any import can install it.  aiosqlite (used by
# PersistentReplayGuard) is incompatible with uvloop's event loop.
os.environ.setdefault("UVLOOP_INSTALL", "0")
sys.modules.setdefault("uvloop", None)  # type: ignore[arg-type]

logger = logging.getLogger(__name__)

# Placeholder address used in .env.example — must be replaced by the user.
_PLACEHOLDER_ADDRESS = "<YOUR_SOLANA_WALLET_ADDRESS>"


def main() -> None:
    cfg = load_config()

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    # Warn if the wallet address is the placeholder
    if not cfg.ag402_address or cfg.ag402_address == _PLACEHOLDER_ADDRESS:
        logger.warning(
            "[GATEWAY] AG402_ADDRESS is not configured! "
            "Payments will fail. Set AG402_ADDRESS in your .env file."
        )

    target_url = os.getenv(
        "AG402_TARGET_URL",
        f"http://localhost:{cfg.port}",
    )

    from ag402_mcp import X402Gateway

    gw = X402Gateway(
        target_url=target_url,
        price=cfg.ag402_price,
        chain=cfg.ag402_chain,
        token=cfg.ag402_token,
        address=cfg.ag402_address,
    )
    app = gw.create_app()

    host = os.getenv("AG402_GATEWAY_HOST", "0.0.0.0")
    port = cfg.ag402_gateway_port

    logger.info("[GATEWAY] Starting ag402 gateway on %s:%d -> %s", host, port, target_url)
    uvicorn.run(app, host=host, port=port, loop="asyncio")


if __name__ == "__main__":
    main()
