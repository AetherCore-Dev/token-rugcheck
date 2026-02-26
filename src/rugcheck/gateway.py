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

    # Build a PaymentVerifier with a real provider for production mode.
    verifier = None
    x402_mode = os.getenv("X402_MODE", "test").lower()
    if x402_mode == "production":
        solana_key = os.getenv("SOLANA_PRIVATE_KEY", "")
        if not solana_key:
            logger.error(
                "[GATEWAY] X402_MODE=production but SOLANA_PRIVATE_KEY is not set! "
                "Cannot start gateway without on-chain verification."
            )
            sys.exit(1)

        try:
            from ag402_core.config import load_config as load_x402_config
            from ag402_core.gateway.auth import PaymentVerifier
            from ag402_core.payment.registry import PaymentProviderRegistry
        except ImportError as exc:
            logger.error(
                "[GATEWAY] X402_MODE=production requires Solana crypto dependencies. "
                'Install them with: pip install "ag402-core[crypto]"  (error: %s)',
                exc,
            )
            sys.exit(1)

        x402_cfg = load_x402_config()
        provider = PaymentProviderRegistry.get_provider(config=x402_cfg)
        verifier = PaymentVerifier(provider=provider, config=x402_cfg)
        logger.info("[GATEWAY] Production mode: on-chain payment verification enabled")

    gw = X402Gateway(
        target_url=target_url,
        price=cfg.ag402_price,
        chain=cfg.ag402_chain,
        token=cfg.ag402_token,
        address=cfg.ag402_address,
        verifier=verifier,
    )
    app = gw.create_app()

    host = os.getenv("AG402_GATEWAY_HOST", "0.0.0.0")
    port = cfg.ag402_gateway_port

    logger.info("[GATEWAY] Starting ag402 gateway on %s:%d -> %s", host, port, target_url)
    uvicorn.run(app, host=host, port=port, loop="asyncio")


if __name__ == "__main__":
    main()
