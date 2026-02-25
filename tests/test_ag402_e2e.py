"""
E2E test: Verify ag402 gateway integration with RugCheck audit service.

Tests:
  1. Start audit server
  2. Start ag402 gateway (X402Gateway) in front of it
  3. Verify direct access to audit server works
  4. Verify gateway returns 402 without payment proof
  5. Verify X402PaymentMiddleware auto-pays and gets 200
  6. Direct audit test (sanity check)

NOTE: We use X402PaymentMiddleware.handle_request() directly instead of
ag402_core.enable() monkey-patch. The monkey-patch replaces
httpx.AsyncClient.send at class level, which also intercepts the
middleware's own internal HTTP calls, causing an infinite 402 loop
when gateway and test client share the same process.

KNOWN ag402 BUG: ag402_core.enable() monkey-patches httpx.AsyncClient.send
at the CLASS level (monkey.py:247). This affects ALL AsyncClient instances,
including the middleware's own self._client. When the middleware makes its
internal HTTP request and gets 402, _patched_send intercepts it and calls
mw.handle_request() again -> infinite recursion. Fix requires a
contextvars re-entrancy guard in _patched_send.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

os.environ.setdefault("X402_MODE", "test")
os.environ.setdefault("X402_NETWORK", "mock")

import httpx
import uvicorn

# ── Configuration ────────────────────────────────────────────────────
AUDIT_PORT = 18200
GATEWAY_PORT = 18201
BONK_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# Timeout budgets (seconds)
SCRIPT_TIMEOUT = 120       # Global hard-kill via SIGALRM
SERVER_START_TIMEOUT = 10  # Max wait for uvicorn to bind port
HANDLE_REQ_TIMEOUT = 60    # handle_request: mw(30) + gw_proxy(30) + aggregate(15+20)
STEP_TIMEOUT = 15          # Simple HTTP steps (health, 402 check, direct audit)
CLEANUP_TIMEOUT = 5        # Server shutdown grace period


# ── Global hard timeout ─────────────────────────────────────────────
def _alarm_handler(signum, frame):
    print(f"\n[FATAL] Script exceeded {SCRIPT_TIMEOUT}s global timeout — force exit", flush=True)
    os._exit(1)


if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(SCRIPT_TIMEOUT)


# ── Helpers ──────────────────────────────────────────────────────────
def _kill_port_holders(*ports: int) -> None:
    """Best-effort kill of processes occupying the given ports."""
    import subprocess
    for port in ports:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=3,
            )
            pids = result.stdout.strip()
            if pids:
                for pid in pids.split("\n"):
                    try:
                        os.kill(int(pid), 9)
                    except (ProcessLookupError, ValueError):
                        pass
        except Exception:
            pass


async def _start_server(srv: uvicorn.Server, label: str, timeout: float) -> asyncio.Task:
    """Start uvicorn server; raise if it doesn't bind within *timeout* seconds."""
    task = asyncio.create_task(srv.serve())
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.1)
        if srv.started:
            elapsed = time.monotonic() - t0
            print(f"  {label} ready ({elapsed:.1f}s)", flush=True)
            return task
    raise RuntimeError(f"{label} failed to start within {timeout}s — port may be in use")


async def _timed(coro, label: str, timeout: float):
    """Run *coro* with timeout; print timing info."""
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        elapsed = time.monotonic() - t0
        return result, elapsed
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        print(f"  FAIL: {label} timed out after {elapsed:.1f}s (limit={timeout}s)", flush=True)
        raise


# ── Main test ────────────────────────────────────────────────────────
def main() -> None:
    asyncio.run(_run())


