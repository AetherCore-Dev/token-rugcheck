"""FastAPI application — the audit API server."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from rugcheck.cache import TTLCache
from rugcheck.config import Config, load_config
from rugcheck.engine.risk_engine import build_report
from rugcheck.fetchers.aggregator import Aggregator
from rugcheck.models import AggregatedData, AuditReport, RiskLevel

logger = logging.getLogger(__name__)

# Solana address: base58, 32-44 chars
SOLANA_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Upstream health: consider degraded if no successful call in this many seconds.
UPSTREAM_HEALTHY_WINDOW = 120  # 2 minutes


# ---------------------------------------------------------------------------
# Rate limiter (sliding window, per-IP, no external dependencies)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by IP.

    Includes periodic eviction of stale entries to prevent unbounded memory
    growth from unique IPs that never return.
    """

    # Run global eviction every N calls to ``check()``.
    _EVICT_EVERY: int = 256

    def __init__(self, max_requests: int, window_seconds: int):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        # ip -> list of request timestamps
        self._windows: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._check_count = 0

    async def check(self, client_ip: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        if client_ip == "unknown":
            return True, 0

        async with self._lock:
            now = time.monotonic()
            cutoff = now - self._window_seconds

            # Periodic global eviction of stale IPs
            self._check_count += 1
            if self._check_count >= self._EVICT_EVERY:
                self._check_count = 0
                stale_keys = [
                    ip for ip, ts_list in self._windows.items()
                    if not ts_list or ts_list[-1] <= cutoff
                ]
                for ip in stale_keys:
                    del self._windows[ip]

            timestamps = self._windows[client_ip]

            # Prune expired entries for this IP
            timestamps[:] = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= self._max_requests:
                retry_after = int(timestamps[0] - cutoff) + 1
                return False, max(retry_after, 1)

            timestamps.append(now)
            return True, 0


class DailyQuota:
    """Per-IP daily request quota. Resets at UTC midnight.

    Caps the number of tracked IPs via ``max_tracked_ips`` to prevent memory
    exhaustion from IP-rotation attacks.
    """

    def __init__(self, max_daily: int, max_tracked_ips: int = 50_000):
        self._max_daily = max_daily
        self._max_tracked_ips = max_tracked_ips
        # ip -> (date_str, count)
        self._counts: dict[str, tuple[str, int]] = {}
        self._lock = asyncio.Lock()

    async def check(self, client_ip: str) -> tuple[bool, int]:
        """Return (allowed, remaining). remaining is 0 when exhausted."""
        if client_ip == "unknown":
            return True, self._max_daily

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async with self._lock:
            # Evict entries from previous days if we're over the cap
            if len(self._counts) >= self._max_tracked_ips:
                stale_keys = [
                    ip for ip, (date_str, _) in self._counts.items()
                    if date_str != today
                ]
                for ip in stale_keys:
                    del self._counts[ip]
                # If still over cap after evicting stale, drop oldest entries
                if len(self._counts) >= self._max_tracked_ips:
                    excess = len(self._counts) - self._max_tracked_ips + 1
                    for ip in list(self._counts.keys())[:excess]:
                        del self._counts[ip]

            entry = self._counts.get(client_ip)
            if entry is None or entry[0] != today:
                # New day or first request — reset
                self._counts[client_ip] = (today, 1)
                return True, self._max_daily - 1

            date_str, count = entry
            if count >= self._max_daily:
                return False, 0

            self._counts[client_ip] = (today, count + 1)
            return True, self._max_daily - count - 1


# Loopback IPs — requests from the ag402 gateway running on the same host.
GATEWAY_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "rugcheck_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "rugcheck_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
)
UPSTREAM_SUCCESS = Counter(
    "rugcheck_upstream_success_total",
    "Successful upstream fetches",
    ["source"],
)
UPSTREAM_FAILURE = Counter(
    "rugcheck_upstream_failure_total",
    "Failed upstream fetches",
    ["source"],
)
CACHE_HIT_TOTAL = Counter(
    "rugcheck_cache_hits_total",
    "Cache hits",
)
CACHE_MISS_TOTAL = Counter(
    "rugcheck_cache_misses_total",
    "Cache misses",
)


