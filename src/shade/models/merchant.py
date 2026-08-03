"""
Merchant model.

Mirrors the Shade backend's Prisma ``Merchant`` schema, with field names
converted from ``camelCase`` (Prisma/JSON) to ``snake_case`` (Python) via
pydantic field aliases.  The :attr:`Merchant.merchant_id` field (from Prisma
``merchantId: Int``) is the numeric identifier the Soroban contract stamps onto
every invoice, making it the bridge between the backend and the on-chain world.
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field, StrictBool, field_validator
from stellar_sdk.strkey import StrKey

from .base import ShadeObject


class Merchant(ShadeObject):
    """A Shade merchant account.

    Build one from an API response with :meth:`ShadeObject.from_dict`, which maps
    camelCase JSON keys to the snake_case fields below.  ``address`` must be a
    valid Stellar ed25519 public key and ``active`` / ``verified`` must be real
    booleans; anything else raises
    :class:`~shade.errors.InvalidRequestError` on construction.
    """

    id: str
    merchant_id: int = Field(alias="merchantId")
    address: str
    active: StrictBool
    verified: StrictBool
    account: Optional[str] = None
    email: Optional[str] = None
    first_name: Optional[str] = Field(default=None, alias="firstName")
    last_name: Optional[str] = Field(default=None, alias="lastName")
    business_name: Optional[str] = Field(default=None, alias="businessName")
    category: Optional[str] = None
    description: Optional[str] = None
    logo: Optional[str] = None
    webhook: Optional[str] = None

    @field_validator("merchant_id", mode="before")
    @classmethod
    def _reject_bool_merchant_id(cls, value: object) -> object:
        # pydantic would otherwise coerce ``True``/``False`` to 1/0; a boolean is
        # never a valid merchant id, so reject it rather than silently accept it.
        if isinstance(value, bool):
            raise ValueError("merchant_id must be an integer, not a boolean")
        return value

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        if not StrKey.is_valid_ed25519_public_key(value):
            raise ValueError(
                "address must be a valid Stellar public key "
                "(starts with 'G', 56 characters)"
            )
        return value

    @property
    def display_name(self) -> Optional[str]:
        """The most informative human-readable name available.

        Prefers ``business_name``; falls back to the person's full name
        (``"{first_name} {last_name}"``); finally ``email``.  Each candidate is
        trimmed, so a blank or whitespace-only value falls through to the next
        one rather than being returned.  ``None`` when nothing is available.
        """
        business_name = (self.business_name or "").strip()
        if business_name:
            return business_name
        first_name = (self.first_name or "").strip()
        last_name = (self.last_name or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part)
        if full_name:
            return full_name
        return (self.email or "").strip() or None
