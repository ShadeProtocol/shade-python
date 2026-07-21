"""Tests for the Payment model."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shade.models import Payment, PaymentStatus


def _payment_payload(**overrides):
    data = {
        "id": "pay_123",
        "status": "pending",
        "amount": "100.50",
        "currency": "XLM",
        "description": "Order #42",
        "merchant_id": "merch_abc",
        "stellar_tx_hash": None,
        "payment_address": "GABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
        "created_at": "2024-01-15T12:00:00Z",
        "updated_at": "2024-01-15T12:05:00Z",
    }
    data.update(overrides)
    return data


def test_payment_from_dict_constructs_without_error():
    payment = Payment.from_dict(_payment_payload())

    assert payment.id == "pay_123"
    assert payment.currency == "XLM"
    assert payment.merchant_id == "merch_abc"
    assert payment.payment_address == "GABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    assert payment.description == "Order #42"
    assert payment.stellar_tx_hash is None
    assert isinstance(payment.created_at, datetime)
    assert isinstance(payment.updated_at, datetime)


def test_payment_amount_is_decimal_not_float():
    payment = Payment.from_dict(_payment_payload(amount="100.50"))

    assert isinstance(payment.amount, Decimal)
    assert not isinstance(payment.amount, float)
    assert payment.amount == Decimal("100.50")


def test_payment_status_is_enum():
    payment = Payment.from_dict(_payment_payload(status="pending"))

    assert isinstance(payment.status, PaymentStatus)
    assert payment.status is PaymentStatus.PENDING
    assert payment.status == "pending"


@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.PENDING,
        PaymentStatus.COMPLETED,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.PARTIALLY_PAID,
    ],
)
def test_payment_accepts_all_status_values(status):
    payment = Payment.from_dict(_payment_payload(status=status.value))
    assert payment.status is status


def test_invalid_status_raises_clear_validation_error():
    with pytest.raises(ValidationError) as exc_info:
        Payment.from_dict(_payment_payload(status="not_a_real_status"))

    errors = exc_info.value.errors()
    assert any(err["loc"] == ("status",) for err in errors)


@pytest.mark.parametrize("amount", [0, -5, "0", "-1.00", Decimal("0")])
def test_amount_must_be_positive(amount):
    with pytest.raises(ValidationError) as exc_info:
        Payment.from_dict(_payment_payload(amount=amount))

    assert "amount" in str(exc_info.value).lower()


def test_optional_fields_default_to_none():
    payload = _payment_payload()
    del payload["description"]
    del payload["stellar_tx_hash"]

    payment = Payment.from_dict(payload)

    assert payment.description is None
    assert payment.stellar_tx_hash is None


def test_extra_api_fields_are_allowed():
    payment = Payment.from_dict(_payment_payload(metadata={"order_id": "42"}))

    assert payment.id == "pay_123"
    assert payment.metadata == {"order_id": "42"}


def test_payment_round_trip_preserves_decimal():
    payment = Payment.from_dict(_payment_payload(amount="0.0000001"))
    restored = Payment.from_dict(payment.to_dict())

    assert restored.amount == Decimal("0.0000001")
    assert restored.status is PaymentStatus.PENDING
    assert restored.id == payment.id
