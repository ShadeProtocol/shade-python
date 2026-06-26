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
        assert gw._base_url == Environment.MAINNET.base_url


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
    def test_mainnet_passphrase_unchanged_when_api_base_set(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.MAINNET,
        )
        assert gw.environment.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE

    def test_testnet_passphrase_unchanged_when_api_base_set(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.TESTNET,
        )
        assert gw.environment.network_passphrase == Network.TESTNET_NETWORK_PASSPHRASE

    def test_api_base_overrides_url_not_passphrase(self):
        from stellar_sdk import Network
        gw = Gateway(
            api_key="test-key",
            api_base="http://localhost:8000",
            environment=Environment.MAINNET,
        )
        assert gw._base_url == "http://localhost:8000"
        assert gw.environment.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE


# ---------------------------------------------------------------------------
# URL resolution precedence
# ---------------------------------------------------------------------------

class TestUrlResolutionPrecedence:
    def test_environment_url_is_default(self):
        gw = Gateway(api_key="test-key", environment=Environment.MAINNET)
        assert gw._base_url == Environment.MAINNET.base_url

    def test_module_level_beats_environment(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key", environment=Environment.MAINNET)
        assert gw._base_url == "https://staging.shadeprotocol.io"

    def test_per_client_beats_module_level(self):
        shade.api_base = "https://staging.shadeprotocol.io"
        gw = Gateway(api_key="test-key", api_base="http://localhost:8000")
        assert gw._base_url == "http://localhost:8000"

    def test_testnet_environment_url_used_by_default(self):
        gw = Gateway(api_key="test-key", environment=Environment.TESTNET)
        assert gw._base_url == Environment.TESTNET.base_url


# ---------------------------------------------------------------------------
# Environment enum
# ---------------------------------------------------------------------------

class TestEnvironment:
    def test_mainnet_base_url(self):
        assert Environment.MAINNET.base_url == "https://api.shadeprotocol.io/v1"

    def test_testnet_base_url(self):
        assert Environment.TESTNET.base_url == "https://testnet.api.shadeprotocol.io/v1"

    def test_mainnet_network_passphrase(self):
        from stellar_sdk import Network
        assert Environment.MAINNET.network_passphrase == Network.PUBLIC_NETWORK_PASSPHRASE

    def test_testnet_network_passphrase(self):
        from stellar_sdk import Network
        assert Environment.TESTNET.network_passphrase == Network.TESTNET_NETWORK_PASSPHRASE


# ---------------------------------------------------------------------------
# ShadeClient alias
# ---------------------------------------------------------------------------

class TestShadeClientAlias:
    def test_shade_client_is_gateway(self):
        from shade import ShadeClient
        assert ShadeClient is Gateway

    def test_shade_client_accepts_api_base(self):
        from shade import ShadeClient
        client = ShadeClient(api_key="test-key", api_base="http://localhost:8000")
        assert client._base_url == "http://localhost:8000"
