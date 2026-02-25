"""Base class for upstream data source fetchers."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from rugcheck.models import FetcherResult

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """Abstract fetcher — one per upstream API."""

    source_name: str = "unknown"

    def __init__(self, client: httpx.AsyncClient, timeout: float = 5.0):
        self.client = client
        self.timeout = timeout

    async def fetch(self, mint_address: str) -> FetcherResult:
        """Fetch data with unified error handling.

        An ``asyncio.wait_for`` wrapper provides a hard deadline in case the
        underlying HTTP client ignores its own timeout (e.g. DNS hangs).
        """
        hard_timeout = self.timeout + 5.0  # generous margin over per-request timeout
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

    @abstractmethod
    async def _do_fetch(self, mint_address: str) -> FetcherResult:
        """Subclass implements the actual HTTP call + parsing."""
        ...
