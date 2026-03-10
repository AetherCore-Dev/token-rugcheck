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

  ag402's ``PaymentProviderRegistry`` currently requires
  ``SOLANA_PRIVATE_KEY`` even though ``SolanaAdapter.verify_payment()``
  only reads on-chain data.  To work around this design limitation, we
  construct ``SolanaAdapter`` directly with a *throwaway* keypair that is
  never used for signing.  This keeps the seller from having to expose
  any real private key.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from rugcheck.config import PLACEHOLDER_ADDRESS, load_config

# Block uvloop before any import can install it.  aiosqlite (used by
# PersistentReplayGuard) is incompatible with uvloop's event loop.
os.environ.setdefault("UVLOOP_INSTALL", "0")
sys.modules.setdefault("uvloop", None)  # type: ignore[arg-type]

logger = logging.getLogger(__name__)

# Re-exported from config for backward compat; canonical definition lives in config.py.
_PLACEHOLDER_ADDRESS = PLACEHOLDER_ADDRESS


def _build_verify_only_provider(rpc_url: str, network: str) -> object:
    """Build a SolanaAdapter for **verification only** (no real private key).

    ag402's SolanaAdapter.__init__ requires a ``private_key`` parameter,
    but ``verify_payment()`` never uses it — it only queries the chain
    via RPC.  We generate a throwaway keypair to satisfy the constructor
    and immediately discard the signing capability.

    Returns a ``SolanaAdapter`` instance suitable for ``PaymentVerifier``.
    """
    from ag402_core.payment.solana_adapter import SolanaAdapter
    from solders.keypair import Keypair  # type: ignore[import-untyped]

    throwaway_kp = Keypair()
    throwaway_key = str(throwaway_kp)  # base58 string

    # Choose the correct USDC mint based on network
    usdc_mints = {
        "mainnet": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "devnet": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
    }
    usdc_mint = usdc_mints.get(network, usdc_mints["devnet"])

    provider = SolanaAdapter(
        private_key=throwaway_key,
        rpc_url=rpc_url,
        usdc_mint=usdc_mint,
    )
    logger.info(
        "[GATEWAY] Built verify-only SolanaAdapter (throwaway keypair, rpc=%s, network=%s)",
        rpc_url,
        network,
    )
    return provider


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
    # read-only (verifies tx_hash via RPC).
    verifier = None
    x402_mode = os.getenv("X402_MODE", "test").lower()
    if x402_mode == "production":
        try:
            from ag402_core.config import load_config as load_x402_config
            from ag402_core.gateway.auth import PaymentVerifier
        except ImportError as exc:
            logger.error(
                "[GATEWAY] X402_MODE=production requires Solana crypto dependencies. "
                'Install them with: pip install "ag402-core[crypto]"  (error: %s)',
                exc,
            )
            sys.exit(1)

        x402_cfg = load_x402_config()
        rpc_url = x402_cfg.effective_rpc_url
        network = cfg.ag402_network  # "devnet" or "mainnet"

        try:
            provider = _build_verify_only_provider(rpc_url, network)
            verifier = PaymentVerifier(provider=provider, config=x402_cfg)
            logger.info("[GATEWAY] Production mode: on-chain payment verification enabled (no seller private key needed)")
        except Exception as exc:
            logger.critical(
                "[GATEWAY] FATAL: Failed to initialise payment verifier: %s. "
                "Ensure ag402-core[crypto] is installed and SOLANA_RPC_URL is reachable. "
                "Refusing to start with mock verification in production — "
                "this would allow free access to paid services.",
                exc,
            )
            sys.exit(1)

    gw = X402Gateway(
        target_url=target_url,
        price=cfg.ag402_price,
        chain=cfg.ag402_chain,
        token=cfg.ag402_token,
        address=cfg.ag402_address,
        verifier=verifier,
        prepaid_signing_key=cfg.ag402_prepaid_signing_key,
    )
    app = gw.create_app()

    host = os.getenv("AG402_GATEWAY_HOST", "0.0.0.0")
    port = cfg.ag402_gateway_port

    logger.info("[GATEWAY] Starting ag402 gateway on %s:%d -> %s", host, port, target_url)
    uvicorn.run(app, host=host, port=port, loop="asyncio")


if __name__ == "__main__":
    main()
