from datetime import datetime
from typing import ClassVar, Optional

import pytest
from pydantic import Field

from shade import InvalidRequestError, ShadeObject


class Payment(ShadeObject):
    id: str
    amount: int
    currency: str = "USDC"
    created_at: Optional[datetime] = None


class Invoice(ShadeObject):
    _id_field: ClassVar[str] = "invoice_id"

    invoice_id: str = Field(alias="invoiceId")
    total: int


class Account(ShadeObject):
    name: str


def test_from_dict_builds_model():
    payment = Payment.from_dict({"id": "pay_1", "amount": 500})

    assert payment.id == "pay_1"
    assert payment.amount == 500
    assert payment.currency == "USDC"


def test_round_trip_preserves_data():
    data = {
        "id": "pay_1",
        "amount": 500,
        "currency": "XLM",
        "created_at": datetime(2026, 7, 22, 12, 30),
    }
    payment = Payment.from_dict(data)

    assert Payment.from_dict(payment.to_dict()).to_dict() == payment.to_dict()
    assert payment.to_dict() == data


def test_round_trip_preserves_aliased_fields():
    invoice = Invoice.from_dict({"invoiceId": "inv_1", "total": 900})

    dumped = invoice.to_dict()
    assert dumped == {"invoiceId": "inv_1", "total": 900}
    assert Invoice.from_dict(dumped).invoice_id == "inv_1"


def test_aliased_fields_also_accept_the_field_name():
    invoice = Invoice.from_dict({"invoice_id": "inv_2", "total": 10})

    assert invoice.invoice_id == "inv_2"


def test_unknown_fields_are_accepted_and_preserved():
    payment = Payment.from_dict(
        {"id": "pay_1", "amount": 500, "settlement_network": "stellar"}
    )

    assert payment.settlement_network == "stellar"
    assert payment.to_dict()["settlement_network"] == "stellar"


def test_type_coercion():
    payment = Payment.from_dict({"id": "pay_1", "amount": "500"})

    assert payment.amount == 500


def test_repr_shows_class_name_and_id():
    payment = Payment.from_dict({"id": "pay_1", "amount": 500})

    assert repr(payment) == "<Payment id='pay_1'>"


def test_repr_uses_overridden_id_field():
    invoice = Invoice.from_dict({"invoiceId": "inv_1", "total": 900})

    assert repr(invoice) == "<Invoice invoice_id='inv_1'>"


def test_repr_without_an_id_field():
    assert repr(Account.from_dict({"name": "acme"})) == "<Account>"


def test_validation_error_surfaces_as_invalid_request_error():
    with pytest.raises(InvalidRequestError) as excinfo:
        Payment.from_dict({"id": "pay_1"})

    error = excinfo.value
    assert "1 validation error for Payment" in str(error)
    assert error.param == "amount"
    assert "amount" in error.field_errors


def test_validation_error_on_direct_construction():
    with pytest.raises(InvalidRequestError):
        Payment(id="pay_1", amount="not-a-number")


def test_from_dict_rejects_non_dict_input():
    with pytest.raises(InvalidRequestError) as excinfo:
        Payment.from_dict(["id", "pay_1"])

    assert "expects a dict" in str(excinfo.value)
