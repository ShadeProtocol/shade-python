"""
Tests for configurable timeout and retry settings (issue #5).

Covers:
* shade.timeout and shade.max_retries module-level settings
* ShadeClient/Gateway per-instance overrides
* Validation of out-of-range values
* Timeout passed through to the HTTP transport layer
* max_retries=0 disables retries entirely
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import shade
from shade import Gateway, ShadeClient
from shade import config as _config
from shade.config import validate_client_settings
from shade.errors import RateLimitError
from shade.http import SyncHTTPClient


@pytest.fixture(autouse=True)
def _reset_client_settings():
    _config.reset()
    yield
    _config.reset()



# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidateClientSettings:
    @pytest.mark.parametrize("timeout", [0, -1.0, -0.1])
    def test_non_positive_timeout_raises(self, timeout):
        with pytest.raises(ValueError, match="timeout must be greater than 0"):
            validate_client_settings(timeout, 3)

    @pytest.mark.parametrize("max_retries", [-1, 11, 100])
    def test_out_of_range_max_retries_raises(self, max_retries):
        with pytest.raises(ValueError, match="max_retries must be between 0 and 10"):
            validate_client_settings(30.0, max_retries)

    def test_boundary_values_are_valid(self):
        validate_client_settings(0.1, 0)
        validate_client_settings(30.0, 10)


# ---------------------------------------------------------------------------
# Module-level shade.timeout / shade.max_retries
# ---------------------------------------------------------------------------

class TestModuleLevelClientSettings:
    def test_defaults(self):
        assert shade.timeout == 30.0
        assert shade.max_retries == 3

    def test_assignment_is_readable(self):
        shade.timeout = 15.0
        shade.max_retries = 5
        assert shade.timeout == 15.0
        assert shade.max_retries == 5

    def test_assignment_updates_config(self):
        shade.timeout = 12.0
        shade.max_retries = 2
        assert _config.timeout == 12.0
        assert _config.max_retries == 2

    def test_used_when_no_per_client_override(self):
        shade.timeout = 12.0
        shade.max_retries = 2
        client = ShadeClient(api_key="test-key")
        assert client._http.timeout == 12.0
        assert client._http.max_retries == 2
        assert client._async_http.timeout == 12.0
        assert client._async_http.max_retries == 2


# ---------------------------------------------------------------------------
# Per-client overrides
# ---------------------------------------------------------------------------

class TestPerClientSettings:
    def test_timeout_override(self):
        shade.timeout = 30.0
        client = ShadeClient(api_key="test-key", timeout=5.0)
        assert client._http.timeout == 5.0
        assert client._async_http.timeout == 5.0

    def test_max_retries_override(self):
        shade.max_retries = 3
        client = ShadeClient(api_key="test-key", max_retries=0)
        assert client._http.max_retries == 0
        assert client._async_http.max_retries == 0

    def test_per_client_beats_module_level(self):
        shade.timeout = 30.0
        shade.max_retries = 3
        client = ShadeClient(api_key="test-key", timeout=5.0, max_retries=1)
        assert client._http.timeout == 5.0
        assert client._http.max_retries == 1

    def test_timeout_reaches_the_httpx_transport(self):
        client = ShadeClient(api_key="test-key", timeout=5.0)

        with patch.object(client._client._http, "request") as mock_request:
            client.request("GET", "/payments")

        assert mock_request.call_args.kwargs["timeout"] == 5.0

    def test_module_level_timeout_reaches_the_httpx_transport(self):
        shade.timeout = 7.0
        client = ShadeClient(api_key="test-key")

        with patch.object(client._client._http, "request") as mock_request:
            client.request("GET", "/payments")

        assert mock_request.call_args.kwargs["timeout"] == 7.0

    def test_invalid_timeout_on_client_raises(self):
        with pytest.raises(ValueError, match="timeout must be greater than 0"):
            ShadeClient(api_key="test-key", timeout=-1.0)

    def test_invalid_max_retries_on_client_raises(self):
        with pytest.raises(ValueError, match="max_retries must be between 0 and 10"):
            ShadeClient(api_key="test-key", max_retries=11)


# ---------------------------------------------------------------------------
# Acceptance criteria: timeout and retry behaviour
# ---------------------------------------------------------------------------

class TestTimeoutBehaviour:
    def test_shade_client_timeout_passed_to_urlopen(self):
        client = SyncHTTPClient(
            base_url="https://api.example.com",
            api_key="test-key",
            timeout=5.0,
        )

        with patch.object(client, "_execute") as mock_execute:
            mock_execute.return_value = (200, {}, b'{"ok": true}')
            client.request("GET", "/payments")

        mock_execute.assert_called_once()
        req = mock_execute.call_args[0][0]
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.status = 200
            mock_urlopen.return_value.__enter__.return_value.headers = {}
            mock_urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
            client._execute(req)
        mock_urlopen.assert_called_once_with(req, timeout=5.0)


class TestMaxRetriesBehaviour:
    def _fake_429_body(self) -> bytes:
        return json.dumps({"error": {"message": "rate limit"}}).encode()

    def test_max_retries_zero_disables_retries(self):
        client = SyncHTTPClient(
            base_url="https://api.example.com",
            api_key="test-key",
            max_retries=0,
        )

        def fake_execute(req):
            return 429, {"Retry-After": "3"}, self._fake_429_body()

        with patch.object(client, "_execute", side_effect=fake_execute), patch(
            "time.sleep"
        ) as mock_sleep:
            with pytest.raises(RateLimitError):
                client.request("POST", "/payments", {})

        mock_sleep.assert_not_called()

    def test_shade_client_max_retries_zero_disables_retries(self):
        client = ShadeClient(api_key="test-key", max_retries=0)

        def fake_execute(req):
            return 429, {"Retry-After": "3"}, self._fake_429_body()

        with patch.object(client._http, "_execute", side_effect=fake_execute), patch(
            "time.sleep"
        ) as mock_sleep:
            with pytest.raises(RateLimitError):
                client._http.request("POST", "/payments", {})

        mock_sleep.assert_not_called()


class TestShadeClientRelationship:
    def test_gateway_is_a_shade_client(self):
        assert issubclass(Gateway, ShadeClient)

    def test_gateway_inherits_client_settings(self):
        gateway = Gateway(api_key="test-key", timeout=7.0, max_retries=1)
        assert gateway._http.timeout == 7.0
        assert gateway._http.max_retries == 1
