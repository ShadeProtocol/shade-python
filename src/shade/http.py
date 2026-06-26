"""
Low-level HTTP transport for the Shade SDK.

Handles:
* HTTP 429 rate-limit detection and ``Retry-After`` parsing
* Automatic retry with ``Retry-After`` wait (or exponential back-off fallback)
* Sync (``urllib.request``) and async (``asyncio`` + ``aiohttp`` if available,
  otherwise raises ``ImportError`` with a helpful message) paths
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .errors import (
    AuthenticationError,
    HTTPError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 3
_BASE_BACKOFF: float = 1.0   # seconds for exponential back-off base
_MAX_BACKOFF: float = 60.0   # cap individual wait at 60 s


def _validate_base_url(url: str) -> None:
    """Raise ValueError if *url* is not an absolute http/https URL."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url must use http:// or https://, got: {url!r}"
        )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_retry_after(headers: Any) -> Optional[int]:
    """Return integer seconds from a ``Retry-After`` header, or ``None``."""
    value = None
    # urllib HTTPMessage / http.client.HTTPMessage
    if hasattr(headers, "get"):
        value = headers.get("Retry-After") or headers.get("retry-after")
    elif isinstance(headers, dict):
        value = headers.get("Retry-After") or headers.get("retry-after")

    if value is None:
        return None
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return None


def _backoff_seconds(attempt: int) -> float:
    """Exponential back-off: 1, 2, 4, … capped at ``_MAX_BACKOFF``."""
    return min(_BASE_BACKOFF * math.pow(2, attempt), _MAX_BACKOFF)


def _retry_delay(attempt: int, base_delay: float) -> float:
    """Return a capped exponential delay with randomized jitter."""
    return min(base_delay * (2**attempt) + random.uniform(0, 0.5), _MAX_BACKOFF)


def _is_retryable_transport_error(exc: Exception) -> bool:
    """Return True for transient network failures that should be retried."""
    if httpx is not None and isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True

    try:
        import aiohttp
    except ImportError:
        aiohttp = None

    if aiohttp is not None and isinstance(
        exc,
        (
            aiohttp.ClientConnectionError,
            aiohttp.ClientConnectorError,
            aiohttp.ClientOSError,
            aiohttp.ServerDisconnectedError,
        ),
    ):
        return True

    if isinstance(exc, (ConnectionResetError, TimeoutError, urllib.error.URLError)):
        return True
    return False


def _is_retryable_status(status: int) -> bool:
    return status in {502, 503, 504}


