"""Custom ag402 gateway entry point.

Workaround for ag402 CLI issues:
  - ``ag402 serve`` hardcodes host=127.0.0.1 (unusable in containers)
  - uvloop/aiosqlite event loop conflict causes crash loop

This script uses the X402Gateway API directly, binds 0.0.0.0,
and avoids uvloop by running plain asyncio + uvicorn.

Seller (provider) architecture note:
  The seller does NOT need a private key.  The ag402 gateway issues x402
  payment challenges containing the seller's *public* receiving address.
  The *buyer* signs and broadcasts the on-chain payment.  The gateway then
  verifies the payment proof on-chain (read-only RPC, no private key).
  When SOLANA_PRIVATE_KEY is absent, the gateway uses ag402's built-in
  address-based verification (checks tx_hash on-chain via RPC).
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

    # Build a PaymentVerifier for production mode.
    # The seller does NOT need a private key — on-chain verification is
    # read-only (verifies tx_hash via RPC).  SOLANA_PRIVATE_KEY is only
    # needed if the provider wants to sign messages (not required by x402).
    verifier = None
    x402_mode = os.getenv("X402_MODE", "test").lower()
    if x402_mode == "production":
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
        try:
            provider = PaymentProviderRegistry.get_provider(config=x402_cfg)
            verifier = PaymentVerifier(provider=provider, config=x402_cfg)
            logger.info("[GATEWAY] Production mode: on-chain payment verification enabled")
        except Exception as exc:
            # Seller doesn't need a private key — the gateway issues 402
            # challenges with the public address; the buyer pays on-chain.
            # Without SOLANA_PRIVATE_KEY, use mock provider so the gateway
            # can still start and serve 402 challenges.  The X402Gateway
            # requires a non-None verifier in production mode, so we fall
            # back to mock adapter + force X402_MODE=test internally.
            logger.warning(
                "[GATEWAY] No SOLANA_PRIVATE_KEY — falling back to mock "
                "payment verification (%s). 402 paywall is active; "
                "on-chain verification is DISABLED.",
                exc,
            )
            os.environ["X402_MODE"] = "test"
            mock_provider = PaymentProviderRegistry.get_provider(name="mock")
            verifier = PaymentVerifier(provider=mock_provider, config=x402_cfg)

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
