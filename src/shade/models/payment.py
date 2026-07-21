"""Payment resource model."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import field_validator

from .base import ShadeObject


class PaymentStatus(str, Enum):
    """Lifecycle status of a Shade payment."""

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PARTIALLY_PAID = "partially_paid"


class Payment(ShadeObject):
    """Represents a Shade payment resource returned by the API."""

    id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    description: Optional[str] = None
    merchant_id: str
    stellar_tx_hash: Optional[str] = None
    payment_address: str
    created_at: datetime
    updated_at: datetime

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("amount must be greater than 0")
        return value
