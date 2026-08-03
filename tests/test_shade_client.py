"""
Tests for per-instance ShadeClient configuration (issue #2).

Acceptance criteria covered:
* ShadeClient(api_key=...) creates an isolated client.
* Resource calls use their client's credentials, not the global config.
* Two clients with different keys coexist without interfering.
* ShadeClient.from_env() reads SHADE_API_KEY from the environment.
* Requesting with no api_key and no global key set raises AuthenticationError.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import shade
from shade import BaseResource, Gateway, ShadeClient
from shade.client import (
    API_KEY_ENV_VAR,
    ENVIRONMENT_ENV_VAR,
    default_client,
    reset_default_client,
)
from shade.config import Environment
from shade.config import config as _config
from shade.errors import AuthenticationError


@pytest.fixture(autouse=True)
def _reset_global_config(monkeypatch):
    """Isolate every test from global config and environment leakage."""
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(ENVIRONMENT_ENV_VAR, raising=False)
    _config.reset()
    reset_default_client()
    yield
    _config.reset()
    reset_default_client()


class Payments(BaseResource):
    """Minimal resource standing in for the real ones, which do not exist yet."""

    def retrieve(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")


def _capture_requests(client: ShadeClient):
    """Patch a client's sync transport, returning the list of sent requests."""
    sent = []

    def fake_execute(req):
        sent.append(req)
        return 200, {}, b'{"id": "pay_1"}'

    return patch.object(client._http, "_execute", side_effect=fake_execute), sent


# ---------------------------------------------------------------------------
# Isolated instances
# ---------------------------------------------------------------------------

class TestIsolatedClient:
    def test_api_key_binds_to_the_instance(self):
        client = ShadeClient(api_key="sk_test_xxx")

        assert client.api_key == "sk_test_xxx"
        assert client._http.api_key == "sk_test_xxx"
        assert client._async_http.api_key == "sk_test_xxx"

    def test_accepts_the_same_parameters_as_global_config(self):
        client = ShadeClient(
            api_key="sk_test_xxx",
            environment="production",
            api_base="http://localhost:8000/",
            timeout=5.0,
            max_retries=1,
        )

        assert client.environment is Environment.PRODUCTION
        assert client.api_base == "http://localhost:8000"
        assert client.timeout == 5.0
        assert client.max_retries == 1

    def test_falls_back_to_global_settings(self):
        _config.api_key = "sk_live_global"
        _config.timeout = 12.0
        _config.max_retries = 2

        client = ShadeClient()

        assert client.api_key == "sk_live_global"
        assert client.timeout == 12.0
        assert client.max_retries == 2

    def test_instance_settings_beat_global_ones(self):
        _config.api_key = "sk_live_global"
        _config.timeout = 12.0

        client = ShadeClient(api_key="sk_test_instance", timeout=3.0)

        assert client.api_key == "sk_test_instance"
        assert client.timeout == 3.0
        assert _config.api_key == "sk_live_global"

    def test_instance_settings_survive_later_global_changes(self):
        client = ShadeClient(api_key="sk_test_instance", timeout=3.0)

        _config.api_key = "sk_live_global"
        _config.timeout = 99.0

        assert client.api_key == "sk_test_instance"
        assert client.timeout == 3.0

    def test_unset_settings_follow_later_global_changes(self):
        _config.api_key = "sk_live_first"
        client = ShadeClient()

        _config.api_key = "sk_live_second"
        _config.timeout = 99.0

        assert client.api_key == "sk_live_second"
        assert client.timeout == 99.0

    def test_repr_masks_the_api_key(self):
        client = ShadeClient(api_key="sk_test_secret_1234")

        assert "sk_test_secret_1234" not in repr(client)
        assert "1234" in repr(client)


# ---------------------------------------------------------------------------
# Coexisting clients
# ---------------------------------------------------------------------------

class TestCoexistingClients:
    def test_two_clients_keep_separate_credentials(self):
        acme = ShadeClient(api_key="sk_live_acme")
        globex = ShadeClient(api_key="sk_live_globex")

        assert acme.api_key == "sk_live_acme"
        assert globex.api_key == "sk_live_globex"
        assert acme._http is not globex._http

    def test_two_clients_keep_separate_settings(self):
        sandbox = ShadeClient(
            api_key="sk_test_a", environment="sandbox", timeout=5.0, max_retries=0
        )
        production = ShadeClient(
            api_key="sk_live_b", environment="production", timeout=20.0, max_retries=5
        )

        assert sandbox.environment is Environment.SANDBOX
        assert production.environment is Environment.PRODUCTION
        assert sandbox.api_base != production.api_base
        assert (sandbox.timeout, sandbox.max_retries) == (5.0, 0)
        assert (production.timeout, production.max_retries) == (20.0, 5)

    def test_requests_carry_each_clients_own_key(self):
        acme = ShadeClient(api_key="sk_live_acme")
        globex = ShadeClient(api_key="sk_live_globex")

        acme_patch, acme_sent = _capture_requests(acme)
        globex_patch, globex_sent = _capture_requests(globex)

        with acme_patch:
            acme._http.request("GET", "/payments/pay_1")
        with globex_patch:
            globex._http.request("GET", "/payments/pay_1")

        assert acme_sent[0].get_header("Authorization") == "Bearer sk_live_acme"
        assert globex_sent[0].get_header("Authorization") == "Bearer sk_live_globex"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

