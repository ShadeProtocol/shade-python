"""
Transfer model.

Represents a payout of funds from the merchant wallet to a destination
address on the Stellar network. Field names are converted from ``camelCase``
(backend/JSON) to ``snake_case`` (Python) via pydantic field aliases, matching
the convention established by :class:`~shade.models.merchant.Merchant`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import Field, field_validator
from stellar_sdk.strkey import StrKey

from .base import ShadeObject


class TransferStatus(str, Enum):
    """Lifecycle status of a transfer."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Transfer(ShadeObject):
    """A payout of funds from the merchant wallet to a destination address.

    Build one from an API response with :meth:`ShadeObject.from_dict`, which
    maps camelCase JSON keys to the snake_case fields below. ``asset`` falls
    back to ``"XLM"`` when the API omits it (or sends it as ``null``), and
    ``status`` is always coerced to a :class:`TransferStatus` member.
    """

    id: str
    source_address: str = Field(alias="sourceAddress")
    destination_address: str = Field(alias="destinationAddress")
    amount: Decimal
    asset: str = "XLM"
    status: TransferStatus
    stellar_tx_hash: Optional[str] = Field(default=None, alias="stellarTxHash")
    fee: Optional[Decimal] = None
    created_at: datetime = Field(alias="createdAt")

    @field_validator("asset", mode="before")
    @classmethod
    def _default_asset(cls, value: object) -> object:
        # Covers both a missing key (pydantic would already default it) and an
        # API response that sends the key as an explicit null/empty string.
        # Other falsy-but-wrong types (e.g. False, 0) are left alone so
        # pydantic's normal type validation rejects them.
        if value is None or value == "":
            return "XLM"
        return value

    @field_validator("source_address", "destination_address")
    @classmethod
    def _validate_stellar_address(cls, value: str) -> str:
        if not StrKey.is_valid_ed25519_public_key(value):
            raise ValueError(
                "must be a valid Stellar public key (starts with 'G', 56 characters)"
            )
        return value

    @field_validator("amount")
    @classmethod
    def _amount_must_be_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("amount must be greater than 0")
        return value

    @field_validator("fee")
    @classmethod
    def _fee_must_not_be_negative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ValueError("fee must not be negative")
        return value
