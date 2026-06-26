import shade
from shade import (
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ShadeError,
)


def test_shade_error_can_be_raised_standalone():
    error = ShadeError("contract rejected payment")

    assert str(error) == "contract rejected payment"
    assert error.message == "contract rejected payment"
    assert error.status_code is None
    assert error.response_body is None


def test_shade_error_includes_http_context():
    error = ShadeError(
        "invalid request",
        status_code=422,
        response_body='{"error":"missing amount"}',
    )

    assert str(error) == "invalid request (status code: 422)"
    assert error.status_code == 422
    assert error.response_body == '{"error":"missing amount"}'


def test_specific_errors_inherit_from_shade_error():
    for error_type in (
        AuthenticationError,
        InvalidRequestError,
        NetworkError,
        NotFoundError,
        RateLimitError,
    ):
        error = error_type("request failed", status_code=400, response_body="raw")

        assert isinstance(error, ShadeError)
        assert str(error) == "request failed (status code: 400)"
        assert error.response_body == "raw"


def test_rate_limit_error_retry_after_from_header():
    error = RateLimitError("too many requests", retry_after=30)

    assert isinstance(error, ShadeError)
    assert error.retry_after == 30
    assert error.status_code == 429


def test_rate_limit_error_retry_after_none_when_absent():
    error = RateLimitError("too many requests")

    assert error.retry_after is None
    assert error.status_code == 429


def test_package_root_exports_error_classes():
    assert shade.ShadeError is ShadeError
    assert shade.AuthenticationError is AuthenticationError
    assert shade.InvalidRequestError is InvalidRequestError
    assert shade.NetworkError is NetworkError
    assert shade.NotFoundError is NotFoundError
    assert shade.RateLimitError is RateLimitError


def test_not_found_error_is_shade_error():
    error = NotFoundError("not found", status_code=404)
    assert isinstance(error, ShadeError)


def test_not_found_error_parses_resource_from_body():
    body = '{"resource_type": "payment", "resource_id": "pay_abc123"}'
    error = NotFoundError("not found", status_code=404, response_body=body)

    assert error.resource_type == "payment"
    assert error.resource_id == "pay_abc123"


def test_not_found_error_explicit_attrs_override_body():
    body = '{"resource_type": "invoice", "resource_id": "inv_999"}'
    error = NotFoundError(
        "not found",
        status_code=404,
        response_body=body,
        resource_type="payment",
        resource_id="pay_001",
    )

    assert error.resource_type == "payment"
    assert error.resource_id == "pay_001"


def test_not_found_error_none_when_body_missing():
    error = NotFoundError("not found", status_code=404)

    assert error.resource_type is None
    assert error.resource_id is None


def test_not_found_error_none_when_body_lacks_fields():
    error = NotFoundError("not found", status_code=404, response_body='{"error":"gone"}')

    assert error.resource_type is None
    assert error.resource_id is None


def test_not_found_error_from_response_factory():
    body = '{"resource_type": "invoice", "resource_id": "inv_456"}'
    error = NotFoundError.from_response("invoice not found", response_body=body)

    assert error.status_code == 404
    assert error.resource_type == "invoice"
    assert error.resource_id == "inv_456"
    assert isinstance(error, ShadeError)


def test_not_found_error_invalid_json_body():
    error = NotFoundError("not found", status_code=404, response_body="not-json")

    assert error.resource_type is None
    assert error.resource_id is None
