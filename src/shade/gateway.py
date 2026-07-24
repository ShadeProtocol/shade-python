from __future__ import annotations

import httpx
from typing import Any, Dict, Optional

from . import config as _config
from .client import ShadeClient as ClientShadeClient
from .config import Environment, validate_client_settings
from .http import AsyncHTTPClient, SyncHTTPClient, DEFAULT_MAX_RETRIES


class Gateway:
    """
    Main entry point for the Shade Payment Gateway.

    Parameters
    ----------
    api_key : str
        Your Shade API key.
    environment : str | Environment, optional
        Controls the Stellar network passphrase and the default API URL.
        Defaults to the module-level ``shade.environment`` (``Environment.SANDBOX``).
    api_base : str, optional
        Override the API host for this client (useful for local dev or staging).
        Takes precedence over the module-level ``shade.api_base`` and the
        URL derived from ``environment``. Trailing slashes are trimmed.
        Intended for development and testing only.
    base_url : str
        Deprecated. Prefer ``api_base``.
    max_retries : int, optional
        Number of automatic retries on HTTP 429 and transient failures.
        Defaults to the module-level ``shade.max_retries`` (3).  Set to ``0``
        to disable auto-retry.
    timeout : float, optional
        Per-request socket timeout in seconds.  Defaults to the module-level
        ``shade.timeout`` (30.0).
    """

    def __init__(
        self,
        api_key: str = "",
        environment: Optional[Environment | str] = None,
        api_base: Optional[str] = None,
        base_url: str = "",
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
        debug: bool = False,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self.api_key = api_key
        
        if environment is not None:
            self.environment = _config.parse_environment(environment)
        else:
            self.environment = _config.environment

        resolved_max_retries = (
            _config.max_retries if max_retries is None else max_retries
        )
        resolved_timeout = _config.timeout if timeout is None else timeout
        validate_client_settings(resolved_timeout, resolved_max_retries)

        # Resolution order: explicit api_base > module-level shade.api_base
        # > legacy base_url > environment URL
        resolved = api_base or _config.api_base or base_url or self.environment.base_url
        self._base_url = resolved.rstrip("/")
        self._http = SyncHTTPClient(
            base_url=self._base_url,
            api_key=api_key,
            max_retries=resolved_max_retries,
            timeout=resolved_timeout,
        )
        self._async_http = AsyncHTTPClient(
            base_url=self._base_url,
            api_key=api_key,
            max_retries=resolved_max_retries,
            timeout=resolved_timeout,
        )

        self._client = ClientShadeClient(
            api_key=api_key,
            base_url=self._base_url,
            debug=debug,
            http_client=http_client,
        )

    # ------------------------------------------------------------------
    # Sync API
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Gateway":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        json: Any = None,
        content: Optional[bytes] = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            path,
            headers=headers,
            json=json,
            content=content,
        )

    def process_payment(self, amount: float, currency: str) -> Dict[str, Any]:
        """
        Process a payment (sync).

        Parameters
        ----------
        amount : float
            Payment amount.
        currency : str
            ISO 4217 currency code (e.g. ``"USD"``).

        Returns
        -------
        dict
            API response body.
        """
        return self._http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Explicitly close the underlying async HTTP client."""
        await self._async_http.aclose()

    async def process_payment_async(
        self, amount: float, currency: str
    ) -> Dict[str, Any]:
        """Async variant of :meth:`process_payment`."""
        return await self._async_http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )
