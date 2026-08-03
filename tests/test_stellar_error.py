"""
Tests for StellarError and the wrap_stellar_errors helper (issue #22).

Acceptance criteria covered:
* A rejected Stellar transaction raises StellarError carrying the Horizon
  result code.
* ``error.original_error`` exposes the raw ``stellar_sdk`` exception.
* A missing trustline surfaces as StellarError with a descriptive message.
"""
from __future__ import annotations

import httpx
import pytest
from stellar_sdk.exceptions import (
    AccountNotFoundException,
    BadRequestError,
    Ed25519PublicKeyInvalidError,
    NotFoundError as HorizonNotFoundError,
)

import shade
from shade.errors import ShadeError, StellarError, wrap_stellar_errors

DESTINATION = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"


def _horizon_error(
    error_cls=BadRequestError,
    *,
    status: int = 400,
    result_codes: dict | None = None,
    extras: dict | None = None,
    title: str | None = "Transaction Failed",
    detail: str | None = "The transaction failed when submitted to the Stellar network.",
):
    """Build a real stellar_sdk Horizon exception from a synthetic response."""
    body: dict = {
        "type": "https://stellar.org/horizon-errors/transaction_failed",
        "title": title,
        "status": status,
        "detail": detail,
    }
    if extras is not None:
        body["extras"] = extras
    elif result_codes is not None:
        body["extras"] = {"result_codes": result_codes}
    return error_cls(httpx.Response(status_code=status, json=body))


# ---------------------------------------------------------------------------
# Rejected transactions
# ---------------------------------------------------------------------------

class TestRejectedTransaction:
    def test_exposes_horizon_transaction_result_code(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_insufficient_fee", "operations": []}
        )

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code == "tx_insufficient_fee"
        assert error.message == (
            "Stellar transaction failed: the fee offered is below the network minimum"
        )

    def test_exposes_operation_result_codes(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_underfunded"]}
        )

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code == "tx_failed"
        assert error.operation_result_codes == ["op_underfunded"]
        assert error.failed_operation_code == "op_underfunded"

    def test_failed_operation_code_skips_successful_operations(self):
        exc = _horizon_error(
            result_codes={
                "transaction": "tx_failed",
                "operations": ["op_success", "op_success", "op_line_full"],
            }
        )

        error = StellarError.from_exception(exc)

        assert error.failed_operation_code == "op_line_full"
        assert error.operation_result_codes == ["op_success", "op_success", "op_line_full"]

    def test_carries_horizon_status_and_raw_body(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_bad_seq", "operations": []}
        )

        error = StellarError.from_exception(exc)

        assert error.status_code == 400
        assert "tx_bad_seq" in error.response_body

    def test_str_includes_result_code_and_status(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_bad_auth", "operations": []}
        )

        error = StellarError.from_exception(exc)

        assert str(error) == (
            "Stellar transaction failed: too few valid signatures, or the wrong "
            "network was used (result code: tx_bad_auth) (status code: 400)"
        )

    def test_falls_back_to_operation_code_when_transaction_code_absent(self):
        exc = _horizon_error(result_codes={"operations": ["op_no_destination"]})

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code == "op_no_destination"


# ---------------------------------------------------------------------------
# Descriptive messages
# ---------------------------------------------------------------------------

class TestDescriptiveMessages:
    def test_missing_trustline_is_described(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_no_trust"]}
        )

        error = StellarError.from_exception(exc)

        assert error.message == (
            "Stellar transaction failed: the destination account has no trustline "
            "for the asset (op_no_trust)"
        )
        assert error.failed_operation_code == "op_no_trust"

    def test_source_missing_trustline_is_described(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_src_no_trust"]}
        )

        error = StellarError.from_exception(exc)

        assert "source account has no trustline" in error.message

    def test_operation_failure_takes_precedence_over_transaction_code(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_underfunded"]}
        )

        error = StellarError.from_exception(exc)

        assert "does not hold enough of the asset" in error.message
        assert "one or more operations" not in error.message

    def test_unrecognised_operation_code_is_passed_through_raw(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_brand_new"]}
        )

        error = StellarError.from_exception(exc)

        assert error.message == "Stellar transaction failed: op_brand_new"
        assert error.failed_operation_code == "op_brand_new"

    def test_unrecognised_transaction_code_is_passed_through_raw(self):
        exc = _horizon_error(result_codes={"transaction": "tx_brand_new"})

        error = StellarError.from_exception(exc)

        assert error.message == "Stellar transaction failed: tx_brand_new"

    def test_explicit_message_overrides_derived_one(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_no_trust"]}
        )

        error = StellarError.from_exception(exc, message="Failed to submit payout txn_1")

        assert error.message == "Failed to submit payout txn_1"
        assert error.stellar_result_code == "tx_failed"
        assert error.failed_operation_code == "op_no_trust"

    def test_horizon_error_without_result_codes_uses_title_and_detail(self):
        exc = _horizon_error(
            HorizonNotFoundError,
            status=404,
            title="Resource Missing",
            detail="The resource at the url requested was not found.",
        )

        error = StellarError.from_exception(exc)

        assert error.message == (
            "Stellar request failed: Resource Missing - The resource at the url "
            "requested was not found."
        )
        assert error.stellar_result_code is None
        assert error.status_code == 404

    def test_account_not_found_names_the_account(self):
        exc = AccountNotFoundException(DESTINATION)

        error = StellarError.from_exception(exc)

        assert error.message == (
            f"Stellar account {DESTINATION} does not exist on the network"
        )

    def test_non_horizon_sdk_error_uses_its_own_text(self):
        exc = Ed25519PublicKeyInvalidError("Invalid Ed25519 Public Key: GBAD")

        error = StellarError.from_exception(exc)

        assert error.message == (
            "Stellar request failed: Invalid Ed25519 Public Key: GBAD"
        )
        assert error.stellar_result_code is None
        assert error.status_code is None
        assert error.response_body is None


