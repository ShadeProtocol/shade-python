"""
Low-level HTTP transport for the Shade SDK.

Handles:
* HTTP 429 rate-limit detection and ``Retry-After`` parsing
* Automatic retry with ``Retry-After`` wait (or exponential back-off fallback)
* Sync (``urllib.request``) and async (``httpx.AsyncClient``) paths

Both clients share a common ``_build_request`` helper so URL-building,
header logic, and payload encoding stay in one place.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

import httpx

from . import config as _config
from .config import DEFAULT_MAX_RETRIES, validate_client_settings
from .errors import (
    AuthenticationError,
    HTTPError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ShadeError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
# Shared request builder
# ---------------------------------------------------------------------------

def _build_request(
    method: str,
    path: str,
    base_url: str,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build common request parameters shared by sync and async clients.

    Returns a dict with keys:
    - ``url``: fully-qualified URL
    - ``headers``: dict with Authorization, Content-Type, Accept
    - ``json``: the payload dict (or ``None``)
    - ``method``: uppercased HTTP method
    """
    url = f"{base_url}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return {
        "url": url,
        "headers": headers,
        "json": payload,
        "method": method.upper(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_retry_after(headers: Any) -> Optional[int]:
    """Return integer seconds from a ``Retry-After`` header, or ``None``."""
    value = None
    # urllib HTTPMessage / http.client.HTTPMessage / httpx.Headers
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
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, (ConnectionResetError, TimeoutError, urllib.error.URLError)):
        return True
    return False


def _is_retryable_status(status: int) -> bool:
    return status in {502, 503, 504}


def _is_retryable_error(exc: Exception) -> bool:
    if _is_retryable_transport_error(exc):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
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
        raise NetworkError(
            f"Request failed with transient server error: {status}",
            status_code=status,
        )

    try:
        detail = json.loads(body).get("error", {}).get("message", "")
    except Exception:
        detail = body.decode("utf-8", errors="replace")[:200]
    raise HTTPError(f"HTTP {status}: {detail}".strip(), status_code=status)


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


# ---------------------------------------------------------------------------
# Single response parser
# ---------------------------------------------------------------------------

def _error_message(data: Any, default: str) -> str:
    """Extract a human-readable message from a parsed error body.

    Handles the common shapes ``{"error": {"message": ...}}``,
    ``{"error": "..."}`` and ``{"message": ...}``. Falls back to *default*
    when nothing usable is present (including when the body failed to decode).
    """
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if message:
                return str(message)
        elif isinstance(err, str) and err:
            return err
        message = data.get("message")
        if message:
            return str(message)
    return default


def _field_errors(data: Any) -> Optional[Any]:
    """Extract field-level validation errors from a parsed error body, if any.

    Looks for ``fields``/``field_errors``/``errors`` either nested under
    ``error`` or at the top level. Returns ``None`` when absent.
    """
    candidates = []
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            candidates.append(err)
        candidates.append(data)
    for source in candidates:
        for key in ("fields", "field_errors", "errors"):
            fields = source.get(key)
            if fields:
                return fields
    return None


