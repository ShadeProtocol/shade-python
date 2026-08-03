from datetime import datetime, timezone

import pytest

import shade
from shade import InvalidRequestError, ShadeObject, WebhookEvent, WebhookEventType

PAYMENT_DATA = {
    "id": "pay_123",
    "amount": "150.25",
    "asset": "USDC",
    "status": "completed",
}


def _api_response(**overrides):
    """A representative camelCase backend payload."""
    data = {
        "id": "evt_123",
        "type": "payment.completed",
        "data": dict(PAYMENT_DATA),
        "createdAt": "2026-07-20T12:00:00Z",
        "livemode": True,
    }
    data.update(overrides)
    return data


def test_from_dict_populates_all_fields():
    event = WebhookEvent.from_dict(_api_response())

    assert event.id == "evt_123"
    assert event.type == "payment.completed"
    assert event.data == PAYMENT_DATA
    assert event.created_at == datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    assert event.livemode is True


def test_data_stays_a_raw_dict():
    event = WebhookEvent.from_dict(_api_response())

    assert isinstance(event.data, dict)
    assert not isinstance(event.data, ShadeObject)
    assert event.data["status"] == "completed"


def test_non_dict_data_raises():
    with pytest.raises(InvalidRequestError):
        WebhookEvent.from_dict(_api_response(data=["a", "b"]))


def test_payload_is_not_mutated():
    payload = _api_response()
    snapshot = {
        **payload,
        "data": dict(payload["data"]),
    }

    WebhookEvent.from_dict(payload)

    assert payload == snapshot


def test_created_at_parsed_from_iso_string():
    event = WebhookEvent.from_dict(_api_response(createdAt="2026-01-02T03:04:05Z"))
    assert event.created_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_created_at_accepts_snake_case_key():
    payload = _api_response()
    del payload["createdAt"]
    payload["created_at"] = "2026-07-20T12:00:00Z"

    event = WebhookEvent.from_dict(payload)
    assert event.created_at == datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def test_invalid_created_at_raises():
    with pytest.raises(InvalidRequestError) as exc_info:
        WebhookEvent.from_dict(_api_response(createdAt="not-a-timestamp"))

    assert "createdAt" in exc_info.value.field_errors


@pytest.mark.parametrize("livemode,expected", [(True, True), (False, False)])
def test_livemode_reflects_payload(livemode, expected):
    event = WebhookEvent.from_dict(_api_response(livemode=livemode))
    assert event.livemode is expected


def test_livemode_string_value_raises():
    with pytest.raises(InvalidRequestError):
        WebhookEvent.from_dict(_api_response(livemode="false"))


@pytest.mark.parametrize("field", ["id", "type", "data", "createdAt", "livemode"])
def test_missing_required_field_raises(field):
    payload = _api_response()
    del payload[field]

    with pytest.raises(InvalidRequestError):
        WebhookEvent.from_dict(payload)


def test_non_dict_payload_raises():
    with pytest.raises(InvalidRequestError):
        WebhookEvent.from_dict("not-a-payload")


def test_unknown_fields_are_preserved():
    event = WebhookEvent.from_dict(_api_response(apiVersion="2026-07-01"))
    assert event.apiVersion == "2026-07-01"


def test_to_dict_round_trips_by_alias():
    event = WebhookEvent.from_dict(_api_response())
    dumped = event.to_dict()

    assert dumped["id"] == "evt_123"
    assert dumped["createdAt"] == datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    assert dumped["data"] == PAYMENT_DATA
    assert dumped["livemode"] is True


def test_repr_shows_event_id():
    event = WebhookEvent.from_dict(_api_response())
    assert repr(event) == "<WebhookEvent id='evt_123'>"


def test_event_type_constants_compare_to_wire_strings():
    assert WebhookEventType.PAYMENT_COMPLETED == "payment.completed"
    assert WebhookEventType.INVOICE_PAID == "invoice.paid"
    assert WebhookEventType.SWAP_SLIPPAGE_EXCEEDED == "swap.slippage_exceeded"


def test_event_type_usable_in_conditionals():
    event = WebhookEvent.from_dict(_api_response())
    assert event.type == WebhookEventType.PAYMENT_COMPLETED
    assert event.type != WebhookEventType.PAYMENT_EXPIRED


def test_unknown_event_type_still_parses():
    event = WebhookEvent.from_dict(_api_response(type="payment.refunded"))
    assert event.type == "payment.refunded"


def test_exported_from_package_root():
    assert shade.WebhookEvent is WebhookEvent
    assert shade.WebhookEventType is WebhookEventType