# ---------------------------------------------------------------------------
# Access to the underlying exception
# ---------------------------------------------------------------------------

class TestOriginalError:
    def test_original_error_is_the_raw_stellar_exception(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_no_trust"]}
        )

        error = StellarError.from_exception(exc)

        assert error.original_error is exc
        assert isinstance(error.original_error, BadRequestError)
        assert error.original_error.extras["result_codes"]["operations"] == ["op_no_trust"]

    def test_original_error_is_none_when_constructed_directly(self):
        error = StellarError("contract call reverted")

        assert error.original_error is None
        assert error.stellar_result_code is None
        assert error.operation_result_codes == []
        assert error.failed_operation_code is None
        assert str(error) == "contract call reverted"


# ---------------------------------------------------------------------------
# Malformed Horizon payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    def test_missing_extras_yields_no_result_codes(self):
        exc = _horizon_error()

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code is None
        assert error.operation_result_codes == []

    def test_non_dict_result_codes_are_ignored(self):
        exc = _horizon_error(extras={"result_codes": "tx_failed"})

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code is None
        assert error.operation_result_codes == []

    def test_non_list_operations_are_ignored(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": "op_no_trust"}
        )

        error = StellarError.from_exception(exc)

        assert error.stellar_result_code == "tx_failed"
        assert error.operation_result_codes == []

    def test_non_string_operation_entries_are_dropped(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": [None, "op_no_trust"]}
        )

        error = StellarError.from_exception(exc)

        assert error.operation_result_codes == ["op_no_trust"]


# ---------------------------------------------------------------------------
# wrap_stellar_errors
# ---------------------------------------------------------------------------

class TestWrapStellarErrors:
    def test_converts_stellar_exception_and_chains_it(self):
        exc = _horizon_error(
            result_codes={"transaction": "tx_failed", "operations": ["op_no_trust"]}
        )

        with pytest.raises(StellarError) as excinfo:
            with wrap_stellar_errors():
                raise exc

        error = excinfo.value
        assert error.original_error is exc
        assert error.__cause__ is exc
        assert error.stellar_result_code == "tx_failed"

    def test_message_argument_overrides_derived_message(self):
        exc = _horizon_error(result_codes={"transaction": "tx_bad_seq"})

        with pytest.raises(StellarError) as excinfo:
            with wrap_stellar_errors("Failed to submit transfer"):
                raise exc

        assert excinfo.value.message == "Failed to submit transfer"
        assert excinfo.value.stellar_result_code == "tx_bad_seq"

    def test_non_stellar_exceptions_pass_through_untouched(self):
        with pytest.raises(RuntimeError, match="boom"):
            with wrap_stellar_errors():
                raise RuntimeError("boom")

    def test_block_without_errors_is_transparent(self):
        with wrap_stellar_errors():
            result = "submitted"

        assert result == "submitted"


# ---------------------------------------------------------------------------
# Integration with the rest of the SDK
# ---------------------------------------------------------------------------

class TestSdkIntegration:
    def test_stellar_error_is_a_shade_error(self):
        exc = _horizon_error(result_codes={"transaction": "tx_failed"})

        with pytest.raises(ShadeError):
            with wrap_stellar_errors():
                raise exc

    def test_exported_from_the_top_level_package(self):
        assert shade.StellarError is StellarError
        assert shade.wrap_stellar_errors is wrap_stellar_errors
