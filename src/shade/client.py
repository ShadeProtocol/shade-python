"""
Per-instance SDK configuration.

``ShadeClient`` binds a set of credentials and connection settings to a single
object, so an application acting on behalf of several merchants can hold one
client per tenant instead of mutating the global ``shade`` module config.
Anything left unset falls back to the global config, resolved per request so a
client on the defaults follows later changes to ``shade.api_key`` and friends.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

import httpx

from .config import Environment, validate_client_settings
from .config import config as _config
from .http import HTTPXTransport
from .http_client import _AsyncHTTPClient, _SyncHTTPClient

API_KEY_ENV_VAR = "SHADE_API_KEY"
ENVIRONMENT_ENV_VAR = "SHADE_ENVIRONMENT"


class ShadeClient:
    """An isolated Shade API client carrying its own credentials and settings.

    Two clients built with different API keys never share state, so a
    multi-tenant application can keep one per merchant::

        acme = ShadeClient(api_key="sk_live_acme")
        globex = ShadeClient(api_key="sk_live_globex")

    Every parameter falls back to the matching global setting
    (``shade.api_key``, ``shade.environment``, …) when omitted. Explicit
    arguments are pinned to the instance; omitted ones track the global config,
    which is read at request time rather than captured at construction.

    Parameters
    ----------
    api_key : str, optional
        Your Shade API key. Defaults to the module-level ``shade.api_key``.
    environment : str | Environment, optional
        Controls the Stellar network passphrase and the default API URL.
        Defaults to the module-level ``shade.environment``.
    api_base : str, optional
        Override the API host for this client (local dev, staging, or a
        self-hosted backend). Takes precedence over the module-level
        ``shade.api_base`` and the URL derived from ``environment``. Trailing
        slashes are trimmed.
    base_url : str
        Deprecated. Prefer ``api_base``.
    max_retries : int, optional
        Automatic retries on HTTP 429 and transient failures. Defaults to
        ``shade.max_retries``. Set to ``0`` to disable auto-retry.
    timeout : float, optional
        Per-request socket timeout in seconds. Defaults to ``shade.timeout``.
    debug : bool
        Log requests and responses for this client. The global
        ``shade.config.debug`` enables logging regardless of this flag.
    http_client : httpx.Client, optional
        Reuse an existing httpx client instead of creating one. The caller
        keeps ownership: :meth:`close` will not close a client it was given.

    Raises
    ------
    ValueError
        If ``timeout`` or ``max_retries`` is out of range, or ``environment``
        is not a recognised value.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        environment: Optional[Environment | str] = None,
        api_base: Optional[str] = None,
        base_url: str = "",
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        debug: bool = False,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key
        self._environment = (
            _config.parse_environment(environment) if environment is not None else None
        )
        api_base = api_base or (base_url if base_url else None)
        self._api_base = api_base.rstrip("/") if api_base else None
        self._timeout = timeout
        self._max_retries = max_retries
        self.debug = debug

        if timeout is not None or max_retries is not None:
            validate_client_settings(
                timeout if timeout is not None else _config.timeout,
                max_retries if max_retries is not None else _config.max_retries,
            )

        self._http = _SyncHTTPClient(
            api_base=self._api_base,
            api_key=self._api_key,
            environment=self._environment,
            max_retries=self._max_retries,
            timeout=self._timeout,
        )
        self._async_http_instance: Optional[_AsyncHTTPClient] = None
        self._client = HTTPXTransport(
            api_key=self._api_key,
            base_url=self._api_base,
            environment=self._environment,
            timeout=self._timeout,
            debug=debug,
            http_client=http_client,
        )

    @property
    def _async_http(self) -> _AsyncHTTPClient:
        if self._async_http_instance is None:
            self._async_http_instance = _AsyncHTTPClient(
                api_base=self._api_base,
                api_key=self._api_key,
                environment=self._environment,
                max_retries=self._max_retries,
                timeout=self._timeout,
            )
        return self._async_http_instance

    @classmethod
    def from_env(cls, **overrides: Any) -> "ShadeClient":
        """Build a client from ``SHADE_API_KEY`` and ``SHADE_ENVIRONMENT``.

        Either variable may be absent, in which case the usual global-config
        fallback applies — so a missing ``SHADE_API_KEY`` with no
        ``shade.api_key`` set leaves the client without credentials, and its
        requests raise :class:`~shade.errors.AuthenticationError`.

        Any keyword argument overrides the corresponding environment variable,
        letting callers take the key from the environment while setting the rest
        explicitly::

            client = ShadeClient.from_env(timeout=5.0)
        """
        env_kwargs: Dict[str, Any] = {}
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if api_key:
            env_kwargs["api_key"] = api_key
        environment = os.environ.get(ENVIRONMENT_ENV_VAR)
        if environment:
            env_kwargs["environment"] = environment
        env_kwargs.update(overrides)
        return cls(**env_kwargs)

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key if self._api_key is not None else _config.api_key

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        self._api_key = value
        self._http.api_key = value
        if self._async_http_instance is not None:
            self._async_http_instance.api_key = value
        self._client.api_key = value

    @property
    def environment(self) -> Environment:
        if self._environment is not None:
            return self._environment
        return _config.environment

    @environment.setter
    def environment(self, value: str | Environment) -> None:
        parsed = _config.parse_environment(value)
        self._environment = parsed
        self._http.environment = parsed
        if self._async_http_instance is not None:
            self._async_http_instance.environment = parsed
        self._client.environment = parsed

    @property
    def timeout(self) -> float:
        return self._timeout if self._timeout is not None else _config.timeout

    @property
    def max_retries(self) -> int:
        return self._max_retries if self._max_retries is not None else _config.max_retries

    @property
    def _base_url(self) -> str:
        if self._api_base:
            return self._api_base
        if _config.api_base:
            return _config.api_base.rstrip("/")
        return self.environment.base_url.rstrip("/")

    @property
    def api_base(self) -> str:
        """The API base URL this client currently sends requests to."""
        return self._base_url

    def close(self) -> None:
        self._http.close()
        self._client.close()

    async def aclose(self) -> None:
        self.close()
        if self._async_http_instance is not None:
            await self._async_http_instance.aclose()
            self._async_http_instance = None

    def __enter__(self) -> "ShadeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "ShadeClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Any = None,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        """Send a request and return the raw ``httpx.Response``."""
        return self._client.request(
            method,
            path,
            headers=headers,
            json=json,
            content=content,
        )

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} api_key={_mask_api_key(self.api_key)!r} "
            f"environment={self.environment.value!r} api_base={self._base_url!r}>"
        )


def _mask_api_key(api_key: Optional[str]) -> str:
    """Show only the last four characters of a key, for use in reprs."""
    if not api_key:
        return "unset"
    if len(api_key) <= 4:
        return "****"
    return "*" * (len(api_key) - 4) + api_key[-4:]


_default_client: Optional[ShadeClient] = None
_default_client_lock = threading.Lock()


def default_client() -> ShadeClient:
    """Return the shared client backed by the global ``shade`` config.

    Resources fall back to this when constructed without an explicit
    ``client=``. It pins no settings of its own, so every global change —
    including a ``shade.api_key`` assigned after the first call — is picked up
    on the next request.
    """
    global _default_client

    with _default_client_lock:
        if _default_client is None:
            _default_client = ShadeClient()
        return _default_client


def reset_default_client() -> None:
    """Drop the cached global client. Primarily useful in tests."""
    global _default_client

    with _default_client_lock:
        client, _default_client = _default_client, None
    if client is not None:
        client.close()
