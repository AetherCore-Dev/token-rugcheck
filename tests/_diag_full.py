"""Full async diagnostic with gateway + middleware — every step has a timeout."""
import asyncio
import os
import signal
import time

os.environ["X402_MODE"] = "test"
os.environ["X402_NETWORK"] = "mock"

# ── Global script timeout (hard kill) ──────────────────────────────
SCRIPT_TIMEOUT = 90  # seconds — the absolute upper bound


def _alarm_handler(signum, frame):
    print(f"\n[FATAL] Script exceeded {SCRIPT_TIMEOUT}s global timeout — force exit", flush=True)
    os._exit(1)


if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(SCRIPT_TIMEOUT)

print("M0: script start", flush=True)


async def _wait(coro, label: str, timeout: float):
    """Await *coro* with a timeout; prints elapsed time either way."""
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        elapsed = time.monotonic() - t0
        print(f"  [{label}] OK  ({elapsed:.1f}s)", flush=True)
        return result
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        print(f"  [{label}] TIMEOUT after {elapsed:.1f}s (limit={timeout}s)", flush=True)
        raise
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"  [{label}] ERROR after {elapsed:.1f}s: {type(exc).__name__}: {exc}", flush=True)
        raise


async def _start_server(srv, label: str, timeout: float = 8.0):
    """Start a uvicorn server with a timeout on the 'started' flag."""
    task = asyncio.create_task(srv.serve())
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.1)
        if srv.started:
            print(f"  [{label}] started in {time.monotonic() - t0:.1f}s", flush=True)
            return task
    print(f"  [{label}] FAILED to start within {timeout}s", flush=True)
    raise RuntimeError(f"{label} did not start within {timeout}s")


async def main():
    print("M1: entering main()", flush=True)
    import httpx
    import uvicorn
    from rugcheck.config import Config
    from rugcheck.server import create_app
    from ag402_mcp import X402Gateway

    srv = gw_srv = None

    try:
        # ── 1. Audit server ──────────────────────────────────────────
        print("M2: starting audit server on :18700 ...", flush=True)
        cfg = Config(port=18700)
        app = create_app(config=cfg)
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18700, log_level="warning"))
        srv.install_signal_handlers = lambda: None
        await _start_server(srv, "audit-srv", timeout=8.0)

        # ── 2. Gateway ───────────────────────────────────────────────
        print("M3: starting gateway on :18701 ...", flush=True)
        gw = X402Gateway(
            target_url="http://127.0.0.1:18700",
            price="0.05",
            address="fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm",
        )
        gw_app = gw.create_app()
        gw_srv = uvicorn.Server(uvicorn.Config(gw_app, host="127.0.0.1", port=18701, log_level="warning"))
        gw_srv.install_signal_handlers = lambda: None
        await _start_server(gw_srv, "gateway-srv", timeout=8.0)

        # ── 3. Quick 402 sanity check ────────────────────────────────
        print("M4: 402 sanity check ...", flush=True)
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await _wait(
                c.get("http://127.0.0.1:18701/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"),
                "402-check", timeout=10.0,
            )
            print(f"  status={r.status_code}", flush=True)

        # ── 4. Set up middleware ─────────────────────────────────────
        print("M5: setting up x402 middleware ...", flush=True)
        from ag402_core.config import load_config as load_x402_config
        from ag402_core.middleware.x402_middleware import X402PaymentMiddleware
        from ag402_core.payment.registry import PaymentProviderRegistry
        from ag402_core.wallet.agent_wallet import AgentWallet

        x402_cfg = load_x402_config()
        wallet = AgentWallet(db_path=os.path.expanduser("~/.ag402/test_diag_full.db"))
        await _wait(wallet.init_db(), "wallet-init", timeout=5.0)
        balance = await wallet.get_balance()
        if balance == 0:
            await wallet.deposit(100.0, note="test")
        provider = PaymentProviderRegistry.get_provider(config=x402_cfg)
        mw = X402PaymentMiddleware(wallet=wallet, provider=provider, config=x402_cfg)
        print("  middleware ready", flush=True)

        # ── 5. handle_request (the big one) ──────────────────────────
        # Timeout budget: mw._client timeout=30s, gateway proxy timeout=30s,
        # aggregate timeout=15s+20s hard cap.  We give 60s total.
        print("M6: handle_request (timeout=60s) ...", flush=True)
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                mw.handle_request(
                    method="GET",
                    url="http://127.0.0.1:18701/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                ),
                timeout=60,
            )
            elapsed = time.monotonic() - t0
            print(f"M7: status={result.status_code} paid={result.payment_made} elapsed={elapsed:.1f}s", flush=True)
            if result.body:
                body = result.body.decode() if isinstance(result.body, bytes) else str(result.body)
                print(f"M7: body={body[:500]}", flush=True)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            print(f"M7: TIMEOUT after {elapsed:.1f}s", flush=True)
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"M7: ERROR after {elapsed:.1f}s: {type(e).__name__}: {e}", flush=True)

    except Exception as e:
        print(f"[FATAL] Unhandled: {type(e).__name__}: {e}", flush=True)
    finally:
        # ── Cleanup ──────────────────────────────────────────────────
        print("M8: cleaning up ...", flush=True)
        if srv:
            srv.should_exit = True
        if gw_srv:
            gw_srv.should_exit = True
        # Give servers a moment to shut down, but don't block forever
        try:
            await asyncio.wait_for(asyncio.sleep(1.0), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        print("[DONE]", flush=True)
        # Cancel the alarm — we finished successfully
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        # Force-exit to avoid lingering uvicorn background tasks
        os._exit(0)


asyncio.run(main())
