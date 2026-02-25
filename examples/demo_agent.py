"""
E2E Demo: AI Agent queries token safety audit via the RugCheck MCP service.

Demonstrates:
  1. Start the audit server locally
  2. Start the ag402 payment gateway (mock mode)
  3. Query a well-known safe token (BONK) through the gateway
  4. Query directly (without payment) and via gateway (with auto-payment)
  5. Show the three-layer audit report
  6. Demonstrate cache hit behavior

Usage:
    python examples/demo_agent.py                # Direct mode (no payment)
    python examples/demo_agent.py --with-gateway  # With ag402 gateway

    Requires internet access to reach GoPlus, RugCheck, and DexScreener APIs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx
import uvicorn

from rugcheck.config import Config
from rugcheck.server import create_app

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AUDIT_PORT = 18000
GATEWAY_PORT = 18001
# BONK — well-known Solana memecoin (should be relatively safe)
BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def log(tag: str, msg: str) -> None:
    print(f"  [{tag}] {msg}")


# ---------------------------------------------------------------------------
# Background server
# ---------------------------------------------------------------------------

class BackgroundServer:
    def __init__(self, app, port: int):
        self.app = app
        self.port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(50):
            await asyncio.sleep(0.1)
            if self._server.started:
                break

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="Token RugCheck MCP Demo")
    parser.add_argument("--with-gateway", action="store_true", help="Run with ag402 payment gateway")
    args = parser.parse_args()

    print()
    print("=" * 64)
    print("  Token RugCheck MCP — End-to-End Demo")
    if args.with_gateway:
        print("  Mode: With ag402 Payment Gateway (mock)")
    else:
        print("  Mode: Direct (no payment gateway)")
    print("=" * 64)
    print()

    # 1. Start audit server
    log("BOOT", f"Starting audit server on port {AUDIT_PORT}...")
    cfg = Config(port=AUDIT_PORT)
    app = create_app(config=cfg)
    server = BackgroundServer(app, AUDIT_PORT)
    await server.start()
    log("BOOT", "Audit server ready.")

    gateway_server = None
    if args.with_gateway:
        # 2. Start ag402 payment gateway (mock mode)
        log("BOOT", f"Starting ag402 gateway on port {GATEWAY_PORT}...")
        from ag402_mcp import X402Gateway

        gw = X402Gateway(
            target_url=f"http://127.0.0.1:{AUDIT_PORT}",
            price="0.05",
            chain="solana",
            token="USDC",
            address="fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm",
        )
        gw_app = gw.create_app()
        gateway_server = BackgroundServer(gw_app, GATEWAY_PORT)
        await gateway_server.start()
        log("BOOT", "ag402 gateway ready.")

    print()
    base_url = f"http://127.0.0.1:{AUDIT_PORT}"
    gateway_url = f"http://127.0.0.1:{GATEWAY_PORT}" if args.with_gateway else None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 3. Health check (direct)
            log("HEALTH", f"GET {base_url}/health")
            resp = await client.get(f"{base_url}/health")
            log("HEALTH", f"Status: {resp.status_code} — {resp.json()}")
            print()

            if gateway_url:
                # 3a. Test gateway without payment — should get 402
                print("-" * 64)
                log("AG402", f"Testing gateway WITHOUT payment proof...")
                log("AG402", f"GET {gateway_url}/audit/{BONK_MINT[:16]}...")
                print("-" * 64)
                resp_402 = await client.get(f"{gateway_url}/audit/{BONK_MINT}")
                log("AG402", f"Status: {resp_402.status_code} (expected 402 Payment Required)")
                if resp_402.status_code == 402:
                    log("AG402", "Payment gateway working correctly!")
                print()

                # 3b. Enable ag402 auto-payment and query through gateway
                print("-" * 64)
                log("AG402", "Enabling ag402 auto-payment (mock mode)...")
                print("-" * 64)
                import ag402_core
                ag402_core.enable()
                log("AG402", f"ag402 enabled: {ag402_core.is_enabled()}")
                print()

            # 4. Audit BONK (known safe token)
            query_url = gateway_url or base_url
            print("-" * 64)
            log("AUDIT", f"Querying token: BONK ({BONK_MINT[:16]}...)")
            log("AUDIT", f"GET {query_url}/audit/{BONK_MINT}")
            print("-" * 64)

            t0 = time.monotonic()
            resp = await client.get(f"{query_url}/audit/{BONK_MINT}")
            elapsed = time.monotonic() - t0

            if resp.status_code == 200:
                report = resp.json()
                action = report["action"]
                analysis = report["analysis"]
                evidence = report["evidence"]
                meta = report["metadata"]

                print()
                log("RESULT", f"Risk Score: {action['risk_score']}/100")
                log("RESULT", f"Risk Level: {action['risk_level']}")
                log("RESULT", f"Safe to Buy: {action['is_safe']}")
                print()
                log("ANALYSIS", analysis["summary"])
                if analysis["red_flags"]:
                    for flag in analysis["red_flags"]:
                        log("RED FLAG", f"[{flag['level']}] {flag['message']}")
                if analysis["green_flags"]:
                    for flag in analysis["green_flags"]:
                        log("GREEN", flag["message"])
                print()
                log("EVIDENCE", f"Price: ${evidence.get('price_usd', 'N/A')}")
                log("EVIDENCE", f"Liquidity: ${evidence.get('liquidity_usd', 'N/A')}")
                log("EVIDENCE", f"24h Volume: ${evidence.get('volume_24h_usd', 'N/A')}")
                log("EVIDENCE", f"Holders: {evidence.get('holder_count', 'N/A')}")
                print()
                log("META", f"Sources: {', '.join(meta['data_sources'])}")
                log("META", f"Completeness: {meta['data_completeness']}")
                log("META", f"Response Time: {meta['response_time_ms']}ms (total: {elapsed:.1f}s)")
                log("META", f"Cache Hit: {meta['cache_hit']}")
            else:
                log("ERROR", f"Status {resp.status_code}: {resp.text}")

            # 5. Cache hit demo
            print()
            print("-" * 64)
            log("CACHE", "Querying BONK again (should be cached)...")
            print("-" * 64)

            t0 = time.monotonic()
            resp2 = await client.get(f"{query_url}/audit/{BONK_MINT}")
            elapsed2 = time.monotonic() - t0

            if resp2.status_code == 200:
                report2 = resp2.json()
                log("CACHE", f"Cache Hit: {report2['metadata']['cache_hit']}")
                log("CACHE", f"Response Time: {elapsed2*1000:.0f}ms (vs {elapsed*1000:.0f}ms first time)")

            # 6. Invalid address test
            print()
            print("-" * 64)
            log("INVALID", "Testing invalid address handling...")
            print("-" * 64)
            resp3 = await client.get(f"{base_url}/audit/this-is-not-valid")
            log("INVALID", f"Status: {resp3.status_code} — {resp3.json()['detail']}")

            # 7. Stats
            print()
            print("-" * 64)
            log("STATS", f"GET {base_url}/stats")
            print("-" * 64)
            resp4 = await client.get(f"{base_url}/stats")
            log("STATS", json.dumps(resp4.json(), indent=2))

            if gateway_url:
                ag402_core.disable()
                log("AG402", "ag402 auto-payment disabled.")

    finally:
        if gateway_server:
            await gateway_server.stop()
        await server.stop()

    print()
    print("=" * 64)
    log("DONE", "Demo complete. Token RugCheck MCP is working.")
    print("=" * 64)
    print()

    # Flush stdout before force-exit to ensure all output is visible.
    import sys as _sys
    _sys.stdout.flush()
    _sys.stderr.flush()

    # Force exit to avoid uvicorn background task hangs
    import os as _os
    _os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
