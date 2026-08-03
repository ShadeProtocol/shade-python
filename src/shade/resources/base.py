"""
Base class shared by every Shade API resource.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..client import ShadeClient, default_client


class BaseResource:
    """Base class for API resources.

    Every resource takes an optional ``client=``. When one is given the resource
    uses that client's credentials and settings; when it is omitted the resource
    falls back to the shared client built from the global ``shade`` config::

        shade.api_key = "sk_live_default"
        Payments().retrieve("pay_1")                  # global credentials
        Payments(client=acme_client).retrieve("pay_1")  # acme's credentials

    The client is resolved on each access rather than captured at construction,
    so a resource built before ``shade.api_key`` was assigned still picks it up.
    """

    def __init__(self, client: Optional[ShadeClient] = None) -> None:
        self._explicit_client = client

    @property
    def client(self) -> ShadeClient:
        """The client backing this resource."""
        if self._explicit_client is not None:
            return self._explicit_client
        return default_client()

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a request through this resource's client and return the body."""
        return self.client._http.request(method, path, payload)

    async def _request_async(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Async counterpart of :meth:`_request`."""
        return await self.client._async_http.request(method, path, payload)

    def __repr__(self) -> str:
        if self._explicit_client is None:
            return f"<{type(self).__name__} client=global>"
        return f"<{type(self).__name__} client={self._explicit_client!r}>"