def _parse_response(response: httpx.Response) -> Dict[str, Any]:
    """Parse an ``httpx.Response`` into a dict, mapping errors to typed exceptions.

    This is the single funnel every resource method should route responses
    through. Centralizing JSON decoding, success detection, and the mapping of
    HTTP status codes to the SDK's typed exception hierarchy here keeps error
    handling from drifting between resources.

    Parameters
    ----------
    response : httpx.Response
        The response returned by an httpx request.

    Returns
    -------
    dict
        The decoded JSON body of a successful (2xx) response.

    Raises
    ------
    AuthenticationError
        For HTTP 401/403.
    InvalidRequestError
        For HTTP 400/422, carrying field-level errors when the body provides
        them.
    NotFoundError
        For HTTP 404.
    RateLimitError
        For HTTP 429.
    NetworkError
        For HTTP 5xx (subject to retry by callers).
    HTTPError
        For any other non-2xx status not covered above.
    ShadeError
        When a 2xx body cannot be decoded as JSON, or a 2xx body itself
        carries an ``error`` key. The raw body and HTTP status are attached to
        every raised exception.
    """
    status = response.status_code
    body = response.text

    # Decode up-front so the raw body can drive both error mapping and the
    # success path. A decode failure is captured rather than raised here so
    # error statuses still produce their typed exception with the raw body.
    try:
        data: Any = json.loads(body) if body else {}
        decoded = True
    except (json.JSONDecodeError, ValueError):
        data = None
        decoded = False

    if 200 <= status < 300:
        if not decoded:
            raise ShadeError(
                "Invalid response from API",
                status_code=status,
                response_body=body,
            )
        if not isinstance(data, dict):
            raise ShadeError(
                "Invalid response from API",
                status_code=status,
                response_body=body,
            )
        # A 2xx body that still carries an error is treated as a failure.
        if data.get("error"):
            raise ShadeError(
                _error_message(data, "API returned an error"),
                status_code=status,
                response_body=body,
            )
        return data

    if status in (401, 403):
        raise AuthenticationError(
            _error_message(data, "Authentication failed"),
            status_code=status,
            response_body=body,
        )

    if status in (400, 422):
        raise InvalidRequestError(
            _error_message(data, "Invalid request"),
            status_code=status,
            response_body=body,
            field_errors=_field_errors(data),
        )

    if status == 404:
        raise NotFoundError(
            _error_message(data, "Resource not found"),
            status_code=status,
            response_body=body,
        )

    if status == 429:
        raise RateLimitError(
            _error_message(data, "Rate limit exceeded"),
            retry_after=_parse_retry_after(response.headers),
            status_code=status,
            response_body=body,
        )

    if 500 <= status < 600:
        raise NetworkError(
            _error_message(data, f"Server error: {status}"),
            status_code=status,
            response_body=body,
        )

    # Any other non-2xx status (e.g. 3xx, uncommon 4xx) still maps to a typed
    # exception so nothing escapes the funnel unhandled.
    raise HTTPError(
        _error_message(data, f"HTTP {status}"),
        status_code=status,
        response_body=body,
    )


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
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> None:
        _validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = _config.max_retries if max_retries is None else max_retries
        self.timeout = _config.timeout if timeout is None else timeout
        validate_client_settings(self.timeout, self.max_retries)

    def _build_request(
        self, method: str, path: str, payload: Optional[Dict[str, Any]]
    ) -> urllib.request.Request:
        """Build a ``urllib.request.Request`` for sync execution."""
        params = _build_request(method, path, self.base_url, self.api_key, payload)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            params["url"], data=data, method=params["method"]
        )
        for key, value in params["headers"].items():
            req.add_header(key, value)
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
# Asynchronous client (httpx.AsyncClient)
# ---------------------------------------------------------------------------

class AsyncHTTPClient:
    """
    Async counterpart of ``SyncHTTPClient``. Uses ``httpx.AsyncClient``.

    Parameters
    ----------
    Same as ``SyncHTTPClient``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> None:
        _validate_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = _config.max_retries if max_retries is None else max_retries
        self.timeout = _config.timeout if timeout is None else timeout
        validate_client_settings(self.timeout, self.max_retries)
        self._client: Optional[httpx.AsyncClient] = None
        self._owns_client = True

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the shared AsyncClient instance, creating it if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """Explicitly close the underlying AsyncClient."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncHTTPClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

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
        RateLimitError
            If 429 and ``max_retries`` is exhausted.
        HTTPError
            For other non-2xx responses.
        """
        params = _build_request(method, path, self.base_url, self.api_key, payload)
        client = await self._get_client()
        attempt = 0

        while True:
            try:
                response = await client.request(
                    params["method"],
                    params["url"],
                    headers=params["headers"],
                    json=params["json"],
                )
                body = response.content
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
                response.status_code,
                response.headers,
                body,
                attempt,
                self.max_retries,
            )
            if wait is None:
                return json.loads(body) if body else {}
            await asyncio.sleep(wait)
            attempt += 1
