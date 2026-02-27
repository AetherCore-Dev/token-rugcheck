"""Tests for base fetcher error message sanitization."""

import httpx

from rugcheck.fetchers.base import BaseFetcher
from rugcheck.models import FetcherResult


class DummyFetcher(BaseFetcher):
    """Fetcher that raises configurable exceptions."""

    source_name = "Dummy"
    # Disable retries in unit tests to isolate error-handling behaviour.
    MAX_RETRIES = 0

    def __init__(self, client, exc_to_raise=None, **kwargs):
        super().__init__(client, **kwargs)
        self._exc = exc_to_raise

    async def _do_fetch(self, mint_address: str) -> FetcherResult:
        if self._exc:
            raise self._exc
        return FetcherResult(source=self.source_name, success=True, data={"ok": True})


async def test_connection_error_sanitized(httpx_mock):
    async with httpx.AsyncClient() as client:
        fetcher = DummyFetcher(client, exc_to_raise=ConnectionError("Connection refused to internal.host:8080"))
        result = await fetcher.fetch("test")
    assert result.success is False
    assert result.error == "connection_failed"
    assert "internal" not in result.error


async def test_os_error_sanitized(httpx_mock):
    async with httpx.AsyncClient() as client:
        fetcher = DummyFetcher(client, exc_to_raise=OSError("getaddrinfo failed for secret-dns.internal"))
        result = await fetcher.fetch("test")
    assert result.success is False
    assert result.error == "dns_error"
    assert "secret-dns" not in result.error


async def test_generic_exception_sanitized(httpx_mock):
    async with httpx.AsyncClient() as client:
        fetcher = DummyFetcher(
            client,
            exc_to_raise=ValueError("/home/deploy/.venv/lib/python3.12/site-packages/foo.py line 42"),
        )
        result = await fetcher.fetch("test")
    assert result.success is False
    assert result.error == "unexpected_error"
    assert "/home" not in result.error
    assert "python" not in result.error


async def test_normal_fetch_unaffected(httpx_mock):
    async with httpx.AsyncClient() as client:
        fetcher = DummyFetcher(client)
        result = await fetcher.fetch("test")
    assert result.success is True
    assert result.data == {"ok": True}
