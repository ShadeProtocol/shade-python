"""
Tests for rate-limit handling (issue #11).

Covers:
* RateLimitError has retry_after attribute
* Sync client retries on 429 and respects Retry-After header
* Sync client falls back to exponential back-off when header is absent
* Sync client raises RateLimitError once max_retries is exhausted
* Async client uses asyncio.sleep (non-blocking)
* Async client raises RateLimitError once max_retries is exhausted
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from http.client import HTTPMessage
from io import BytesIO
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shade import RateLimitError
from shade.errors import HTTPError, InvalidRequestError, NetworkError
from shade.http import (
    DEFAULT_MAX_RETRIES,
    AsyncHTTPClient,
    SyncHTTPClient,
    _backoff_seconds,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def _make_headers(retry_after: Optional[str] = None) -> dict:
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return headers


def _fake_429_body(message: str = "rate limit") -> bytes:
    return json.dumps({"error": {"message": message}}).encode()


def _fake_200_body() -> bytes:
    return json.dumps({"id": "pay_123", "status": "ok"}).encode()


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def test_integer_header(self):
        assert _parse_retry_after({"Retry-After": "30"}) == 30

    def test_lowercase_header(self):
        assert _parse_retry_after({"retry-after": "5"}) == 5

    def test_absent_header(self):
        assert _parse_retry_after({}) is None

    def test_non_integer_value_returns_none(self):
        assert _parse_retry_after({"Retry-After": "Mon, 30 Jun 2026 12:00:00 GMT"}) is None

    def test_negative_clamped_to_zero(self):
        assert _parse_retry_after({"Retry-After": "-1"}) == 0


# ---------------------------------------------------------------------------
# RateLimitError
# ---------------------------------------------------------------------------

class TestRateLimitError:
    def test_has_retry_after_attribute(self):
        err = RateLimitError("too many requests", retry_after=42)
        assert err.retry_after == 42

    def test_retry_after_none_when_absent(self):
        err = RateLimitError("too many requests")
        assert err.retry_after is None

    def test_status_code_is_429(self):
        err = RateLimitError("oops")
        assert err.status_code == 429

    def test_is_http_error_subclass(self):
        assert isinstance(RateLimitError("x"), HTTPError)


# ---------------------------------------------------------------------------
# SyncHTTPClient — helpers
# ---------------------------------------------------------------------------

def _make_urllib_error(status: int, headers: dict, body: bytes) -> urllib.error.HTTPError:
    """Build a urllib HTTPError that looks like a real one."""
    msg = MagicMock()
    msg.read.return_value = body
    msg.get.side_effect = lambda k, d=None: headers.get(k, d)
    err = urllib.error.HTTPError(
        url="https://example.com",
        code=status,
        msg="",
        hdrs=msg,
        fp=BytesIO(body),
    )
    err.headers = msg
    return err


class TestSyncHTTPClientRateLimit:
    """Tests for the synchronous client."""

    def _client(self, max_retries: int = DEFAULT_MAX_RETRIES) -> SyncHTTPClient:
        return SyncHTTPClient(
            base_url="https://api.example.com",
            api_key="test-key",
            max_retries=max_retries,
        )

    # -- retry succeeds on second attempt -----------------------------------

    def test_retries_on_429_then_succeeds(self):
        client = self._client(max_retries=3)
        responses = [
            (429, {"Retry-After": "1"}, _fake_429_body()),
            (200, {}, _fake_200_body()),
        ]
        call_count = 0

        def fake_execute(req):
            nonlocal call_count
            status, hdrs, body = responses[call_count]
            call_count += 1
            return status, hdrs, body

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            result = client.request("POST", "/payments", {})

        assert result == {"id": "pay_123", "status": "ok"}
        assert call_count == 2
        mock_sleep.assert_called_once_with(1)  # waited Retry-After seconds

    # -- waits at least Retry-After seconds ---------------------------------

    def test_sleep_duration_matches_retry_after(self):
        client = self._client(max_retries=1)
        responses = [
            (429, {"Retry-After": "7"}, _fake_429_body()),
            (200, {}, _fake_200_body()),
        ]
        idx = 0

        def fake_execute(req):
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            client.request("GET", "/payments")

        mock_sleep.assert_called_once_with(7)

    # -- falls back to exponential back-off when header absent ---------------

    def test_exponential_backoff_when_no_retry_after_header(self):
        client = self._client(max_retries=2)
        responses = [
            (429, {}, _fake_429_body()),   # attempt 0 → backoff(0) = 1 s
            (429, {}, _fake_429_body()),   # attempt 1 → backoff(1) = 2 s
            (200, {}, _fake_200_body()),
        ]
        idx = 0

        def fake_execute(req):
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        sleep_calls: List[float] = []
        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            client.request("GET", "/payments")

        assert sleep_calls == [_backoff_seconds(0), _backoff_seconds(1)]

    # -- raises RateLimitError when max_retries exhausted -------------------

    def test_raises_rate_limit_error_when_retries_exhausted(self):
        client = self._client(max_retries=2)
        responses = [
            (429, {"Retry-After": "5"}, _fake_429_body()),
            (429, {"Retry-After": "5"}, _fake_429_body()),
            (429, {"Retry-After": "5"}, _fake_429_body()),
        ]
        idx = 0

        def fake_execute(req):
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep"):
            with pytest.raises(RateLimitError) as exc_info:
                client.request("POST", "/payments", {})

        assert exc_info.value.retry_after == 5
        assert exc_info.value.status_code == 429

    # -- zero max_retries raises immediately --------------------------------

    def test_no_retry_when_max_retries_zero(self):
        client = self._client(max_retries=0)

        def fake_execute(req):
            return 429, {"Retry-After": "3"}, _fake_429_body()

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(RateLimitError) as exc_info:
                client.request("POST", "/payments", {})

        mock_sleep.assert_not_called()
        assert exc_info.value.retry_after == 3

    # -- non-429 HTTP errors are not retried --------------------------------

    def test_non_429_raises_http_error_immediately(self):
        client = self._client(max_retries=3)

        def fake_execute(req):
            return 500, {}, b'{"error":{"message":"server error"}}'

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(HTTPError) as exc_info:
                client.request("GET", "/payments")

        mock_sleep.assert_not_called()
        assert exc_info.value.status_code == 500

    # -- retry_after attribute is None when header absent ------------------

    def test_rate_limit_error_retry_after_none_when_no_header(self):
        client = self._client(max_retries=0)

        def fake_execute(req):
            return 429, {}, _fake_429_body()

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep"):
            with pytest.raises(RateLimitError) as exc_info:
                client.request("POST", "/payments", {})

        assert exc_info.value.retry_after is None

    def test_retries_on_transient_5xx_then_succeeds(self):
        client = self._client(max_retries=2)
        responses = [
            (503, {}, b"{}"),
            (503, {}, b"{}"),
            (200, {}, _fake_200_body()),
        ]
        idx = 0

        def fake_execute(req):
            nonlocal idx
            status, headers, body = responses[idx]
            idx += 1
            return status, headers, body

        sleep_calls: List[float] = []
        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
             patch("shade.http.random.uniform", side_effect=[0.0, 0.0]):
            result = client.request("GET", "/payments")

        assert result == {"id": "pay_123", "status": "ok"}
        assert sleep_calls == [1.0, 2.0]

    def test_400_raises_invalid_request_error_immediately(self):
        client = self._client(max_retries=3)

        def fake_execute(req):
            return 400, {}, b'{"error": {"message": "bad request"}}'

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(InvalidRequestError) as exc_info:
                client.request("GET", "/payments")

        mock_sleep.assert_not_called()
        assert exc_info.value.status_code == 400

    def test_retries_exhausted_raise_network_error(self):
        client = self._client(max_retries=1)
        responses = [
            (503, {}, b"{}"),
            (503, {}, b"{}"),
            (503, {}, b"{}"),
        ]
        idx = 0

        def fake_execute(req):
            nonlocal idx
            status, headers, body = responses[idx]
            idx += 1
            return status, headers, body

        with patch.object(client, "_execute", side_effect=fake_execute), \
             patch("time.sleep") as mock_sleep:
            with pytest.raises(NetworkError) as exc_info:
                client.request("GET", "/payments")

        assert exc_info.value.status_code == 503
        assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# AsyncHTTPClient
# ---------------------------------------------------------------------------

class TestAsyncHTTPClientRateLimit:
    """Tests for the async client — uses asyncio.sleep (non-blocking)."""

    def _client(self, max_retries: int = DEFAULT_MAX_RETRIES) -> AsyncHTTPClient:
        return AsyncHTTPClient(
            base_url="https://api.example.com",
            api_key="test-key",
            max_retries=max_retries,
        )

    def _run(self, coro):
        return asyncio.run(coro)

    # -- async retries on 429 then succeeds ---------------------------------

    def test_async_retries_on_429_then_succeeds(self):
        client = self._client(max_retries=3)

        responses = [
            (429, {"Retry-After": "2"}, _fake_429_body()),
            (200, {}, _fake_200_body()),
        ]

        async def run():
            aiohttp = pytest.importorskip("aiohttp")
            idx = 0

            async def fake_request(*args, **kwargs):
                nonlocal idx
                status, hdrs, body = responses[idx]
                idx += 1
                mock_resp = AsyncMock()
                mock_resp.status = status
                mock_resp.headers = hdrs
                mock_resp.read = AsyncMock(return_value=body)
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=False)
                return mock_resp

            sleep_calls = []

            with patch("aiohttp.ClientSession") as mock_session_cls, \
                 patch("asyncio.sleep", new_callable=AsyncMock,
                       side_effect=lambda s: sleep_calls.append(s)):
                mock_session = AsyncMock()
                mock_session.request = fake_request
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session_cls.return_value = mock_session

                result = await client.request("POST", "/payments", {})

            assert result == {"id": "pay_123", "status": "ok"}
            assert sleep_calls == [2]  # asyncio.sleep, not time.sleep

        self._run(run())

    # -- async raises RateLimitError when exhausted -------------------------

    def test_async_raises_rate_limit_error_when_exhausted(self):
        client = self._client(max_retries=1)

        async def run():
            aiohttp = pytest.importorskip("aiohttp")
            call_count = 0

            async def fake_request(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                mock_resp = AsyncMock()
                mock_resp.status = 429
                mock_resp.headers = {"Retry-After": "10"}
                mock_resp.read = AsyncMock(return_value=_fake_429_body())
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=False)
                return mock_resp

            with patch("aiohttp.ClientSession") as mock_session_cls, \
                 patch("asyncio.sleep", new_callable=AsyncMock):
                mock_session = AsyncMock()
                mock_session.request = fake_request
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session_cls.return_value = mock_session

                with pytest.raises(RateLimitError) as exc_info:
                    await client.request("POST", "/payments", {})

            assert exc_info.value.retry_after == 10
            # 1 initial + 1 retry = 2 total attempts
            assert call_count == 2

        self._run(run())

    # -- async uses asyncio.sleep, not time.sleep ---------------------------

    def test_async_uses_asyncio_sleep_not_time_sleep(self):
        client = self._client(max_retries=1)

        async def run():
            pytest.importorskip("aiohttp")
            responses = [
                (429, {"Retry-After": "3"}, _fake_429_body()),
                (200, {}, _fake_200_body()),
            ]
            idx = 0

            async def fake_request(*args, **kwargs):
                nonlocal idx
                status, hdrs, body = responses[idx]
                idx += 1
                mock_resp = AsyncMock()
                mock_resp.status = status
                mock_resp.headers = hdrs
                mock_resp.read = AsyncMock(return_value=body)
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=False)
                return mock_resp

            with patch("aiohttp.ClientSession") as mock_session_cls, \
                 patch("asyncio.sleep", new_callable=AsyncMock) as mock_async_sleep, \
                 patch("time.sleep") as mock_sync_sleep:
                mock_session = AsyncMock()
                mock_session.request = fake_request
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session_cls.return_value = mock_session

                await client.request("POST", "/payments", {})

            mock_async_sleep.assert_called_once_with(3)
            mock_sync_sleep.assert_not_called()

        self._run(run())