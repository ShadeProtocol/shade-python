from shade import Environment, Gateway


def test_gateway_initialization():
    gateway = Gateway()
    assert gateway is not None


def test_process_payment():
    gateway = Gateway()
    result = gateway.process_payment(100.0, "USD")
    assert result is True


def test_gateway_defaults_to_sandbox_environment():
    gateway = Gateway()

    assert gateway.environment is Environment.SANDBOX
    assert gateway.horizon_url == "https://horizon-testnet.stellar.org"
    assert gateway.network_passphrase == "Test SDF Network ; September 2015"
    assert gateway.api_base_url == "https://api.sandbox.shadeprotocol.io"


def test_gateway_environment_string_updates_stellar_and_api_config():
    gateway = Gateway()

    gateway.environment = "production"

    assert gateway.environment is Environment.PRODUCTION
    assert gateway.horizon_url == "https://horizon.stellar.org"
    assert gateway.network_passphrase == "Public Global Stellar Network ; September 2015"
    assert gateway.api_base_url == "https://api.shadeprotocol.io"
