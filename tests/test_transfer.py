from datetime import datetime
from decimal import Decimal

import pytest
from stellar_sdk import Keypair

import shade
from shade import InvalidRequestError, ShadeObject, Transfer, TransferStatus

SOURCE_ADDRESS = Keypair.random().public_key
DESTINATION_ADDRESS = Keypair.random().public_key


def _api_response(**overrides):
    """A representative camelCase backend payload."""
    data = {
        "id": "trf_123",
        "sourceAddress": SOURCE_ADDRESS,
        "destinationAddress": DESTINATION_ADDRESS,
        "amount": "150.25",
        "asset": "USDC",
        "status": "pending",
        "stellarTxHash": None,
        "fee": "0.5",
        "createdAt": "2026-07-20T12:00:00Z",
    }
    data.update(overrides)
    return data


def test_from_dict_maps_camelcase_to_snake_case():
    transfer = Transfer.from_dict(_api_response())

    assert transfer.id == "trf_123"
    assert transfer.source_address == SOURCE_ADDRESS
    assert transfer.destination_address == DESTINATION_ADDRESS
    assert transfer.amount == Decimal("150.25")
    assert transfer.asset == "USDC"
    assert transfer.status == TransferStatus.PENDING
    assert transfer.stellar_tx_hash is None
    assert transfer.fee == Decimal("0.5")
    assert transfer.created_at == datetime.fromisoformat("2026-07-20T12:00:00+00:00")


def test_amount_and_fee_are_decimal():
    transfer = Transfer.from_dict(_api_response(amount="99.99", fee="1.10"))
    assert isinstance(transfer.amount, Decimal)
    assert isinstance(transfer.fee, Decimal)


def test_asset_defaults_to_xlm_when_absent():
    payload = _api_response()
    del payload["asset"]
    transfer = Transfer.from_dict(payload)
    assert transfer.asset == "XLM"


def test_asset_defaults_to_xlm_when_null():
    transfer = Transfer.from_dict(_api_response(asset=None))
    assert transfer.asset == "XLM"


def test_malformed_asset_is_rejected_not_defaulted():
    # False/0 are falsy but must not be silently coerced to "XLM" — they are
    # the wrong type and should surface as a validation error instead.
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(asset=False))
    assert exc_info.value.param == "asset"


def test_status_is_transfer_status_enum():
    for raw in ("pending", "processing", "completed", "failed"):
        transfer = Transfer.from_dict(_api_response(status=raw))
        assert isinstance(transfer.status, TransferStatus)
        assert transfer.status.value == raw


def test_invalid_status_raises():
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(status="bogus"))
    assert exc_info.value.param == "status"


def test_completed_transfer_has_stellar_tx_hash():
    transfer = Transfer.from_dict(
        _api_response(status="completed", stellarTxHash="a" * 64)
    )
    assert transfer.status is TransferStatus.COMPLETED
    assert transfer.stellar_tx_hash == "a" * 64


def test_fee_defaults_to_none():
    payload = _api_response()
    del payload["fee"]
    transfer = Transfer.from_dict(payload)
    assert transfer.fee is None


def test_transfer_is_exported_from_package():
    assert shade.Transfer is Transfer
    assert shade.TransferStatus is TransferStatus
    assert issubclass(Transfer, ShadeObject)


def test_from_dict_requires_a_mapping():
    with pytest.raises(InvalidRequestError):
        Transfer.from_dict([("id", "x")])  # type: ignore[arg-type]


def test_missing_id_raises_clear_validation_error():
    payload = _api_response()
    del payload["id"]
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(payload)
    assert exc_info.value.param == "id"


def test_invalid_source_address_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(sourceAddress="not-a-stellar-key"))
    assert exc_info.value.param == "sourceAddress"


def test_invalid_destination_address_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(destinationAddress="not-a-stellar-key"))
    assert exc_info.value.param == "destinationAddress"


def test_non_positive_amount_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(amount="0"))
    assert exc_info.value.param == "amount"


def test_negative_fee_is_rejected():
    with pytest.raises(InvalidRequestError) as exc_info:
        Transfer.from_dict(_api_response(fee="-1"))
    assert exc_info.value.param == "fee"


def test_from_dict_preserves_unknown_keys():
    transfer = Transfer.from_dict(_api_response(description="Payout for order #9"))
    assert transfer.to_dict()["description"] == "Payout for order #9"


def test_to_dict_round_trips_to_camelcase():
    payload = _api_response()
    transfer = Transfer.from_dict(payload)
    round_tripped = transfer.to_dict()
    assert round_tripped["sourceAddress"] == SOURCE_ADDRESS
    assert round_tripped["destinationAddress"] == DESTINATION_ADDRESS
    assert round_tripped["stellarTxHash"] is None
    assert Transfer.from_dict(round_tripped) == transfer
