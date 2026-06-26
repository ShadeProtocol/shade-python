from __future__ import annotations

from typing import Any, Dict, Optional

from . import config as _config
from .config import Environment, get_environment_config, parse_environment
from .http import AsyncHTTPClient, SyncHTTPClient, DEFAULT_MAX_RETRIES


class Gateway:
    """
    Main entry point for the Shade Payment Gateway.

    Parameters
    ----------
    api_key : str
        Your Shade API key.
    environment : Environment
        Controls the Stellar network passphrase and the default API URL.
        Defaults to ``Environment.MAINNET``.
    api_base : str, optional
        Override the API host for this client (useful for local dev or staging).
        Takes precedence over the module-level ``shade.api_base`` and the
        URL derived from ``environment``. Trailing slashes are trimmed.
        Intended for development and testing only.
    base_url : str
        Deprecated. Prefer ``api_base``.
    max_retries : int
        Number of automatic retries on HTTP 429.  Defaults to
        ``DEFAULT_MAX_RETRIES`` (3).  Set to ``0`` to disable.
    timeout : float
        Per-request socket timeout in seconds.
    """
    @property
    def environment(self) -> Environment:
        return self._environment

    @environment.setter
    def environment(self, value: Environment | str) -> None:
        self._environment = parse_environment(value)
        self._environment_config = get_environment_config(self._environment)

    @property
    def horizon_url(self) -> str:
        return self._environment_config.horizon_url

    @property
    def network_passphrase(self) -> str:
        return self._environment_config.network_passphrase

    @property
    def api_base_url(self) -> str:
        return self._environment_config.api_base_url

    def __init__(
        self,
        api_key: str = "",
        environment: Environment = Environment.MAINNET,
        api_base: Optional[str] = None,
        base_url: str = "",
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self.api_key = api_key
        self.environment = environment

        # Resolution order: explicit api_base > module-level shade.api_base
        # > legacy base_url > environment URL
        resolved = api_base or _config.api_base or base_url or environment.base_url
        self._base_url = resolved.rstrip("/")

        self._http = SyncHTTPClient(
            base_url=self._base_url,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )
        self._async_http = AsyncHTTPClient(
            base_url=self._base_url,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Sync API
    # ------------------------------------------------------------------

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

    async def process_payment_async(
        self, amount: float, currency: str
    ) -> Dict[str, Any]:
        """Async variant of :meth:`process_payment`."""
        return await self._async_http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )
