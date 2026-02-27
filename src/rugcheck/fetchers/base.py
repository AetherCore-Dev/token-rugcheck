"""Base class for upstream data source fetchers."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from rugcheck.models import FetcherResult

logger = logging.getLogger(__name__)

# Errors that are worth retrying (transient / server-side).
_RETRYABLE_ERRORS = frozenset({"timeout", "connection_failed", "dns_error", "http_429"})


def _is_retryable(error: str) -> bool:
    """Return True if the error string indicates a transient/retryable failure."""
    if error in _RETRYABLE_ERRORS:
        return True
    # Any 5xx HTTP status is retryable
    if error.startswith("http_5"):
        return True
    return False


class BaseFetcher(ABC):
    """Abstract fetcher — one per upstream API."""

    source_name: str = "unknown"

    # Retry configuration — kept minimal so total latency fits within
    # the 4.0s aggregate timeout.  Only the fastest sources (DexScreener)
    # will realistically get a retry; slower ones will be cut off by the
    # aggregator if they exceed the budget.
    MAX_RETRIES: int = 1
    RETRY_BACKOFFS: tuple[float, ...] = (0.3,)

    def __init__(self, client: httpx.AsyncClient, timeout: float = 5.0):
        self.client = client
        self.timeout = timeout

    async def _single_fetch(self, mint_address: str) -> FetcherResult:
        """Single fetch attempt with unified error handling.

        An ``asyncio.wait_for`` wrapper provides a hard deadline in case the
        underlying HTTP client ignores its own timeout (e.g. DNS hangs).
        """
        hard_timeout = self.timeout + 1.0  # small margin over per-request timeout
        try:
            return await asyncio.wait_for(self._do_fetch(mint_address), timeout=hard_timeout)
        except asyncio.TimeoutError:
            logger.warning("[%s] hard timeout after %.1fs", self.source_name, hard_timeout)
            return FetcherResult(source=self.source_name, success=False, error="timeout")
        except httpx.TimeoutException:
            return FetcherResult(source=self.source_name, success=False, error="timeout")
        except httpx.HTTPStatusError as exc:
            return FetcherResult(
                source=self.source_name,
                success=False,
                error=f"http_{exc.response.status_code}",
            )
        except ConnectionError:
            return FetcherResult(source=self.source_name, success=False, error="connection_failed")
        except OSError:
            return FetcherResult(source=self.source_name, success=False, error="dns_error")
        except Exception:  # noqa: BLE001
            return FetcherResult(source=self.source_name, success=False, error="unexpected_error")

    async def fetch(self, mint_address: str) -> FetcherResult:
        """Fetch data with automatic retries on transient failures.

        Retries up to ``MAX_RETRIES`` times with exponential backoff for
        transient errors (429, 5xx, timeout, connection failures).
        Client errors (4xx except 429) are returned immediately.
        """
        result = await self._single_fetch(mint_address)
        if result.success:
            return result

        for attempt in range(self.MAX_RETRIES):
            if not _is_retryable(result.error):
                return result

            backoff = self.RETRY_BACKOFFS[attempt] if attempt < len(self.RETRY_BACKOFFS) else self.RETRY_BACKOFFS[-1]
            logger.info(
                "[%s] retry %d/%d after %.1fs (error=%s)",
                self.source_name, attempt + 1, self.MAX_RETRIES, backoff, result.error,
            )
            await asyncio.sleep(backoff)

            result = await self._single_fetch(mint_address)
            if result.success:
                return result

        return result

    @abstractmethod
    async def _do_fetch(self, mint_address: str) -> FetcherResult:
        """Subclass implements the actual HTTP call + parsing."""
        ...