def _retry_with_backoff(fn, max_retries: int, base_delay: float):
    """Execute *fn* and retry transient failures with exponential back-off."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable_error(exc):
                raise
            delay = _retry_delay(attempt, base_delay)
            logger.debug(
                "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                attempt + 1,
                max_retries + 1,
                delay,
            )
            time.sleep(delay)


def _is_retryable_error(exc: Exception) -> bool:
    if _is_retryable_transport_error(exc):
        return True

    if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
        return _is_retryable_status(exc.response.status_code)

    if isinstance(exc, HTTPError):
        return _is_retryable_status(exc.status_code or 0)

    return False


def _raise_for_status(
    status: int,
    headers: Any,
    body: bytes,
    attempt: int,
    max_retries: int,
) -> Optional[int]:
    """
    Inspect *status* and decide what to do.

    Returns
    -------
    int | None
        Seconds to wait before retrying, or ``None`` if the call succeeded.

    Raises
    ------
    RateLimitError
        If HTTP 429 and retries are exhausted (or auto-retry is off).
    InvalidRequestError
        For HTTP 400 responses.
    AuthenticationError
        For HTTP 401/403 responses.
    NotFoundError
        For HTTP 404 responses.
    NetworkError
        For transient 502/503/504 responses after retries are exhausted.
    HTTPError
        For any other non-2xx status.
    """
    if 200 <= status < 300:
        return None  # success

    if status == 429:
        retry_after = _parse_retry_after(headers)
        if attempt < max_retries:
            wait = retry_after if retry_after is not None else _backoff_seconds(attempt)
            return wait  # signal: "sleep this long, then retry"
        # exhausted
        try:
            detail = json.loads(body).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        msg = f"Rate limit exceeded. {detail}".strip()
        raise RateLimitError(msg, retry_after=retry_after)

    if status == 400:
        raise InvalidRequestError("Invalid request", status_code=status)

    if status in {401, 403}:
        raise AuthenticationError("Authentication failed", status_code=status)

    if status == 404:
        response_body = body.decode("utf-8", errors="replace")
        raise NotFoundError(
            "Resource not found",
            status_code=status,
            response_body=response_body,
        )

    if status in {502, 503, 504}:
        if attempt < max_retries:
            wait = _retry_delay(attempt, _BASE_BACKOFF)
            logger.debug(
                "Retrying request after server error (attempt %s/%s) in %.3fs",
                attempt + 1,
                max_retries + 1,
                wait,
            )
            return wait
        raise NetworkError(f"Request failed with transient server error: {status}", status_code=status)

    try:
        detail = json.loads(body).get("error", {}).get("message", "")
    except Exception:
        detail = body.decode("utf-8", errors="replace")[:200]
    raise HTTPError(f"HTTP {status}: {detail}".strip(), status_code=status)


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------

class SyncHTTPClient:
    """
    Thin synchronous HTTP client with built-in 429 handling.

    Parameters
    ----------
    base_url : str
        Base URL (no trailing slash).
    api_key : str
        Bearer token sent as ``Authorization: Bearer <api_key>``.
    max_retries : int
        How many times to retry on 429 before raising ``RateLimitError``.
        Set to ``0`` to disable auto-retry.
    timeout : float
        Socket timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 30.0,
    ) -> None:
        _validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout

    def _build_request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]]
    ) -> urllib.request.Request:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        return req

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an HTTP request, retrying on 429 as configured.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        RateLimitError
            If 429 and ``max_retries`` is exhausted.
        HTTPError
            For other non-2xx responses.
        """
        attempt = 0
        while True:
            req = self._build_request(method, path, payload)
            try:
                status, headers, body = self._execute(req)
            except Exception as exc:
                if _is_retryable_transport_error(exc):
                    if attempt >= self.max_retries:
                        raise NetworkError(
                            "Request failed after exhausting retries",
                            status_code=None,
                        ) from exc
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        self.max_retries + 1,
                        delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise
            wait = _raise_for_status(status, headers, body, attempt, self.max_retries)
            if wait is None:
                return json.loads(body) if body else {}
            time.sleep(wait)
            attempt += 1

    def _execute(
        self, req: urllib.request.Request
    ) -> Tuple[int, Any, bytes]:
        """Send *req* and return (status, headers, body)."""
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, exc.headers, body


# ---------------------------------------------------------------------------
# Asynchronous client
# ---------------------------------------------------------------------------

class AsyncHTTPClient:
    """
    Async counterpart of ``SyncHTTPClient``.  Uses ``aiohttp`` under the hood.

    Parameters
    ----------
    Same as ``SyncHTTPClient``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 30.0,
    ) -> None:
        _validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Async HTTP request with 429 retry using ``asyncio.sleep``.

        Returns
        -------
        dict
            Parsed JSON response body.

        Raises
        ------
        RateLimitError, HTTPError
            Same semantics as ``SyncHTTPClient.request``.
        ImportError
            If ``aiohttp`` is not installed.
        """
        import asyncio  # stdlib — always available

        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError(
                "aiohttp is required for async support. "
                "Install it with: pip install aiohttp"
            ) from exc

        url_base = self.base_url
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        connector = aiohttp.TCPConnector()
        timeout_cfg = aiohttp.ClientTimeout(total=self.timeout)

        attempt = 0
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout_cfg
        ) as session:
            while True:
                url = f"{url_base}/{path.lstrip('/')}"
                try:
                    resp = await session.request(
                        method.upper(),
                        url,
                        json=payload,
                        headers=headers,
                    )
                    body = await resp.read()
                except Exception as exc:
                    if _is_retryable_transport_error(exc):
                        if attempt >= self.max_retries:
                            raise NetworkError(
                                "Request failed after exhausting retries",
                                status_code=None,
                            ) from exc
                        delay = _retry_delay(attempt, _BASE_BACKOFF)
                        logger.debug(
                            "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                            attempt + 1,
                            self.max_retries + 1,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                    raise
                wait = _raise_for_status(
                    resp.status, resp.headers, body, attempt, self.max_retries
                )
                if wait is None:
                    return json.loads(body) if body else {}
                await asyncio.sleep(wait)
                attempt += 1