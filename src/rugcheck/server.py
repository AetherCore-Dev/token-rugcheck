"""FastAPI application — the audit API server."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from rugcheck.cache import TTLCache
from rugcheck.config import Config, load_config
from rugcheck.engine.risk_engine import build_report
from rugcheck.fetchers.aggregator import Aggregator
from rugcheck.models import AuditReport

logger = logging.getLogger(__name__)

# Solana address: base58, 32-44 chars
SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Upstream health: consider degraded if no successful call in this many seconds.
UPSTREAM_HEALTHY_WINDOW = 120  # 2 minutes


# ---------------------------------------------------------------------------
# Rate limiter (sliding window, per-IP, no external dependencies)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by IP."""

    # Loopback addresses are exempt from rate limiting.  The ag402 payment
    # gateway runs on the same host and proxies requests through localhost;
    # its 402→pay→retry handshake would otherwise consume multiple rate-limit
    # slots per logical request.
    EXEMPT_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1"})

    def __init__(self):
        # path_prefix -> (max_requests, window_seconds)
        self._limits: dict[str, tuple[int, int]] = {}
        # (path_prefix, ip) -> list of request timestamps
        self._windows: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def add_limit(self, path_prefix: str, max_requests: int, window_seconds: int) -> None:
        self._limits[path_prefix] = (max_requests, window_seconds)

    async def check(self, path: str, client_ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if client_ip in self.EXEMPT_IPS:
            return True, 0

        for prefix, (max_req, window) in self._limits.items():
            if path.startswith(prefix):
                async with self._lock:
                    now = time.monotonic()
                    key = (prefix, client_ip)
                    timestamps = self._windows[key]

                    # Prune expired entries
                    cutoff = now - window
                    timestamps[:] = [t for t in timestamps if t > cutoff]

                    if len(timestamps) >= max_req:
                        retry_after = int(timestamps[0] - cutoff) + 1
                        return False, max(retry_after, 1)

                    timestamps.append(now)
                    return True, 0

        # No limit configured for this path
        return True, 0


def create_app(config: Config | None = None, aggregator: Aggregator | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Service configuration. Loaded from env if not provided.
        aggregator: Optional pre-built aggregator (for testing). If None,
                    one is created during lifespan startup.
    """
    cfg = config or load_config()

    cache = TTLCache(ttl_seconds=cfg.cache_ttl_seconds, max_size=cfg.cache_max_size)
    # Store aggregator in a mutable container so lifespan and routes can share it
    state = {"aggregator": aggregator, "total_requests": 0}

    # Rate limiter
    rate_limiter = RateLimiter()
    rate_limiter.add_limit("/audit", max_requests=60, window_seconds=60)
    rate_limiter.add_limit("/stats", max_requests=10, window_seconds=60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["aggregator"] is None:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            state["aggregator"] = Aggregator(cfg, client=client)
        logger.info("[SERVER] Audit service ready on %s:%d", cfg.host, cfg.port)
        yield
        if state["aggregator"] is not None:
            await state["aggregator"].close()

    app = FastAPI(
        title="Token RugCheck MCP",
        description="Solana token safety audit for AI agents — rug pull detection powered by ag402 micropayments",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = await rate_limiter.check(request.url.path, client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    @app.get("/audit/{mint_address}", response_model=AuditReport)
    async def audit(mint_address: str) -> AuditReport:
        state["total_requests"] += 1

        if not SOLANA_ADDR_RE.match(mint_address):
            raise HTTPException(status_code=400, detail="Invalid Solana address format")

        cached, data_age = await cache.get(mint_address)
        if cached is not None:
            logger.info("[AUDIT] Cache HIT for %s", mint_address[:16])
            cached.metadata.cache_hit = True
            cached.metadata.data_age_seconds = int(data_age)
            return cached

        agg = state["aggregator"]
        if agg is None:
            raise HTTPException(status_code=503, detail="Service not initialized")

        t0 = time.monotonic()
        try:
            data = await asyncio.wait_for(agg.aggregate(mint_address), timeout=20.0)
        except asyncio.TimeoutError:
            logger.error("[AUDIT] aggregate() hard timeout for %s", mint_address[:16])
            raise HTTPException(
                status_code=503,
                detail="Upstream data sources timed out. Please try again later.",
            )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if not data.sources_succeeded:
            raise HTTPException(
                status_code=503,
                detail="All upstream data sources unavailable. Please try again later.",
            )

        report = build_report(mint_address, data, response_time_ms=elapsed_ms)
        report.metadata.data_age_seconds = 0
        logger.info(
            "[AUDIT] %s -> risk=%d (%s) in %dms [%s]",
            mint_address[:16],
            report.action.risk_score,
            report.action.risk_level.value,
            elapsed_ms,
            ",".join(data.sources_succeeded),
        )

        await cache.set(mint_address, report)
        return report

    @app.get("/health")
    async def health():
        agg = state["aggregator"]
        status = "ok"

        if agg is not None and agg.last_success_time is not None:
            seconds_since_success = time.monotonic() - agg.last_success_time
            if seconds_since_success > UPSTREAM_HEALTHY_WINDOW:
                status = "degraded"
        elif agg is not None and agg.last_failure_time is not None and agg.last_success_time is None:
            # We've had failures but never a success
            status = "degraded"

        result = {
            "status": status,
            "service": "token-rugcheck-mcp",
            "version": "0.1.0",
        }

        if agg is not None and agg.last_success_time is not None:
            result["last_upstream_success_secs_ago"] = int(time.monotonic() - agg.last_success_time)

        return result

    @app.get("/stats")
    async def stats():
        return {
            "total_requests": state["total_requests"],
            "cache": cache.stats,
        }

    return app
