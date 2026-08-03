from decimal import Decimal

import pytest

import shade
from shade import AssetBalance, Balance, InvalidRequestError, ShadeObject


def _asset_balance(**overrides):
    data = {
        "assetCode": "XLM",
        "assetIssuer": None,
        "balance": "100.00",
    }
    data.update(overrides)
    return data


def _api_response(**overrides):
    data = {
        "id": "bal_123",
        "merchantId": "merch_456",
        "balances": [
            _asset_balance(assetCode="XLM", assetIssuer=None, balance="100.00"),
            _asset_balance(assetCode="USDC", assetIssuer="GBBD47CC6KON37" + "X" * 42, balance="250.50"),
        ],
    }
    data.update(overrides)
    return data


# ── AssetBalance ──────────────────────────────────────────────────────────


def test_asset_balance_from_dict_maps_camelcase():
    ab = AssetBalance.from_dict(_asset_balance(assetCode="USDC", assetIssuer="GABC"))
    assert ab.asset_code == "USDC"
    assert ab.asset_issuer == "GABC"
    assert ab.balance == Decimal("100.00")


def test_asset_balance_native_has_no_issuer():
    ab = AssetBalance.from_dict(_asset_balance())
    assert ab.asset_code == "XLM"
    assert ab.asset_issuer is None


def test_asset_balance_balance_is_decimal():
    ab = AssetBalance.from_dict(_asset_balance(balance="99.99"))
    assert isinstance(ab.balance, Decimal)
    assert ab.balance == Decimal("99.99")


def test_asset_balance_preserves_unknown_keys():
    ab = AssetBalance.from_dict(_asset_balance(trustlineLimit="1000"))
    assert ab.to_dict()["trustlineLimit"] == "1000"


def test_asset_balance_is_shade_object():
    assert issubclass(AssetBalance, ShadeObject)


def test_asset_balance_repr_uses_asset_code():
    ab = AssetBalance.from_dict(_asset_balance())
    assert "asset_code=" in repr(ab)
    assert "XLM" in repr(ab)


# ── Balance ───────────────────────────────────────────────────────────────


def test_balance_from_dict_maps_camelcase():
    balance = Balance.from_dict(_api_response())
    assert balance.id == "bal_123"
    assert balance.merchant_id == "merch_456"
    assert len(balance.balances) == 2


def test_balance_balances_are_asset_balance_instances():
    balance = Balance.from_dict(_api_response())
    for entry in balance.balances:
        assert isinstance(entry, AssetBalance)


def test_balance_amounts_are_decimal():
    balance = Balance.from_dict(_api_response())
    for entry in balance.balances:
        assert isinstance(entry.balance, Decimal)


def test_balance_get_returns_matching_asset():
    balance = Balance.from_dict(_api_response())
    xlm = balance.get("XLM")
    assert xlm is not None
    assert xlm.asset_code == "XLM"
    assert xlm.balance == Decimal("100.00")


def test_balance_get_returns_usdc():
    balance = Balance.from_dict(_api_response())
    usdc = balance.get("USDC")
    assert usdc is not None
    assert usdc.asset_code == "USDC"
    assert usdc.balance == Decimal("250.50")


def test_balance_get_is_case_insensitive():
    balance = Balance.from_dict(_api_response())
    assert balance.get("xlm") is not None
    assert balance.get("usdc") is not None


def test_balance_get_returns_none_for_missing_asset():
    balance = Balance.from_dict(_api_response())
    assert balance.get("NOTEXIST") is None


def test_balance_get_returns_none_for_empty_balances():
    balance = Balance.from_dict(_api_response(balances=[]))
    assert balance.get("XLM") is None


def test_balance_optional_fields_default_to_none():
    payload = {"balances": [_asset_balance()]}
    balance = Balance.from_dict(payload)
    assert balance.id is None
    assert balance.merchant_id is None


def test_balance_is_exported_from_package():
    assert shade.Balance is Balance
    assert shade.AssetBalance is AssetBalance


def test_balance_preserves_unknown_keys():
    balance = Balance.from_dict(_api_response(metadata="test"))
    assert balance.to_dict()["metadata"] == "test"


def test_balance_to_dict_round_trips():
    payload = _api_response()
    balance = Balance.from_dict(payload)
    round_tripped = balance.to_dict()
    rebalanced = Balance.from_dict(round_tripped)
    assert rebalanced.id == balance.id
    assert rebalanced.merchant_id == balance.merchant_id
    assert len(rebalanced.balances) == len(balance.balances)


def test_balance_repr_uses_id():
    balance = Balance.from_dict(_api_response())
    assert "bal_123" in repr(balance)


def test_balance_repr_without_id():
    balance = Balance.from_dict({"balances": [_asset_balance()]})
    assert repr(balance) == "<Balance>"
