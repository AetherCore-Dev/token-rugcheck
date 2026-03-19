"""Shared quota and IP resolution utilities.

Extracted from ``server.py`` so that both the audit server and the
gateway wrapper can share the same rate-limiting primitives without
duplication.

Design notes:
  - ``DailyQuota`` is async-safe (``asyncio.Lock``), single-process only.
  - ``resolve_client_ip`` trusts proxy headers only from known Cloudflare IPs
    or loopback — see the security comment in ``_is_trusted_proxy``.
  - ``QuotaResult`` is a frozen dataclass for immutability.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trusted proxy networks — Cloudflare IP ranges
# Source: https://www.cloudflare.com/ips/
# ---------------------------------------------------------------------------

_CLOUDFLARE_IPV4 = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
]
_CLOUDFLARE_IPV6 = [
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
    "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
    "2c0f:f248::/32",
]

TRUSTED_PROXY_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network(cidr) for cidr in _CLOUDFLARE_IPV4 + _CLOUDFLARE_IPV6
]


def _is_trusted_proxy(ip_str: str) -> bool:
    """Return True if *ip_str* belongs to a known trusted proxy (Cloudflare or loopback)."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    return any(addr in net for net in TRUSTED_PROXY_NETWORKS)


# ---------------------------------------------------------------------------
# QuotaResult — immutable result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaResult:
    """Immutable result of a quota check."""

    allowed: bool
    remaining: int


# ---------------------------------------------------------------------------
# Date helper (patchable for testing)
# ---------------------------------------------------------------------------


def _today() -> str:
    """Return today's date as YYYY-MM-DD in UTC. Extracted for test patching."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# DailyQuota — per-IP daily request counter
# ---------------------------------------------------------------------------


class DailyQuota:
    """Per-IP daily request quota. Resets at UTC midnight.

    Caps the number of tracked IPs via ``max_tracked_ips`` to prevent memory
    exhaustion from IP-rotation attacks.
    """

    def __init__(self, max_daily: int, max_tracked_ips: int = 50_000) -> None:
        self._max_daily = max_daily
        self._max_tracked_ips = max_tracked_ips
        # ip -> (date_str, count)
        self._counts: dict[str, tuple[str, int]] = {}
        self._lock = asyncio.Lock()

    async def evict_stale(self) -> int:
        """Remove entries from previous days. Returns number of evicted entries."""
        async with self._lock:
            today = _today()
            stale_keys = [
                ip for ip, (date_str, _) in self._counts.items()
                if date_str != today
            ]
            for ip in stale_keys:
                del self._counts[ip]
            return len(stale_keys)

    async def check(self, client_ip: str) -> QuotaResult:
        """Check and consume one unit of quota for *client_ip*.

        Returns a ``QuotaResult`` with ``allowed`` and ``remaining``.
        """
        if not client_ip or client_ip == "unknown":
            client_ip = "__unknown__"

        async with self._lock:
            today = _today()
            # Evict entries from previous days if over cap
            if len(self._counts) >= self._max_tracked_ips:
                stale_keys = [
                    ip for ip, (date_str, _) in self._counts.items()
                    if date_str != today
                ]
                for ip in stale_keys:
                    del self._counts[ip]
                # If still over cap, drop oldest entries
                if len(self._counts) >= self._max_tracked_ips:
                    excess = len(self._counts) - self._max_tracked_ips + 1
                    for ip in list(self._counts.keys())[:excess]:
                        del self._counts[ip]

            entry = self._counts.get(client_ip)
            if entry is None or entry[0] != today:
                # New day or first request — check zero-quota edge case
                if self._max_daily <= 0:
                    return QuotaResult(allowed=False, remaining=0)
                self._counts[client_ip] = (today, 1)
                return QuotaResult(allowed=True, remaining=self._max_daily - 1)

            date_str, count = entry
            if count >= self._max_daily:
                return QuotaResult(allowed=False, remaining=0)

            self._counts[client_ip] = (today, count + 1)
            return QuotaResult(
                allowed=True,
                remaining=self._max_daily - count - 1,
            )


# ---------------------------------------------------------------------------
# resolve_client_ip — proxy-aware IP extraction
# ---------------------------------------------------------------------------


def resolve_client_ip(request: Request) -> str:
    """Extract the real client IP from proxy headers.

    SECURITY: Only trust CF-Connecting-IP and X-Forwarded-For when the
    direct socket peer is a known trusted proxy (Cloudflare IP range or
    loopback).  If the socket peer is untrusted, these headers can be
    freely forged by the caller to bypass rate limiting.

    Priority (when socket peer is trusted):
      1. CF-Connecting-IP  (set by Cloudflare, single IP)
      2. X-Forwarded-For   (leftmost = original client)
      3. request.client.host (direct connection)
    """
    if request.client is None:
        return "unknown"

    socket_ip = request.client.host

    if _is_trusted_proxy(socket_ip):
        cf_ip = request.headers.get("cf-connecting-ip", "").strip()
        if cf_ip:
            try:
                addr = ipaddress.ip_address(cf_ip)
                return str(addr)  # normalized form (e.g., "2001:db8::1")
            except ValueError:
                pass  # fall through to XFF / socket_ip

        xff = request.headers.get("x-forwarded-for")
        if xff:
            real_ip = xff.split(",")[0].strip()
            if real_ip:
                try:
                    addr = ipaddress.ip_address(real_ip)
                    return str(addr)  # normalized form
                except ValueError:
                    pass  # fall through to socket_ip

    return socket_ip