def _normalize_path(path: str) -> str:
    """Collapse /audit/<dynamic> into /audit/{mint_address} to avoid cardinality explosion."""
    if path.startswith("/audit/"):
        return "/audit/{mint_address}"
    return path


def _record_upstream_metrics(data: AggregatedData) -> None:
    """Increment per-source success/failure counters."""
    for src in data.sources_succeeded:
        UPSTREAM_SUCCESS.labels(source=src).inc()
    for src in data.sources_failed:
        UPSTREAM_FAILURE.labels(source=src).inc()


def _build_degraded_report(
    mint_address: str, data: AggregatedData, elapsed_ms: int
) -> AuditReport:
    """Build a degraded report when all upstream sources are unavailable.

    Overrides the action layer to avoid a misleading ``is_safe=True`` verdict
    that the risk engine would produce when it has no data to evaluate.
    Degraded reports are intentionally NOT cached so subsequent requests
    retry the upstream sources.
    """
    report = build_report(mint_address, data, response_time_ms=elapsed_ms)
    report.degraded = True
    report.metadata.data_completeness = "unavailable"
    report.metadata.data_age_seconds = 0
    # Override action layer — "no data" must NOT appear safe
    report.action.is_safe = False
    report.action.risk_level = RiskLevel.CRITICAL
    report.action.risk_score = 100
    report.analysis.summary = (
        "All upstream data sources are currently unavailable. "
        "Cannot assess token safety. Do NOT trade based on this report."
    )
    return report


# ---------------------------------------------------------------------------
# Internal-only endpoints — restricted to loopback
# ---------------------------------------------------------------------------

