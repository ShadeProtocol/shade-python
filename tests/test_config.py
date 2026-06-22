import pytest

from shade import Environment
from shade.config import parse_environment


def test_parse_environment_accepts_string_shorthands():
    assert parse_environment("sandbox") is Environment.SANDBOX
    assert parse_environment("production") is Environment.PRODUCTION


def test_parse_environment_rejects_invalid_strings_with_valid_options():
    with pytest.raises(ValueError) as exc:
        parse_environment("staging")

    message = str(exc.value)
    assert "staging" in message
    assert "sandbox" in message
    assert "production" in message

