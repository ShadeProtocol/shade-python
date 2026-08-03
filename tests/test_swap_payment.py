"""
Tests for SwapPayment model and SwapStatus enum (issue #41).
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import datetime, timezone

from shade.models.swap import SwapPayment, SwapStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_PAYLOAD: dict = {
    "id": "swap_abc123",
    "pay_in_token": "USDC",
    "settle_out_token": "XLM",
    "amount_in": "250.00",
    "amount_out": "1500.75",
    "routing_path": ["USDC", "XLM"],
    "slippage_tolerance": 0.005,
    "status": "pending",
    "stellar_tx_hash": None,
    "created_at": "2024-06-01T12:00:00",
}


# ---------------------------------------------------------------------------
# SwapStatus enum
# ---------------------------------------------------------------------------


class TestSwapStatus:
    def test_all_members_present(self):
        members = {s.value for s in SwapStatus}
        assert members == {
            "pending",
            "swapping",
            "completed",
            "failed",
            "slippage_exceeded",
        }

    def test_is_str_enum(self):
        """SwapStatus members should compare equal to their string values."""
        assert SwapStatus.PENDING == "pending"
        assert SwapStatus.SLIPPAGE_EXCEEDED == "slippage_exceeded"

    def test_construction_from_value(self):
        assert SwapStatus("completed") is SwapStatus.COMPLETED


# ---------------------------------------------------------------------------
# SwapPayment.from_dict
# ---------------------------------------------------------------------------


class TestSwapPaymentFromDict:
    def test_populates_all_fields(self):
        swap = SwapPayment.from_dict(VALID_PAYLOAD)

        assert swap.id == "swap_abc123"
        assert swap.pay_in_token == "USDC"
        assert swap.settle_out_token == "XLM"
        assert swap.amount_in == Decimal("250.00")
        assert swap.amount_out == Decimal("1500.75")
        assert swap.routing_path == ["USDC", "XLM"]
        assert swap.slippage_tolerance == 0.005
        assert swap.status is SwapStatus.PENDING
        assert swap.stellar_tx_hash is None
        assert swap.created_at == datetime(2024, 6, 1, 12, 0, 0)

    def test_routing_path_is_list(self):
        payload = {**VALID_PAYLOAD, "routing_path": ["USDC", "XLM", "BTC"]}
        swap = SwapPayment.from_dict(payload)
        assert isinstance(swap.routing_path, list)
        assert swap.routing_path == ["USDC", "XLM", "BTC"]

    def test_amount_out_none_when_absent(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "amount_out"}
        swap = SwapPayment.from_dict(payload)
        assert swap.amount_out is None

    def test_stellar_tx_hash_populated(self):
        tx = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        payload = {**VALID_PAYLOAD, "stellar_tx_hash": tx, "status": "completed"}
        swap = SwapPayment.from_dict(payload)
        assert swap.stellar_tx_hash == tx

    def test_status_is_swap_status_enum(self):
        for status_value in ("pending", "swapping", "completed", "failed", "slippage_exceeded"):
            payload = {**VALID_PAYLOAD, "status": status_value}
            swap = SwapPayment.from_dict(payload)
            assert isinstance(swap.status, SwapStatus)

    def test_created_at_parsed_from_iso_string(self):
        swap = SwapPayment.from_dict(VALID_PAYLOAD)
        assert isinstance(swap.created_at, datetime)

    def test_created_at_none_when_absent(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "created_at"}
        swap = SwapPayment.from_dict(payload)
        assert swap.created_at is None

    def test_created_at_accepts_datetime_object(self):
        dt = datetime(2024, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
        payload = {**VALID_PAYLOAD, "created_at": dt}
        swap = SwapPayment.from_dict(payload)
        assert swap.created_at is dt

    def test_missing_required_field_raises_key_error(self):
        for required in ("id", "pay_in_token", "settle_out_token", "amount_in", "routing_path", "slippage_tolerance", "status"):
            payload = {k: v for k, v in VALID_PAYLOAD.items() if k != required}
            with pytest.raises(KeyError):
                SwapPayment.from_dict(payload)


# ---------------------------------------------------------------------------
# slippage_tolerance validation
# ---------------------------------------------------------------------------


class TestSlippageValidation:
    def _make(self, slippage: float) -> SwapPayment:
        return SwapPayment(
            id="swap_test",
            pay_in_token="USDC",
            settle_out_token="XLM",
            amount_in=Decimal("100"),
            routing_path=["USDC", "XLM"],
            slippage_tolerance=slippage,
            status=SwapStatus.PENDING,
        )

    @pytest.mark.parametrize("valid", [0.001, 0.005, 0.01, 0.1, 0.5, 0.999])
    def test_valid_slippage_accepted(self, valid: float):
        swap = self._make(valid)
        assert swap.slippage_tolerance == valid

    @pytest.mark.parametrize("invalid", [0.0, 1.0, -0.1, 1.5, 2.0, -1.0])
    def test_invalid_slippage_raises_value_error(self, invalid: float):
        with pytest.raises(ValueError, match="slippage_tolerance"):
            self._make(invalid)

    def test_from_dict_invalid_slippage_raises_value_error(self):
        payload = {**VALID_PAYLOAD, "slippage_tolerance": 0.0}
        with pytest.raises(ValueError):
            SwapPayment.from_dict(payload)


# ---------------------------------------------------------------------------
# Direct constructor
# ---------------------------------------------------------------------------


class TestSwapPaymentConstructor:
    def test_routing_path_is_copied(self):
        original = ["USDC", "XLM"]
        swap = SwapPayment(
            id="swap_001",
            pay_in_token="USDC",
            settle_out_token="XLM",
            amount_in=Decimal("50"),
            routing_path=original,
            slippage_tolerance=0.01,
            status=SwapStatus.PENDING,
        )
        original.append("BTC")
        assert "BTC" not in swap.routing_path  # internal list is a copy

    def test_optional_fields_default_to_none(self):
        swap = SwapPayment(
            id="swap_002",
            pay_in_token="BTC",
            settle_out_token="USDC",
            amount_in=Decimal("0.01"),
            routing_path=["BTC", "XLM", "USDC"],
            slippage_tolerance=0.02,
            status=SwapStatus.SWAPPING,
        )
        assert swap.amount_out is None
        assert swap.stellar_tx_hash is None
        assert swap.created_at is None


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


class TestSwapPaymentToDict:
    def test_round_trip_via_from_dict(self):
        swap = SwapPayment.from_dict(VALID_PAYLOAD)
        d = swap.to_dict()
        swap2 = SwapPayment.from_dict(d)

        assert swap2.id == swap.id
        assert swap2.amount_in == swap.amount_in
        assert swap2.routing_path == swap.routing_path
        assert swap2.status == swap.status

    def test_status_serialised_as_string(self):
        swap = SwapPayment.from_dict(VALID_PAYLOAD)
        d = swap.to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "pending"

    def test_routing_path_copy_is_returned(self):
        """Mutating the serialised routing_path must not affect the model."""
        swap = SwapPayment.from_dict(VALID_PAYLOAD)
        d = swap.to_dict()
        d["routing_path"].append("BTC")  # mutate the returned copy
        assert "BTC" not in swap.routing_path  # internal list unchanged


# ---------------------------------------------------------------------------
# Constructor raw-string status coercion (CodeRabbit fix)
# ---------------------------------------------------------------------------


class TestSwapStatusCoercion:
    def _make(self, status_value) -> SwapPayment:
        return SwapPayment(
            id="swap_coerce",
            pay_in_token="USDC",
            settle_out_token="XLM",
            amount_in=Decimal("100"),
            routing_path=["USDC", "XLM"],
            slippage_tolerance=0.01,
            status=status_value,
        )

    def test_raw_string_is_coerced_to_enum(self):
        """Passing a raw status string should still store a SwapStatus enum."""
        swap = self._make("completed")
        assert isinstance(swap.status, SwapStatus)
        assert swap.status is SwapStatus.COMPLETED

    def test_to_dict_works_after_raw_string_status(self):
        """to_dict must not raise even when status was constructed from a string."""
        swap = self._make("swapping")
        d = swap.to_dict()
        assert d["status"] == "swapping"

    def test_invalid_raw_string_raises_value_error(self):
        """An unknown status string should raise ValueError from the enum."""
        with pytest.raises(ValueError):
            self._make("unknown_status")
