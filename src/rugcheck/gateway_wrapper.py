"""Quota-aware gateway wrapper.

Sits in front of the ag402 ``X402Gateway`` ASGI app and provides a free-tier
bypass: if the requesting IP still has daily quota remaining, the request is
proxied directly to the audit-server via HTTP (skipping the payment wall).
Once the daily quota is exhausted, requests fall through to the X402Gateway
which returns the normal 402 Payment Required challenge.

Architecture::

    Client → QuotaAwareGateway
                ├── free quota remaining → HTTP proxy to audit-server:8000 → 200 + X-Free-Remaining
                └── quota exhausted     → X402Gateway ASGI app → 402 Payment Required

Design decisions:
  - Free-tier requests are **HTTP-proxied** to the audit-server (not in-process).
    This ensures a single audit instance with shared cache, circuit breaker, and
    quota state — no resource duplication.
  - The X402Gateway ASGI app is delegated to in-process for the paid path.
  - ``/health`` always proxies to the audit-server (no quota consumed).
  - Proxy headers (CF-Connecting-IP, X-Forwarded-For) are stripped before
    forwarding to prevent IP spoofing at the audit-server layer.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import Counter

from rugcheck.quota import DailyQuota, resolve_client_ip

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

FREE_REQUESTS_TOTAL = Counter(
    "rugcheck_gateway_free_requests_total",
    "Free-tier requests served through quota bypass",
)
FREE_QUOTA_EXHAUSTED_TOTAL = Counter(
    "rugcheck_gateway_free_quota_exhausted_total",
    "Requests where free quota was exhausted (fell through to 402)",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Headers to strip from incoming requests before proxying.
# Prevents the audit-server from trusting forged proxy headers.
# Also strips hop-by-hop headers per RFC 7230 §6.1.
_STRIPPED_REQUEST_HEADERS: frozenset[str] = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "cf-connecting-ip",
    "x-forwarded-for",
    "x-real-ip",
})

# Timeout for proxying to the audit-server (seconds).
# Must be longer than the audit-server's internal 4.5s aggregate timeout.
_PROXY_TIMEOUT: float = 10.0

# Timeout for in-process ASGI forwarding to the gateway app (seconds).
_GATEWAY_TIMEOUT: float = 10.0

# Valid audit path patterns — only these should be forwarded to the payment gateway.
# Everything else that isn't a registered free-pass route returns 404.
_VALID_GATEWAY_PATH = re.compile(r"^/(v1/)?audit/[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ---------------------------------------------------------------------------
# QuotaAwareGateway
# ---------------------------------------------------------------------------


class QuotaAwareGateway:
    """Wraps an X402Gateway ASGI app with free-quota bypass.

    Args:
        gateway_app: The ASGI app from ``X402Gateway.create_app()`` — handles
            the 402 payment flow when free quota is exhausted.
        target_url: The HTTP URL of the audit-server (e.g.,
            ``http://audit-server:8000``). Free-tier requests are proxied here.
        daily_quota: ``DailyQuota`` instance for per-IP tracking.
        free_quota_enabled: Set to False to disable the free tier entirely
            (all requests go to the payment gateway).
    """

    def __init__(
        self,
        *,
        gateway_app: Any,
        target_url: str,
        daily_quota: DailyQuota,
        free_quota_enabled: bool = True,
        _proxy_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._gateway_app = gateway_app
        self._target_url = target_url.rstrip("/")
        self._daily_quota = daily_quota
        self._free_quota_enabled = free_quota_enabled
        self._injected_proxy_client = _proxy_client

    def create_app(self) -> FastAPI:
        """Build the composite ASGI app with quota-aware routing."""

        gateway_app = self._gateway_app
        target_url = self._target_url
        daily_quota = self._daily_quota
        free_quota_enabled = self._free_quota_enabled
        injected_client = self._injected_proxy_client

        # Long-lived HTTP client for proxying to audit-server.
        # Created in lifespan, reused for all requests.
        proxy_client_holder: dict[str, httpx.AsyncClient | None] = {"client": injected_client}
        # Long-lived ASGI client for forwarding to X402Gateway in-process.
        gateway_client_holder: dict[str, httpx.AsyncClient | None] = {"client": None}

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            if proxy_client_holder["client"] is None:
                proxy_client_holder["client"] = httpx.AsyncClient(
                    base_url=target_url,
                    timeout=httpx.Timeout(_PROXY_TIMEOUT, connect=5.0),
                    limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
                )
            # Long-lived ASGI client for forwarding to X402Gateway
            gateway_transport = httpx.ASGITransport(app=gateway_app)
            gateway_client_holder["client"] = httpx.AsyncClient(
                transport=gateway_transport,
                base_url="http://internal",
                timeout=httpx.Timeout(_GATEWAY_TIMEOUT),
            )
            # Background task: hourly cleanup of stale daily-quota entries
            cleanup_task = asyncio.create_task(_quota_cleanup_loop(daily_quota))
            logger.info("[GATEWAY-WRAPPER] Proxy client ready → %s", target_url)
            yield
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            # Only close the client if we created it (not injected for testing)
            if injected_client is None:
                client = proxy_client_holder["client"]
                if client is not None:
                    await client.aclose()
            gw_client = gateway_client_holder["client"]
            if gw_client is not None:
                await gw_client.aclose()

        app = FastAPI(
            title="Token RugCheck Gateway",
            description="Quota-aware payment gateway",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            lifespan=lifespan,
        )

        # --- Free-pass routes: always proxy to audit-server, no quota consumed ---

        @app.get("/health")
        async def health(request: Request):
            """Health check — always proxied to audit backend, no quota consumed."""
            client = proxy_client_holder["client"]
            if client is None:
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "detail": "Proxy not initialized"},
                )
            try:
                resp = await client.get("/health")
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers={"content-type": resp.headers.get("content-type", "application/json")},
                )
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning("[GATEWAY-WRAPPER] Health proxy failed: %s", exc)
                return JSONResponse(
                    status_code=503,
                    content={"status": "unavailable", "detail": "Audit server unreachable"},
                )

        @app.get("/playground")
        async def playground(request: Request):
            """Playground — always proxied to audit backend, no quota consumed."""
            client = proxy_client_holder["client"]
            if client is None:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Proxy not initialized"},
                )
            try:
                resp = await client.get("/playground")
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers={"content-type": resp.headers.get("content-type", "text/html")},
                )
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning("[GATEWAY-WRAPPER] Playground proxy failed: %s", exc)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Audit server unreachable"},
                )

        # --- Free-pass: trending (no quota consumed) ---

        @app.get("/v1/trending")
        async def trending_proxy(request: Request):
            """Trending tokens — always proxied to audit backend, no quota consumed."""
            client = proxy_client_holder["client"]
            if client is None:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Proxy not initialized"},
                )
            try:
                resp = await client.get("/v1/trending")
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers={"content-type": resp.headers.get("content-type", "application/json")},
                )
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                logger.warning("[GATEWAY-WRAPPER] Trending proxy failed: %s", exc)
                return JSONResponse(
                    status_code=502,
                    content={"detail": "Trending data unavailable"},
                )

        # --- Audit endpoints: quota-gated ---

        @app.api_route("/v1/audit/{mint_address}", methods=["GET"])
        @app.api_route("/audit/{mint_address}", methods=["GET"])
        async def audit_with_quota(request: Request, mint_address: str):
            """Audit endpoint with free-quota bypass."""
            client_ip = resolve_client_ip(request)

            # If free quota disabled, fall through to gateway immediately
            if not free_quota_enabled:
                return await _forward_to_gateway(
                    request, gateway_client_holder["client"], gateway_app,
                )

            # Check quota
            result = await daily_quota.check(client_ip)

            if result.allowed:
                FREE_REQUESTS_TOTAL.inc()
                # Proxy to audit-server via HTTP (free path)
                proxy_client = proxy_client_holder["client"]
                if proxy_client is None:
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Gateway proxy not initialized"},
                    )
                try:
                    response = await _proxy_to_audit(
                        request, proxy_client, _build_forward_url(request),
                    )
                    response.headers["X-Free-Remaining"] = str(result.remaining)
                    response.headers["X-Quota-Tier"] = "free"
                    return response
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    logger.warning(
                        "[GATEWAY-WRAPPER] Free-tier proxy failed for %s: %s — falling through to payment",
                        client_ip, exc,
                    )
                    # Graceful degradation: fall through to 402 payment path
                    return await _forward_to_gateway(
                        request, gateway_client_holder["client"], gateway_app,
                    )
            else:
                FREE_QUOTA_EXHAUSTED_TOTAL.inc()
                logger.info(
                    "[GATEWAY-WRAPPER] Free quota exhausted for %s — falling through to payment",
                    client_ip,
                )
                return await _forward_to_gateway(
                    request, gateway_client_holder["client"], gateway_app,
                )

        # --- Catch-all: 404 for unknown paths, forward valid audit paths to gateway ---

        @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def catch_all(request: Request, path: str):
            """Unknown paths return 404; valid audit paths go to payment gateway."""
            if _VALID_GATEWAY_PATH.match(request.url.path):
                return await _forward_to_gateway(
                    request, gateway_client_holder["client"], gateway_app,
                )
            return JSONResponse(
                status_code=404,
                content={"detail": "Not found"},
            )

        return app


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------


async def _quota_cleanup_loop(quota: DailyQuota) -> None:
    """Periodically evict stale DailyQuota entries (every hour).

    Runs once immediately at startup, then every hour thereafter.
    """
    while True:
        try:
            evicted = await quota.evict_stale()
            if evicted:
                logger.info("[GATEWAY-WRAPPER] Evicted %d stale quota entries", evicted)
        except Exception:
            logger.exception("[GATEWAY-WRAPPER] Error during quota cleanup")
        await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_forward_url(request: Request) -> str:
    """Build the forwarding URL preserving path and query string.

    Normalizes the path to ensure a single leading slash, preventing
    protocol-relative URL issues (e.g., ``//path``).
    """
    path = "/" + request.url.path.lstrip("/")
    query = request.url.query
    if query:
        return f"{path}?{query}"
    return path


def _safe_request_headers(request: Request) -> dict[str, str]:
    """Build a sanitized header dict, stripping proxy and hop-by-hop headers.

    SECURITY: CF-Connecting-IP and X-Forwarded-For are stripped to prevent
    the audit-server from trusting forged values.
    """
    return {
        k: v for k, v in request.headers.items()
        if k.lower() not in _STRIPPED_REQUEST_HEADERS
    }


async def _proxy_to_audit(
    request: Request,
    client: httpx.AsyncClient,
    url: str,
) -> Response:
    """Proxy a request to the audit-server via HTTP.

    Uses the long-lived ``httpx.AsyncClient`` for connection pooling.
    """
    body = await request.body()
    resp = await client.request(
        method=request.method,
        url=url,
        headers=_safe_request_headers(request),
        content=body if body else None,
    )

    # Forward response with safe headers.
    # NOTE: content-encoding is intentionally excluded — httpx transparently
    # decompresses the body, so forwarding the original encoding header
    # would cause clients to attempt double-decompression.
    response_headers: dict[str, str] = {}
    for key in ("content-type", "x-request-id", "deprecation", "link",
                "cache-control", "etag", "vary"):
        val = resp.headers.get(key)
        if val is not None:
            response_headers[key] = val

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
    )


async def _forward_to_gateway(
    request: Request,
    gateway_client: httpx.AsyncClient | None,
    gateway_app: Any,
) -> Response:
    """Forward a request to the X402Gateway ASGI app in-process.

    Uses the long-lived ``gateway_client`` when available; falls back to
    creating a one-shot client if the lifespan client is not yet ready.
    """
    body = await request.body()

    async def _do_request(client: httpx.AsyncClient) -> httpx.Response:
        return await client.request(
            method=request.method,
            url=_build_forward_url(request),
            headers=_safe_request_headers(request),
            content=body if body else None,
        )

    try:
        if gateway_client is not None:
            resp = await _do_request(gateway_client)
        else:
            # Fallback: one-shot client (should not happen in normal operation)
            transport = httpx.ASGITransport(app=gateway_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://internal",
                timeout=httpx.Timeout(_GATEWAY_TIMEOUT),
            ) as client:
                resp = await _do_request(client)
    except (asyncio.TimeoutError, httpx.HTTPError) as exc:
        logger.error("[GATEWAY-WRAPPER] Gateway forward failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Payment gateway unavailable"},
        )

    # Filter hop-by-hop headers that must not be forwarded
    safe_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in {"transfer-encoding", "connection", "content-length"}
    }
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=safe_headers,
    )
