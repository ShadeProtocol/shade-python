from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any, Dict, Mapping, Optional

import httpx

from ._debug import log_request, log_response
from .config import Environment, get_config, validate_client_settings
from .config import config as _config
from .errors import NetworkError, ShadeError
from .http import (
    _BASE_BACKOFF,
    _is_retryable_error,
    _is_retryable_status,
    _parse_response,
    _parse_retry_after,
    _retry_delay,
)

logger = logging.getLogger(__name__)


def _build_full_url(base: str, path: str) -> str:
    """Combine a base URL and a path, avoiding double or missing slashes.

    Examples
    --------
    >>> _build_full_url("https://api.example.com", "/users")
    'https://api.example.com/users'
    >>> _build_full_url("https://api.example.com/", "users")
    'https://api.example.com/users'
    """
    if not base:
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")


class _BaseHTTPClient:
    """Base class for HTTP clients centralising common configuration and request preparation."""

    _IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
    _IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        environment: Optional[Environment | str] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.api_key = api_key
        self._api_base = api_base.rstrip("/") if api_base else None
        self.environment = environment
        self._timeout = timeout
        self._max_retries = max_retries
        if timeout is not None or max_retries is not None:
            validate_client_settings(
                timeout if timeout is not None else _config.timeout,
                max_retries if max_retries is not None else _config.max_retries,
            )
        import shade

        self._user_agent = f"shade-python/{shade.__version__}"

    @property
    def max_retries(self) -> int:
        return self._max_retries if self._max_retries is not None else _config.max_retries

    @property
    def timeout(self) -> float:
        return self._timeout if self._timeout is not None else _config.timeout

    @property
    def api_base(self) -> Optional[str]:
        return self._api_base

    @property
    def base_url(self) -> str:
        if self._api_base:
            return self._api_base
        env = (
            _config.parse_environment(self.environment)
            if self.environment is not None
            else _config.environment
        )
        return _config.api_base or env.base_url.rstrip("/")

    def _headers(
        self,
        api_key: str,
        has_json_body: bool,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": self._user_agent,
        }
        if has_json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _merge_headers(
        self,
        base: Mapping[str, str],
        extra: Optional[Mapping[str, str]],
    ) -> Dict[str, str]:
        if not extra:
            return dict(base)
        merged = dict(base)
        for k, v in extra.items():
            k_fold = k.casefold()
            matching_keys = [existing_k for existing_k in merged if existing_k.casefold() == k_fold]
            for existing_k in matching_keys:
                del merged[existing_k]
            if v is not None:
                merged[k] = v
        return merged

    def _has_idempotency_key(self, headers: Mapping[str, str]) -> bool:
        for name in headers:
            if name.lower() == self._IDEMPOTENCY_KEY_HEADER.lower():
                return True
        return False

    def _prepare_request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> tuple[Any, str, str, Dict[str, str], bool]:
        cfg = get_config(
            api_key=self.api_key,
            environment=self.environment,
            api_base=self._api_base,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )

        method_upper = method.upper()
        url = _build_full_url(cfg.base_url, path)

        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.scheme.lower() == "http":
            hostname = (parsed_url.hostname or "").lower()
            is_local = hostname in ("localhost", "127.0.0.1", "::1")
            has_withheld_auth = False
            if headers:
                for k, v in headers.items():
                    if k.casefold() == "authorization" and v is None:
                        has_withheld_auth = True
                        break
            if not is_local:
                raise ValueError("HTTPS is required for non-local API bases")
            if not has_withheld_auth:
                raise ValueError(
                    "Cleartext HTTP API bases are not allowed when sending bearer credentials"
                )

        final_headers = self._merge_headers(
            self._headers(cfg.api_key, has_json_body=json is not None),
            headers,
        )
        safe_to_retry = (
            method_upper in self._IDEMPOTENT_METHODS
            or self._has_idempotency_key(final_headers)
        )
        return cfg, method_upper, url, final_headers, safe_to_retry


