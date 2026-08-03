import pytest
from stellar_sdk import Keypair

import shade
from shade import InvalidRequestError, Merchant, ShadeObject

VALID_ADDRESS = Keypair.random().public_key


def _api_response(**overrides):
    """A representative camelCase backend payload."""
    data = {
        "id": "clx123",
        "merchantId": 42,
        "address": VALID_ADDRESS,
        "account": "GACCOUNT",
        "email": "owner@acme.test",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "businessName": "Acme Payments",
        "category": "software",
        "description": "We take money.",
        "logo": "https://cdn.test/logo.png",
        "webhook": "https://acme.test/hooks",
        "active": True,
        "verified": True,
    }
    data.update(overrides)
    return data


def test_from_dict_maps_camelcase_to_snake_case():
    merchant = Merchant.from_dict(_api_response())

    assert merchant.id == "clx123"
    assert merchant.merchant_id == 42
    assert merchant.address == VALID_ADDRESS
    assert merchant.first_name == "Ada"
    assert merchant.last_name == "Lovelace"
    assert merchant.business_name == "Acme Payments"
    assert merchant.active is True
    assert merchant.verified is True


def test_merchant_id_is_int():
    merchant = Merchant.from_dict(_api_response(merchantId=7))
    assert isinstance(merchant.merchant_id, int)
    assert merchant.merchant_id == 7


def test_merchant_is_exported_from_package():
    assert shade.Merchant is Merchant
    assert issubclass(Merchant, ShadeObject)


def test_from_dict_preserves_unknown_keys():
    # The ShadeObject base allows extra fields so a server-side addition never
    # breaks an older SDK; the known fields still map correctly.
    merchant = Merchant.from_dict(_api_response(createdAt="2026-01-01"))
    assert merchant.merchant_id == 42
    assert merchant.to_dict()["createdAt"] == "2026-01-01"


def test_from_dict_requires_a_mapping():
    with pytest.raises(InvalidRequestError):
        Merchant.from_dict([("id", "x")])  # type: ignore[arg-type]


def test_invalid_address_raises_on_construction():
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(address="not-a-stellar-key"))
    assert exc_info.value.param == "address"


def test_address_wrong_length_is_rejected():
    with pytest.raises(InvalidRequestError):
        Merchant(
            id="x",
            merchant_id=1,
            address="G" + "A" * 55,  # starts with G but too short / bad checksum
            active=True,
            verified=False,
        )


def test_non_integer_merchant_id_raises():
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(merchantId="abc"))
    assert exc_info.value.param == "merchantId"


def test_boolean_merchant_id_is_rejected():
    # A bool would otherwise be coerced to 1/0; it is never a valid merchant id.
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(merchantId=True))
    assert exc_info.value.param == "merchantId"


@pytest.mark.parametrize("field", ["active", "verified"])
@pytest.mark.parametrize("value", ["false", "true", "", 0, 1, None])
def test_non_boolean_flags_are_rejected(field, value):
    """Strings like "false" must not be silently coerced to True."""
    with pytest.raises(InvalidRequestError) as exc_info:
        Merchant.from_dict(_api_response(**{field: value}))
    assert exc_info.value.param == field


def test_boolean_flags_are_preserved():
    merchant = Merchant.from_dict(_api_response(active=False, verified=True))
    assert merchant.active is False
    assert merchant.verified is True


def test_display_name_prefers_business_name():
    merchant = Merchant.from_dict(_api_response())
    assert merchant.display_name == "Acme Payments"


def test_display_name_falls_back_to_full_name():
    merchant = Merchant.from_dict(_api_response(businessName=None))
    assert merchant.display_name == "Ada Lovelace"


def test_display_name_trims_missing_last_name():
    merchant = Merchant.from_dict(_api_response(businessName=None, lastName=None))
    assert merchant.display_name == "Ada"


def test_display_name_falls_back_to_email():
    merchant = Merchant.from_dict(
        _api_response(businessName=None, firstName=None, lastName=None)
    )
    assert merchant.display_name == "owner@acme.test"


def test_display_name_ignores_whitespace_only_business_name():
    """A blank business_name must fall through, not be returned verbatim."""
    merchant = Merchant.from_dict(_api_response(businessName="   "))
    assert merchant.display_name == "Ada Lovelace"


def test_display_name_ignores_whitespace_only_names():
    merchant = Merchant.from_dict(
        _api_response(businessName="", firstName="  ", lastName="\t")
    )
    assert merchant.display_name == "owner@acme.test"


def test_display_name_is_none_when_every_candidate_is_blank():
    merchant = Merchant.from_dict(
        _api_response(businessName="   ", firstName="", lastName=None, email="  ")
    )
    assert merchant.display_name is None


def test_display_name_trims_the_returned_value():
    merchant = Merchant.from_dict(_api_response(businessName="  Acme Payments  "))
    assert merchant.display_name == "Acme Payments"


def test_display_name_normalizes_padded_name_components():
    # Each component is stripped before joining, so padding does not leak into
    # the middle of the full name as a double space.
    merchant = Merchant.from_dict(
        _api_response(businessName=None, firstName=" Ada ", lastName=" Lovelace ")
    )
    assert merchant.display_name == "Ada Lovelace"


def test_optional_fields_default_to_none():
    merchant = Merchant(
        id="x",
        merchant_id=1,
        address=VALID_ADDRESS,
        active=False,
        verified=False,
    )
    assert merchant.account is None
    assert merchant.email is None
    assert merchant.display_name is None


def test_to_dict_round_trips_to_camelcase():
    payload = _api_response()
    merchant = Merchant.from_dict(payload)
    assert merchant.to_dict() == payload
    assert Merchant.from_dict(merchant.to_dict()) == merchant