_INTERNAL_PATHS: frozenset[str] = frozenset({"/metrics", "/stats"})


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

    # Circuit breaker state — protects upstream APIs from retry storms
    cb = {
        "consecutive_failures": 0,
        "last_open_time": 0.0,       # monotonic timestamp when breaker opened
        "threshold": cfg.circuit_breaker_threshold,
        "cooldown": cfg.circuit_breaker_cooldown,
    }

    # Rate limiters — differentiated for free vs paid (gateway) users
    paid_limiter = RateLimiter(max_requests=cfg.paid_rate_limit, window_seconds=60)
    stats_limiter = RateLimiter(max_requests=10, window_seconds=60)
    free_daily = DailyQuota(max_daily=cfg.free_daily_quota)

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

    # ---- Middleware: Request-ID (outermost — runs first) ----

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or ""
        # Sanitize: strip CRLF to prevent header injection, cap length
        request_id = request_id.replace("\r", "").replace("\n", "")
        if not request_id or len(request_id) > 128:
            request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # ---- Middleware: Internal-only access control ----

    @app.middleware("http")
    async def internal_access_middleware(request: Request, call_next):
        path = request.url.path
        if path in _INTERNAL_PATHS:
            client_ip = request.client.host if request.client else "unknown"
            if client_ip not in GATEWAY_IPS:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Endpoint restricted to internal access."},
                )
        return await call_next(request)

    # ---- Middleware: Rate limiting ----

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        is_loopback = client_ip in GATEWAY_IPS

        if path.startswith("/audit"):
            if is_loopback:
                # Paid user via gateway — per-minute limit
                allowed, retry_after = await paid_limiter.check(client_ip)
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many requests. Please slow down."},
                        headers={"Retry-After": str(retry_after)},
                    )
            else:
                # Free user — daily quota
                allowed, remaining = await free_daily.check(client_ip)
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Daily free quota exceeded. "
                            "Pay via ag402 gateway for higher limits.",
                        },
                        headers={"Retry-After": "86400"},
                    )
        elif path.startswith("/stats") and not is_loopback:
            allowed, retry_after = await stats_limiter.check(client_ip)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Please slow down."},
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)

    # ---- Middleware: Prometheus metrics ----

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        path = _normalize_path(request.url.path)
        method = request.method
        t0 = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - t0
        REQUEST_COUNT.labels(method=method, path=path, status=response.status_code).inc()
        REQUEST_DURATION.labels(method=method, path=path).observe(duration)
        return response

    # ---- Circuit breaker helpers ----

    def _cb_is_open() -> bool:
        """Return True if the circuit breaker is currently open (tripped)."""
        if cb["consecutive_failures"] < cb["threshold"]:
            return False
        # Breaker is tripped — check if cooldown has elapsed
        elapsed = time.monotonic() - cb["last_open_time"]
        if elapsed >= cb["cooldown"]:
            # Allow one probe request (half-open)
            cb["consecutive_failures"] = 0
            return False
        return True

    def _cb_record_failure() -> None:
        cb["consecutive_failures"] += 1
        if cb["consecutive_failures"] >= cb["threshold"]:
            cb["last_open_time"] = time.monotonic()

    def _cb_record_success() -> None:
        cb["consecutive_failures"] = 0

    # ---- Routes ----

    @app.get("/audit/{mint_address}", response_model=AuditReport)
    async def audit(mint_address: str) -> AuditReport:
        state["total_requests"] += 1

        if not SOLANA_ADDR_RE.match(mint_address):
            raise HTTPException(status_code=400, detail="Invalid Solana address format")

        cached, data_age = await cache.get(mint_address)
        if cached is not None:
            CACHE_HIT_TOTAL.inc()
            logger.info("[AUDIT] Cache HIT for %s", mint_address[:16])
            cached.metadata.cache_hit = True
            cached.metadata.data_age_seconds = int(data_age)
            return cached

        # Circuit breaker check — if open, return degraded immediately
        if _cb_is_open():
            logger.warning("[AUDIT] Circuit breaker OPEN — returning degraded for %s", mint_address[:16])
            data = AggregatedData(
                sources_succeeded=[],
                sources_failed=["RugCheck", "DexScreener", "GoPlus"],
            )
            return _build_degraded_report(mint_address, data, elapsed_ms=0)

        agg = state["aggregator"]
        if agg is None:
            raise HTTPException(status_code=503, detail="Service not initialized")

        CACHE_MISS_TOTAL.inc()
        t0 = time.monotonic()
        try:
            data = await asyncio.wait_for(agg.aggregate(mint_address), timeout=4.5)
        except asyncio.TimeoutError:
            logger.error("[AUDIT] aggregate() hard timeout for %s", mint_address[:16])
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            data = AggregatedData(
                sources_succeeded=[],
                sources_failed=["RugCheck", "DexScreener", "GoPlus"],
            )
            _record_upstream_metrics(data)
            _cb_record_failure()
            report = _build_degraded_report(mint_address, data, elapsed_ms)
            return report
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        _record_upstream_metrics(data)

        if not data.sources_succeeded:
            _cb_record_failure()
            report = _build_degraded_report(mint_address, data, elapsed_ms)
            return report

        _cb_record_success()
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

        if agg is not None:
            has_ever_been_called = (
                agg.last_success_time is not None or agg.last_failure_time is not None
            )
            if not has_ever_been_called:
                # Server is idle — no audit requests received yet.
                # This is normal after startup, not degraded.
                status = "ok"
            elif agg.last_failure_time is not None and agg.last_success_time is None:
                # We've had failures but never a single success
                status = "degraded"
            elif agg.last_success_time is not None:
                seconds_since_success = time.monotonic() - agg.last_success_time
                if seconds_since_success > UPSTREAM_HEALTHY_WINDOW:
                    # Only degrade if there has also been a recent failure,
                    # otherwise the server is simply idle (no incoming requests).
                    if (
                        agg.last_failure_time is not None
                        and (time.monotonic() - agg.last_failure_time) < UPSTREAM_HEALTHY_WINDOW
                    ):
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

    @app.get("/metrics")
    async def metrics():
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
