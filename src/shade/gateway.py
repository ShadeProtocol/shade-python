from __future__ import annotations

from typing import Any, Dict

from .client import ShadeClient


class Gateway(ShadeClient):
    """
    Main entry point for the Shade Payment Gateway.

    A :class:`~shade.client.ShadeClient` with the payment operations attached.
    See :class:`~shade.client.ShadeClient` for the constructor parameters and
    how each one falls back to the global ``shade`` config.
    """

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

    async def process_payment_async(
        self, amount: float, currency: str
    ) -> Dict[str, Any]:
        """Async variant of :meth:`process_payment`."""
        return await self._async_http.request(
            "POST",
            "/payments",
            {"amount": amount, "currency": currency},
        )
