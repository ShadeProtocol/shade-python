"""
Balance models.

Represents the per-asset balance breakdown returned by
:meth:`Merchant.get_balance`. A merchant wallet may hold multiple Stellar
assets (native XLM, USDC, etc.), each described by an
:class:`AssetBalance` entry.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from pydantic import Field

from .base import ShadeObject


class AssetBalance(ShadeObject):
    """Balance for a single Stellar asset.

    ``asset_code`` is ``"XLM"`` for the native balance.  For issued assets
    ``asset_issuer`` contains the issuing account's public key; it is
    ``None`` for the native asset.
    """

    _id_field = "asset_code"

    asset_code: str = Field(alias="assetCode")
    asset_issuer: Optional[str] = Field(default=None, alias="assetIssuer")
    balance: Decimal


class Balance(ShadeObject):
    """Collection of per-asset balances for a merchant wallet.

    The ``balances`` list is populated from the ``balances`` key in the API
    response.  Use :meth:`get` to look up a specific asset by code.
    """

    _id_field = "id"

    id: Optional[str] = None
    merchant_id: Optional[str] = Field(default=None, alias="merchantId")
    balances: List[AssetBalance] = []

    def get(self, asset_code: str) -> Optional[AssetBalance]:
        """Return the :class:`AssetBalance` for *asset_code*, or ``None``.

        Examples::

            balance.get("XLM")     # native balance
            balance.get("USDC")    # USDC balance if held
            balance.get("NOTEXIST")  # None — no KeyError
        """
        code = asset_code.upper()
        for entry in self.balances:
            if entry.asset_code.upper() == code:
                return entry
        return None