async def _run() -> None:
    from rugcheck.config import Config
    from rugcheck.server import create_app

    # Pre-clean: kill any leftover processes on our ports
    _kill_port_holders(AUDIT_PORT, GATEWAY_PORT)

    print("\n" + "=" * 60)
    print("  ag402 E2E Integration Test")
    print("=" * 60)

    audit_server = gw_server = None
    audit_task = gw_task = None
    errors = 0

    try:
        # --- Step 1: Start audit server ---
        print(f"\n[1/6] Starting audit server on :{AUDIT_PORT}...")
        cfg = Config(port=AUDIT_PORT)
        app = create_app(config=cfg)

        audit_server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=AUDIT_PORT, log_level="error")
        )
        audit_server.install_signal_handlers = lambda: None
        audit_task = await _start_server(audit_server, "Audit server", SERVER_START_TIMEOUT)

        # --- Step 2: Start ag402 gateway ---
        print(f"\n[2/6] Starting ag402 gateway on :{GATEWAY_PORT}...")
        from ag402_mcp import X402Gateway

        gw = X402Gateway(
            target_url=f"http://127.0.0.1:{AUDIT_PORT}",
            price="0.05",
            chain="solana",
            token="USDC",
            address="fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm",
        )
        gw_app = gw.create_app()

        gw_server = uvicorn.Server(
            uvicorn.Config(gw_app, host="127.0.0.1", port=GATEWAY_PORT, log_level="error")
        )
        gw_server.install_signal_handlers = lambda: None
        gw_task = await _start_server(gw_server, "ag402 gateway", SERVER_START_TIMEOUT)

        async with httpx.AsyncClient(timeout=STEP_TIMEOUT) as client:
            # --- Step 3: Test direct access ---
            print("\n[3/6] Testing direct access to audit server...")
            try:
                resp, elapsed = await _timed(
                    client.get(f"http://127.0.0.1:{AUDIT_PORT}/health"),
                    "health check", STEP_TIMEOUT,
                )
                if resp.status_code == 200:
                    print(f"  PASS: Health check returned {resp.status_code} ({elapsed:.1f}s)")
                else:
                    print(f"  FAIL: Health check returned {resp.status_code}")
                    errors += 1
            except asyncio.TimeoutError:
                errors += 1

            # --- Step 4: Test gateway returns 402 without payment ---
            print("\n[4/6] Testing gateway WITHOUT payment proof...")
            try:
                resp_402, elapsed = await _timed(
                    client.get(f"http://127.0.0.1:{GATEWAY_PORT}/audit/{BONK_MINT}"),
                    "gateway 402", STEP_TIMEOUT,
                )
                if resp_402.status_code == 402:
                    print(f"  PASS: Gateway returned {resp_402.status_code} (Payment Required) ({elapsed:.1f}s)")
                else:
                    print(f"  FAIL: Expected 402, got {resp_402.status_code}")
                    errors += 1
            except asyncio.TimeoutError:
                errors += 1

            # --- Step 5: Test auto-payment via X402PaymentMiddleware ---
            print(f"\n[5/6] Testing payment via X402PaymentMiddleware (timeout={HANDLE_REQ_TIMEOUT}s)...")
            from ag402_core.config import load_config as load_x402_config
            from ag402_core.middleware.x402_middleware import X402PaymentMiddleware
            from ag402_core.payment.registry import PaymentProviderRegistry
            from ag402_core.wallet.agent_wallet import AgentWallet

            x402_cfg = load_x402_config()
            wallet = AgentWallet(db_path=os.path.expanduser("~/.ag402/test_e2e_wallet.db"))
            await asyncio.wait_for(wallet.init_db(), timeout=5.0)
            balance = await wallet.get_balance()
            if balance == 0:
                await wallet.deposit(100.0, note="E2E test auto-fund")
            provider = PaymentProviderRegistry.get_provider(config=x402_cfg)

            mw = X402PaymentMiddleware(
                wallet=wallet,
                provider=provider,
                config=x402_cfg,
            )

            t0 = time.monotonic()
            result = None
            try:
                result = await asyncio.wait_for(
                    mw.handle_request(
                        method="GET",
                        url=f"http://127.0.0.1:{GATEWAY_PORT}/audit/{BONK_MINT}",
                    ),
                    timeout=HANDLE_REQ_TIMEOUT,
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t0
                print(f"  FAIL: handle_request timed out after {elapsed:.1f}s")
                print("    Likely cause: external APIs (RugCheck/DexScreener/GoPlus) unreachable.")
                errors += 1

            if result is not None:
                elapsed = time.monotonic() - t0
                if result.status_code == 200 and result.payment_made:
                    import json
                    report = json.loads(result.body)
                    print(f"  PASS: Got audit report via gateway (status {result.status_code}) in {elapsed:.1f}s")
                    print(f"    Payment: ${result.amount_paid} (tx: {result.tx_hash[:24]}...)")
                    print(f"    Risk Score: {report['action']['risk_score']}")
                    print(f"    Risk Level: {report['action']['risk_level']}")
                    print(f"    Sources: {report['metadata']['data_sources']}")
                else:
                    body_text = result.body.decode() if isinstance(result.body, bytes) else str(result.body)
                    print(f"  FAIL: Expected 200+payment, got status={result.status_code} paid={result.payment_made} in {elapsed:.1f}s")
                    print(f"    Body: {body_text[:200]}")
                    if result.error:
                        print(f"    Error: {result.error}")
                    errors += 1

            # --- Step 6: Direct audit test (sanity) ---
            print("\n[6/6] Testing direct audit endpoint...")
            try:
                resp_direct, elapsed = await _timed(
                    client.get(f"http://127.0.0.1:{AUDIT_PORT}/audit/{BONK_MINT}"),
                    "direct audit", STEP_TIMEOUT,
                )
                if resp_direct.status_code == 200:
                    report2 = resp_direct.json()
                    print(f"  PASS: Direct audit returned {resp_direct.status_code} ({elapsed:.1f}s)")
                    print(f"    Cache Hit: {report2['metadata']['cache_hit']}")
                else:
                    print(f"  FAIL: Direct audit returned {resp_direct.status_code}")
                    errors += 1
            except asyncio.TimeoutError:
                errors += 1

    except Exception as e:
        print(f"\n[FATAL] {type(e).__name__}: {e}", flush=True)
        errors += 1

    finally:
        # --- Cleanup: shut down servers with timeout ---
        if audit_server:
            audit_server.should_exit = True
        if gw_server:
            gw_server.should_exit = True

        for label, task in [("audit", audit_task), ("gateway", gw_task)]:
            if task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=CLEANUP_TIMEOUT)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    task.cancel()

    # --- Summary ---
    print("\n" + "=" * 60)
    if errors == 0:
        print("  ALL TESTS PASSED")
    else:
        print(f"  {errors} TEST(S) FAILED")
    print("=" * 60 + "\n")

    # Cancel alarm before exiting
    if hasattr(signal, "SIGALRM"):
        signal.alarm(0)

    # Force exit to avoid uvicorn background task hangs
    os._exit(errors)


if __name__ == "__main__":
    main()