class _SyncHTTPClient(_BaseHTTPClient):
    """Internal synchronous HTTP client wrapping ``httpx.Client``.

    This is an implementation detail shared by sync resource methods through
    :class:`~shade.client.ShadeClient`. It centralises header construction,
    URL building, response parsing and retry logic so resources never need
    to import or reference ``httpx`` directly.

    Parameters
    ----------
    api_key : str, optional
        Bearer token. Resolved against the global config at request time
        when omitted.
    api_base : str, optional
        Override the API base URL. Resolved against the global config at
        request time when omitted.
    timeout : float, optional
        Per-request socket timeout in seconds. Resolved against the global
        config at request time when omitted.
    environment : str | Environment, optional
        Controls the default ``api_base`` and the Stellar network.
    max_retries : int, optional
        How many times to retry HTTP 429 and transient 5xx errors. Defaults
        to the global ``shade.max_retries``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        environment: Optional[Environment | str] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            environment=environment,
            max_retries=max_retries,
        )
        self._client = httpx.Client()

    def close(self) -> None:
        """Close the underlying ``httpx.Client``."""
        self._client.close()

    def __enter__(self) -> "_SyncHTTPClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request, retrying on 429/transient errors.

        Non-idempotent methods (``POST`` without an ``Idempotency-Key``
        header) are *not* automatically retried on 5xx or transport
        failures to avoid the risk of duplicate side-effects such as
        double-charging a payment. They are still retried on HTTP 429,
        since a rate-limit response proves the server declined to process
        the request and no side-effect occurred.

        Parameters
        ----------
        method : str
            HTTP verb (``"GET"``, ``"POST"``, …).
        path : str
            API path, e.g. ``"/payments"``. Combined with the resolved
            base URL.
        params : Mapping[str, Any], optional
            Query-string parameters encoded and appended to the URL.
        json : Any, optional
            JSON-serializable request body. When provided the
            ``Content-Type: application/json`` header is added.
        headers : Mapping[str, str], optional
            Per-request headers merged on top of the defaults. Pass an
            ``Idempotency-Key`` here to safely retry ``POST`` requests
            the server guarantees to be idempotent.

        Returns
        -------
        dict
            Decoded JSON response body.

        Raises
        ------
        ~shade.errors.AuthenticationError
            For HTTP 401/403.
        ~shade.errors.InvalidRequestError
            For HTTP 400/422.
        ~shade.errors.NotFoundError
            For HTTP 404.
        ~shade.errors.RateLimitError
            For HTTP 429 once retries are exhausted.
        ~shade.errors.NetworkError
            For HTTP 5xx once retries are exhausted, or unrecoverable
            transport failures.
        ~shade.errors.HTTPError
            For any other non-2xx status.
        ~shade.errors.ShadeError
            When a 2xx response body is not valid JSON.
        """
        cfg, method_upper, url, final_headers, safe_to_retry = self._prepare_request(
            method, path, params=params, json=json, headers=headers
        )

        attempt = 0
        while True:
            if _config.debug:
                log_request(
                    method_upper, url, final_headers, json if json is not None else params
                )

            try:
                response = self._client.request(
                    method_upper,
                    url,
                    headers=final_headers,
                    params=params,
                    json=json,
                    timeout=cfg.timeout,
                )
            except Exception as exc:
                if safe_to_retry and _is_retryable_error(exc):
                    if attempt >= cfg.max_retries:
                        raise NetworkError(
                            "Request failed after exhausting retries",
                            status_code=None,
                        ) from exc
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    import time

                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

            if _config.debug:
                log_response(response.status_code, response.headers, response.text)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers)
                if attempt < cfg.max_retries:
                    wait = (
                        retry_after
                        if retry_after is not None
                        else _retry_delay(attempt, _BASE_BACKOFF)
                    )
                    logger.debug(
                        "Retrying request after 429 (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        wait,
                    )
                    import time

                    time.sleep(wait)
                    attempt += 1
                    continue

            try:
                return _parse_response(response)
            except Exception as exc:
                retryable = _is_retryable_error(exc)
                if not retryable and isinstance(exc, ShadeError):
                    retryable = _is_retryable_status(exc.status_code or 0)
                if (
                    safe_to_retry
                    and attempt < cfg.max_retries
                    and retryable
                ):
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after retryable status (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    import time

                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

    def get(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return self.request("GET", path, params=params, headers=headers)

    def post(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return self.request("POST", path, params=params, json=json, headers=headers)

    def patch(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return self.request("PATCH", path, params=params, json=json, headers=headers)

    def delete(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return self.request("DELETE", path, params=params, json=json, headers=headers)


class _AsyncHTTPClient(_BaseHTTPClient):
    """Internal asynchronous HTTP client wrapping ``httpx.AsyncClient``.

    This is the async counterpart to :class:`_SyncHTTPClient`. It centralises header
    construction, URL building, response parsing and retry logic using ``asyncio.sleep``
    for non-blocking retries.

    Parameters
    ----------
    api_key : str, optional
        Bearer token. Resolved against the global config at request time
        when omitted.
    api_base : str, optional
        Override the API base URL. Resolved against the global config at
        request time when omitted.
    timeout : float, optional
        Per-request socket timeout in seconds. Resolved against the global
        config at request time when omitted.
    environment : str | Environment, optional
        Controls the default ``api_base`` and the Stellar network.
    max_retries : int, optional
        How many times to retry HTTP 429 and transient 5xx errors. Defaults
        to the global ``shade.max_retries``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        environment: Optional[Environment | str] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            environment=environment,
            max_retries=max_retries,
        )
        self._client = httpx.AsyncClient()

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient``."""
        await self._client.aclose()

    async def __aenter__(self) -> "_AsyncHTTPClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """Execute an HTTP request asynchronously, retrying on 429/transient errors.

        See :meth:`_SyncHTTPClient.request` for detail on idempotency and error handling.
        """
        cfg, method_upper, url, final_headers, safe_to_retry = self._prepare_request(
            method, path, params=params, json=json, headers=headers
        )

        attempt = 0
        while True:
            if _config.debug:
                log_request(
                    method_upper, url, final_headers, json if json is not None else params
                )

            try:
                response = await self._client.request(
                    method_upper,
                    url,
                    headers=final_headers,
                    params=params,
                    json=json,
                    timeout=cfg.timeout,
                )
            except Exception as exc:
                if safe_to_retry and _is_retryable_error(exc):
                    if attempt >= cfg.max_retries:
                        raise NetworkError(
                            "Request failed after exhausting retries",
                            status_code=None,
                        ) from exc
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after transient failure (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise

            if _config.debug:
                log_response(response.status_code, response.headers, response.text)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers)
                if attempt < cfg.max_retries:
                    wait = (
                        retry_after
                        if retry_after is not None
                        else _retry_delay(attempt, _BASE_BACKOFF)
                    )
                    logger.debug(
                        "Retrying request after 429 (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue

            try:
                return _parse_response(response)
            except Exception as exc:
                retryable = _is_retryable_error(exc)
                if not retryable and isinstance(exc, ShadeError):
                    retryable = _is_retryable_status(exc.status_code or 0)
                if (
                    safe_to_retry
                    and attempt < cfg.max_retries
                    and retryable
                ):
                    delay = _retry_delay(attempt, _BASE_BACKOFF)
                    logger.debug(
                        "Retrying request after retryable status (attempt %s/%s) in %.3fs",
                        attempt + 1,
                        cfg.max_retries + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise

    async def get(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return await self.request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return await self.request("POST", path, params=params, json=json, headers=headers)

    async def patch(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return await self.request("PATCH", path, params=params, json=json, headers=headers)

    async def delete(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        return await self.request("DELETE", path, params=params, json=json, headers=headers)

