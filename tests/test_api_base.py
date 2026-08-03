"""
Tests for api_base override support (issue #6).

Covers:
* shade.api_base module-level attribute reads and writes
* Gateway(api_base=...) per-client override
* Trailing slash normalisation
* Precedence: explicit api_base > module-level shade.api_base > environment URL
* Environment still controls Stellar network passphrase when api_base is set
"""
from __future__ import annotations

import pytest

import shade
from shade import Gateway
from shade import config as _config
from shade.config import Environment


@pytest.fixture(autouse=True)
def _reset_api_base():
    original = _config.api_base
    yield
    _config.api_base = original


# ---------------------------------------------------------------------------
# Module-level shade.api_base
# ---------------------------------------------------------------------------

class TestModuleLevelApiBase:
    def test_defaults_to_none(self):
        assert shade.api_base is None

    def test_assignment_is_readable(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        assert shade.api_base == "https://staging.shadeprotocol.io"

    def test_assignment_updates_config(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        assert _config.api_base == "https://staging.shadeprotocol.io"

    def test_used_when_no_per_client_override(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key")
        assert gw._base_url == "https://staging.shadeprotocol.io"

    def test_trailing_slash_normalised(self):
        shade.api_base = "https://staging.shadeprotocol.io/"
        gw = Gateway(api_key="test-key")
        assert gw._base_url == "https://staging.shadeprotocol.io"

    def test_reset_to_none_restores_environment_url(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        shade.api_base = None
        gw = Gateway(api_key="test-key")
        assert gw._base_url == Environment.SANDBOX.base_url  # default is SANDBOX now


# ---------------------------------------------------------------------------
# Module-level shade.environment
# ---------------------------------------------------------------------------

class TestModuleLevelEnvironment:
    @pytest.fixture(autouse=True)
    def _reset_environment(self):
        original = shade.environment
        yield
        shade.environment = original

    def test_defaults_to_sandbox(self):
        assert shade.environment == Environment.SANDBOX
        assert _config.environment == Environment.SANDBOX

    def test_assignment_updates_config(self):
        shade.environment = "production"
        assert shade.environment == Environment.PRODUCTION
        assert _config.environment == Environment.PRODUCTION

    def test_invalid_assignment_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid environment. Valid options are: 'sandbox', 'production'"):
            shade.environment = "invalid"

    def test_used_by_gateway_by_default(self):
        shade.environment = "production"
        gw = Gateway(api_key="test-key")
        assert gw.environment == Environment.PRODUCTION
        assert gw._base_url == Environment.PRODUCTION.base_url

    def test_per_client_override(self):
        shade.environment = "production"
        gw = Gateway(api_key="test-key", environment="sandbox")
        assert gw.environment == Environment.SANDBOX
        assert gw._base_url == Environment.SANDBOX.base_url

# ---------------------------------------------------------------------------
# Per-client api_base
# ---------------------------------------------------------------------------

class TestPerClientApiBase:
    def test_overrides_environment_url(self):
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000")
        assert gw._base_url == "http://localhost:8000"

    def test_trailing_slash_normalised(self):
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000/")
        assert gw._base_url == "http://localhost:8000"

    def test_takes_precedence_over_module_level(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000")
        assert gw._base_url == "http://localhost:8000"

    def test_http_client_uses_resolved_base_url(self):
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000")
        assert gw._http.base_url == "http://localhost:8000"
        assert gw._async_http.base_url == "http://localhost:8000"


# ---------------------------------------------------------------------------
# Environment passphrase independence
# ---------------------------------------------------------------------------

class TestEnvironmentPassphrase:
    def test_production_passphrase_unchanged_when_api_base_set(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.PRODUCTION,
        )
        assert gw.environment.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE

    def test_sandbox_passphrase_unchanged_when_api_base_set(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.SANDBOX,
        )
        assert gw.environment.network_passphrase == Network.TESTNET_NETWORK_PASSPHRASE

    def test_api_base_overrides_url_not_passphrase(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.PRODUCTION,
        )
        assert gw._base_url == "http://localhost:8000"
        assert gw.environment.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE


# ---------------------------------------------------------------------------
# URL resolution precedence
# ---------------------------------------------------------------------------

class TestUrlResolutionPrecedence:
    def test_environment_url_is_default(self):
        gw = Gateway(api_key="test-key", environment=Environment.PRODUCTION)
        assert gw._base_url == Environment.PRODUCTION.base_url

    def test_module_level_beats_environment(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key", environment=Environment.PRODUCTION)
        assert gw._base_url == "https://staging.shadeprotocol.io"

    def test_per_client_beats_module_level(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000")
        assert gw._base_url == "http://localhost:8000"

    def test_sandbox_environment_url_used_by_default(self):
        gw = Gateway(api_key="test-key", environment=Environment.SANDBOX)
        assert gw._base_url == Environment.SANDBOX.base_url


# ---------------------------------------------------------------------------
# Environment enum
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_production_base_url(self):
        assert Environment.PRODUCTION.base_url == "https://api.shadeprotocol.io/v1"

    def test_sandbox_base_url(self):
        assert Environment.SANDBOX.base_url == "https://testnet.api.shadeprotocol.io/v1"

    def test_production_network_passphrase(self):
        from stellar_sdk import Network
        assert Environment.PRODUCTION.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE

    def test_sandbox_network_passphrase(self):
        from stellar_sdk import Network
        assert Environment.SANDBOX.network_passphrase == Network.TESTNET_NETWORK_PASSPHRASE

    def test_horizon_urls(self):
        assert Environment.PRODUCTION.horizon_url == "https://horizon.stellar.org"
        assert Environment.SANDBOX.horizon_url == "https://horizon-testnet.stellar.org"


# ---------------------------------------------------------------------------
# ShadeClient
# ---------------------------------------------------------------------------

class TestShadeClient:
    def test_gateway_is_a_shade_client(self):
        from shade import ShadeClient
        assert issubclass(Gateway, ShadeClient)

    def test_shade_client_accepts_api_base(self):
        from shade import ShadeClient
        client = ShadeClient(api_key="test-key", api_base="http://localhost:8000")
        assert client._base_url == "http://localhost:8000"