class TestResourceClientBinding:
    def test_resource_uses_its_clients_credentials_not_the_global_ones(self):
        _config.api_key = "sk_live_global"
        acme = ShadeClient(api_key="sk_live_acme")
        payments = Payments(client=acme)

        request_patch, sent = _capture_requests(acme)
        with request_patch:
            result = payments.retrieve("pay_1")

        assert result == {"id": "pay_1"}
        assert sent[0].get_header("Authorization") == "Bearer sk_live_acme"
        assert payments.client is acme

    def test_resource_falls_back_to_global_config_when_client_omitted(self):
        _config.api_key = "sk_live_global"
        payments = Payments()

        assert payments.client.api_key == "sk_live_global"

    def test_two_resources_on_different_clients_do_not_interfere(self):
        acme = ShadeClient(api_key="sk_live_acme")
        globex = ShadeClient(api_key="sk_live_globex")

        assert Payments(client=acme).client.api_key == "sk_live_acme"
        assert Payments(client=globex).client.api_key == "sk_live_globex"

    def test_resource_picks_up_a_global_key_set_after_construction(self):
        payments = Payments()
        _config.api_key = "sk_live_late"

        assert payments.client.api_key == "sk_live_late"

    def test_resource_without_client_or_global_key_raises(self):
        payments = Payments()

        with pytest.raises(AuthenticationError, match="No API key provided"):
            payments.retrieve("pay_1")

    def test_repr_distinguishes_global_from_explicit_clients(self):
        assert repr(Payments()) == "<Payments client=global>"
        assert "Payments client=<ShadeClient" in repr(
            Payments(client=ShadeClient(api_key="sk_test_x"))
        )


# ---------------------------------------------------------------------------
# default_client
# ---------------------------------------------------------------------------

class TestDefaultClient:
    def test_is_cached_between_calls(self):
        _config.api_key = "sk_live_global"

        assert default_client() is default_client()

    def test_follows_global_setting_changes(self):
        _config.api_key = "sk_live_first"
        client = default_client()

        _config.api_key = "sk_live_second"

        assert client.api_key == "sk_live_second"

    def test_requests_raise_without_a_global_key(self):
        with pytest.raises(AuthenticationError, match="No API key provided"):
            default_client().request("GET", "/payments")


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------

class TestFromEnv:
    def test_reads_api_key_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk_test_from_env")

        client = ShadeClient.from_env()

        assert client.api_key == "sk_test_from_env"

    def test_reads_environment_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk_test_from_env")
        monkeypatch.setenv(ENVIRONMENT_ENV_VAR, "production")

        client = ShadeClient.from_env()

        assert client.environment is Environment.PRODUCTION
        assert client.api_base == Environment.PRODUCTION.base_url

    def test_defaults_to_global_environment_when_var_absent(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk_test_from_env")
        _config.environment = Environment.SANDBOX

        client = ShadeClient.from_env()

        assert client.environment is Environment.SANDBOX

    def test_invalid_environment_value_raises(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk_test_from_env")
        monkeypatch.setenv(ENVIRONMENT_ENV_VAR, "staging")

        with pytest.raises(ValueError, match="Invalid environment"):
            ShadeClient.from_env()

    def test_keyword_overrides_beat_the_environment(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "sk_test_from_env")

        client = ShadeClient.from_env(api_key="sk_test_explicit", timeout=5.0)

        assert client.api_key == "sk_test_explicit"
        assert client.timeout == 5.0

    def test_falls_back_to_global_key_when_var_absent(self):
        _config.api_key = "sk_live_global"

        assert ShadeClient.from_env().api_key == "sk_live_global"

    def test_requests_raise_when_neither_env_var_nor_global_key_is_set(self):
        with pytest.raises(AuthenticationError, match="No API key provided"):
            ShadeClient.from_env().request("GET", "/payments")

    def test_empty_env_var_is_treated_as_absent(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VAR, "")

        assert ShadeClient.from_env().api_key is None


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------

class TestMissingApiKey:
    def test_raises_authentication_error_when_no_key_anywhere(self):
        with pytest.raises(AuthenticationError, match="No API key provided"):
            ShadeClient().request("GET", "/payments")

    def test_empty_api_key_raises_authentication_error(self):
        with pytest.raises(AuthenticationError, match="No API key provided"):
            ShadeClient(api_key="").request("GET", "/payments")

    def test_error_names_the_ways_to_supply_a_key(self):
        with pytest.raises(AuthenticationError) as excinfo:
            ShadeClient().request("GET", "/payments")

        message = str(excinfo.value)
        assert "api_key=" in message
        assert "shade.api_key" in message
        assert API_KEY_ENV_VAR in message

    def test_gateway_shares_the_same_requirement(self):
        with pytest.raises(AuthenticationError, match="No API key provided"):
            Gateway().process_payment(10.0, "USD")


# ---------------------------------------------------------------------------
# Module-level shade.api_key
# ---------------------------------------------------------------------------

class TestModuleLevelApiKey:
    def test_defaults_to_none(self):
        assert shade.api_key is None

    def test_assignment_is_readable(self):
        shade.api_key = "sk_live_module"
        assert shade.api_key == "sk_live_module"

    def test_assignment_updates_config(self):
        shade.api_key = "sk_live_module"
        assert _config.api_key == "sk_live_module"

    def test_used_by_clients_built_without_a_key(self):
        shade.api_key = "sk_live_module"
        assert ShadeClient().api_key == "sk_live_module"
