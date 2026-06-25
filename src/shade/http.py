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
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from .errors import HTTPError, RateLimitError

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

    try:
        detail = json.loads(body).get("error", {}).get("message", "")
    except Exception:
        detail = body.decode("utf-8", errors="replace")[:200]
    raise HTTPError(f"HTTP {status}: {detail}".strip(), status_code=status)


# ---------------------------------------------------------------------------
# Shared request-building
# ---------------------------------------------------------------------------

def _build_url(base_url: str, path: str) -> str:
    return f"{base_url}/{path.lstrip('/')}"


def _build_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_request(
    method: str,
    base_url: str,
    path: str,
    payload: Optional[Dict[str, Any]],
) -> Tuple[str, str, Dict[str, str], Optional[Dict[str, Any]]]:
    """Shared URL + header + payload helper for sync and async clients."""
    url = _build_url(base_url, path)
    headers = _build_headers(api_key="")
    return method.upper(), url, headers, payload





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
        method_u, url, headers, data = _build_request(
            method,
            self.base_url,
            path,
            payload,
        )
        # inject headers with api_key
        headers = _build_headers(self.api_key)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None


        req = urllib.request.Request(url, data=data, method=method_u)
        for k, v in headers.items():
            req.add_header(k, v)
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
            status, headers, body = self._execute(req)
            wait = _raise_for_status(status, headers, body, attempt, self.max_retries)
            if wait is None:
                # success
                return json.loads(body) if body else {}
            # 429 — sleep and retry
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
# Asynchronous client (httpx)
# ---------------------------------------------------------------------------

class _AsyncHTTPClient:
    """Async counterpart of ``SyncHTTPClient`` using ``httpx.AsyncClient``."""

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
        self._client = None

    async def _get_client(self):
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=_build_headers(self.api_key),
            )

        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        import asyncio

        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "httpx is required for async HTTP support. "
                "Install it with: pip install httpx"
            ) from exc

        attempt = 0
        while True:
            url = _build_url(self.base_url, path)
            client = await self._get_client()

            req_method = method.upper()

            # Use httpx request with json=payload
            resp = await client.request(req_method, url, json=payload)


            body = resp.content


            wait = _raise_for_status(
                resp.status_code, resp.headers, body, attempt, self.max_retries
            )
            if wait is None:
                return json.loads(body) if body else {}
            await asyncio.sleep(wait)
            attempt += 1


class AsyncHTTPClient(_AsyncHTTPClient):
    """Public async client (httpx-based)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )


