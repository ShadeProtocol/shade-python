"""
Shade SDK exceptions.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator, List, Optional, Tuple

from stellar_sdk.exceptions import BaseHorizonError, SdkError

INVALID_REQUEST_STATUS_CODES = (400, 422)

# Sentinel distinguishing "field_errors not supplied" (parse it from the body)
# from an explicit ``field_errors=None`` passed by the response funnel.
_UNSET = object()


class ShadeError(Exception):
    """Base exception for all Shade SDK errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status code: {self.status_code})"


class HTTPError(ShadeError):
    """Raised for non-2xx responses that are not handled by a more specific error."""

    def __init__(
        self,
        message: str,
        status_code: int,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)


class RateLimitError(HTTPError):
    """
    Raised when the API returns HTTP 429 Too Many Requests and either:
    - auto-retry is disabled, or
    - ``max_retries`` has been exhausted.

    Attributes
    ----------
    retry_after : int | None
        Seconds to wait before the next attempt, parsed from the
        ``Retry-After`` response header.  ``None`` if the header was absent.
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        status_code: int = 429,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, response_body=response_body)
        self.retry_after = retry_after

    def __str__(self) -> str:  # pragma: no cover
        base = super().__str__()
        if self.retry_after is not None:
            return f"{base} (retry after {self.retry_after}s)"
        return base


class AuthenticationError(ShadeError):
    """Raised when authentication fails or credentials are invalid."""


class InvalidRequestError(ShadeError):
    """Raised when a request is malformed or rejected by validation (HTTP 400/422).

    Attributes:
        param: The offending parameter named by the API, if any.
        field_errors: Field-level validation errors. When supplied explicitly by
            the SDK's response funnel it reflects exactly what the body provided
            (a dict, a list, or ``None`` when absent). When the error is built
            directly from a response body, it is parsed into a dict (``{}`` when
            absent).
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        param: Optional[str] = None,
        field_errors: Any = _UNSET,
    ) -> None:
        super().__init__(message, status_code, response_body)
        parsed = _parse_error_response(response_body)
        self.param: Optional[str] = param if param is not None else parsed.get("param")
        self.field_errors: Any = (
            parsed.get("field_errors", {}) if field_errors is _UNSET else field_errors
        )

    def __str__(self) -> str:
        message = self.message
        if self.param:
            message = f"{message} (param: {self.param})"
        if self.status_code is None:
            return message
        return f"{message} (status code: {self.status_code})"

    @classmethod
    def from_response(
        cls,
        status_code: int,
        response_body: Optional[str] = None,
    ) -> "InvalidRequestError":
        """Construct from a raw 400/422 API response body."""
        parsed = _parse_error_response(response_body)
        message = parsed.get("message") or "Invalid request"
        return cls(
            message,
            status_code=status_code,
            response_body=response_body,
            param=parsed.get("param"),
            field_errors=parsed.get("field_errors", {}),
        )


class NotFoundError(ShadeError):
    """Raised on HTTP 404 responses.

    Attributes:
        resource_type: Kind of resource that was not found (e.g. "payment", "invoice").
        resource_id:   ID of the missing resource.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code, response_body)
        parsed = _parse_body(response_body)
        self.resource_type: Optional[str] = resource_type or parsed.get("resource_type")
        self.resource_id: Optional[str] = resource_id or parsed.get("resource_id")

    @classmethod
    def from_response(
        cls,
        message: str,
        response_body: Optional[str] = None,
    ) -> "NotFoundError":
        """Construct from a raw 404 response body."""
        return cls(message, status_code=404, response_body=response_body)


class SignatureVerificationError(ShadeError):
    """
    Raised by ``Webhook.construct_event()`` when the HMAC-SHA256 signature in
    the ``Shade-Signature`` header does not match the signature computed for
    the payload.

    Attributes:
        header: The raw ``Shade-Signature`` header value as received.
    """

    def __init__(
        self,
        message: str,
        header: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.header = header

    @classmethod
    def from_mismatch(cls, header: Optional[str] = None) -> "SignatureVerificationError":
        """Construct the error for a computed/received signature mismatch.

        The expected signature is deliberately not accepted as an argument
        here, so it can never end up in the exception message.
        """
        message = (
            "Webhook signature verification failed: the received signature "
            "does not match the signature computed for this payload. This "
            "usually means the webhook secret is incorrect, or the payload "
            "was modified in transit."
        )
        return cls(message, header=header)


class NetworkError(ShadeError):
    """Raised when the SDK cannot complete a network request."""


OPERATION_SUCCESS_CODE = "op_success"

# Human-readable descriptions for the Stellar result codes this SDK is most
# likely to surface. Codes absent from these tables fall back to the raw code,
# so an unrecognised code still reaches the caller intact.
TRANSACTION_RESULT_CODE_DESCRIPTIONS: dict[str, str] = {
    "tx_failed": "one or more operations in the transaction failed",
    "tx_too_early": "the ledger closed before the transaction's minimum time bound",
    "tx_too_late": "the ledger closed after the transaction's maximum time bound",
    "tx_missing_operation": "the transaction contained no operations",
    "tx_bad_seq": "the sequence number does not match the source account",
    "tx_bad_auth": "too few valid signatures, or the wrong network was used",
    "tx_bad_auth_extra": "the transaction carries signatures that were not needed",
    "tx_insufficient_balance": "the fee would drop the source account below its minimum reserve",
    "tx_insufficient_fee": "the fee offered is below the network minimum",
    "tx_no_source_account": "the source account does not exist on the network",
    "tx_internal_error": "Horizon reported an unknown internal error",
    "tx_not_supported": "the network does not support this transaction",
    "tx_fee_bump_inner_failed": "the inner transaction of the fee bump failed",
}

OPERATION_RESULT_CODE_DESCRIPTIONS: dict[str, str] = {
    "op_malformed": "the operation is malformed",
    "op_underfunded": "the source account does not hold enough of the asset",
    "op_src_no_trust": "the source account has no trustline for the asset",
    "op_src_not_authorized": "the source account is not authorized to send the asset",
    "op_no_destination": "the destination account does not exist on the network",
    "op_no_trust": "the destination account has no trustline for the asset",
    "op_not_authorized": "the destination account is not authorized to hold the asset",
    "op_line_full": "the transfer would exceed the destination account's trustline limit",
    "op_no_issuer": "the issuer of the asset does not exist",
    "op_low_reserve": "the resulting account would fall below its minimum reserve",
    "op_bad_auth": "the operation was not signed by enough authorized signers",
    "op_no_account": "the source account of the operation does not exist",
    "op_not_supported": "the network does not support this operation",
    "change_trust_no_issuer": "the issuer of the asset does not exist",
    "change_trust_invalid_limit": "the requested trustline limit is invalid",
    "change_trust_low_reserve": "the account cannot cover the reserve for a new trustline",
    "change_trust_self_not_allowed": "an account cannot create a trustline to itself",
}


class StellarError(ShadeError):
    """Raised when a Stellar/Horizon call fails.

    Wraps the underlying ``stellar_sdk`` exception so callers keep access to the
    raw error while still handling a single SDK exception type. Build one from a
    caught ``stellar_sdk`` exception with :meth:`from_exception`, or let
    :func:`wrap_stellar_errors` do it for a whole block.

    Attributes:
        stellar_result_code: The transaction-level Horizon result code (e.g.
            ``"tx_failed"``, ``"tx_insufficient_fee"``). Falls back to the first
            failing operation code when Horizon reported no transaction code, and
            is ``None`` when the failure carried no result codes at all.
        operation_result_codes: Per-operation result codes exactly as Horizon
            ordered them, including any ``"op_success"`` entries. Empty when the
            failure was not a rejected transaction.
        original_error: The raw ``stellar_sdk`` exception, or ``None`` when the
            error was constructed directly.
    """

    def __init__(
        self,
        message: str,
        stellar_result_code: Optional[str] = None,
        original_error: Optional[Exception] = None,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
        operation_result_codes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message, status_code, response_body)
        self.stellar_result_code = stellar_result_code
        self.original_error = original_error
        self.operation_result_codes: List[str] = list(operation_result_codes or [])

    @property
    def failed_operation_code(self) -> Optional[str]:
        """The first operation result code that is not ``"op_success"``.

        Lets callers branch on the specific failure (``op_no_trust``,
        ``op_underfunded``, …) without walking
        :attr:`operation_result_codes` themselves.
        """
        for code in self.operation_result_codes:
            if code != OPERATION_SUCCESS_CODE:
                return code
        return None

    def __str__(self) -> str:
        message = self.message
        if self.stellar_result_code:
            message = f"{message} (result code: {self.stellar_result_code})"
        if self.status_code is None:
            return message
        return f"{message} (status code: {self.status_code})"

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        message: Optional[str] = None,
    ) -> "StellarError":
        """Wrap a ``stellar_sdk`` exception, pulling out any Horizon result codes.

        Args:
            exc: The caught ``stellar_sdk`` exception.
            message: Overrides the message derived from the result codes. Useful
                for adding operation context the exception cannot know about
                (e.g. "Failed to submit payout txn_123").
        """
        transaction_code, operation_codes = _stellar_result_codes(exc)
        status_code, response_body = _horizon_context(exc)
        return cls(
            message or _stellar_failure_message(exc, transaction_code, operation_codes),
            stellar_result_code=transaction_code
            or next((c for c in operation_codes if c != OPERATION_SUCCESS_CODE), None),
            original_error=exc,
            status_code=status_code,
            response_body=response_body,
            operation_result_codes=operation_codes,
        )


@contextmanager
def wrap_stellar_errors(message: Optional[str] = None) -> Generator[None, None, None]:
    """Re-raise any ``stellar_sdk`` failure inside the block as :class:`StellarError`.

    The Stellar integration layer wraps its Horizon and Soroban calls with this
    so callers only ever have to catch :class:`~shade.errors.ShadeError`::

        with wrap_stellar_errors("Failed to submit payment"):
            server.submit_transaction(transaction)

    Args:
        message: Overrides the derived message on the raised ``StellarError``.
            The result codes and original exception are attached either way.
    """
    try:
        yield
    except SdkError as exc:
        raise StellarError.from_exception(exc, message=message) from exc


def _stellar_result_codes(exc: Exception) -> Tuple[Optional[str], List[str]]:
    """Return ``(transaction_code, operation_codes)`` from a Horizon error.

    Horizon reports these under ``extras.result_codes``. Every level is
    type-checked rather than assumed, so a malformed or partial error body
    degrades to "no codes" instead of raising while building an exception.
    """
    extras = getattr(exc, "extras", None)
    if not isinstance(extras, dict):
        return None, []
    result_codes = extras.get("result_codes")
    if not isinstance(result_codes, dict):
        return None, []

    transaction = result_codes.get("transaction")
    operations = result_codes.get("operations")
    return (
        transaction if isinstance(transaction, str) else None,
        [code for code in operations if isinstance(code, str)]
        if isinstance(operations, list)
        else [],
    )


def _horizon_context(exc: Exception) -> Tuple[Optional[int], Optional[str]]:
    """Return ``(status_code, response_body)`` for a Horizon error, else ``(None, None)``."""
    if not isinstance(exc, BaseHorizonError):
        return None, None
    status = getattr(exc, "status", None)
    body = getattr(exc, "message", None)
    return (
        status if isinstance(status, int) else None,
        body if isinstance(body, str) else None,
    )


def _stellar_failure_message(
    exc: Exception,
    transaction_code: Optional[str],
    operation_codes: List[str],
) -> str:
    """Build a human-readable message for a Stellar failure.

    Prefers the most specific signal available: a failing operation code first
    (that is what actually went wrong), then the transaction code, then whatever
    Horizon or the exception itself described.
    """
    operation_code = next(
        (code for code in operation_codes if code != OPERATION_SUCCESS_CODE), None
    )
    if operation_code is not None:
        description = OPERATION_RESULT_CODE_DESCRIPTIONS.get(operation_code)
        if description:
            return f"Stellar transaction failed: {description} ({operation_code})"
        return f"Stellar transaction failed: {operation_code}"

    if transaction_code is not None:
        description = TRANSACTION_RESULT_CODE_DESCRIPTIONS.get(transaction_code)
        return f"Stellar transaction failed: {description or transaction_code}"

    account_id = getattr(exc, "account_id", None)
    if account_id:
        return f"Stellar account {account_id} does not exist on the network"

    title = getattr(exc, "title", None)
    detail = getattr(exc, "detail", None)
    if title and detail:
        return f"Stellar request failed: {title} - {detail}"
    if title or detail:
        return f"Stellar request failed: {title or detail}"

    text = str(exc).strip()
    return f"Stellar request failed: {text or type(exc).__name__}"


def raise_for_invalid_request(
    status_code: int,
    response_body: Optional[str] = None,
) -> None:
    """Raise InvalidRequestError when the API returns 400 or 422."""
    if status_code in INVALID_REQUEST_STATUS_CODES:
        raise InvalidRequestError.from_response(status_code, response_body)


def _parse_body(response_body: Optional[str]) -> dict[str, Any]:
    if not response_body:
        return {}
    try:
        data = json.loads(response_body)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _parse_error_response(response_body: Optional[str]) -> dict[str, Any]:
    data = _parse_body(response_body)
    error = data.get("error", {})
    if not isinstance(error, dict):
        error = {}

    field_errors = error.get("field_errors")
    if not isinstance(field_errors, dict):
        field_errors = data.get("field_errors")
    if not isinstance(field_errors, dict):
        field_errors = {}

    message = error.get("message")
    if not message:
        message = data.get("message")

    return {
        "message": message,
        "param": error.get("param"),
        "field_errors": field_errors,
    }
