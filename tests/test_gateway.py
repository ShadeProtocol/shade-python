import pytest
from unittest.mock import patch
from shade import Environment, Gateway


def test_gateway_initialization():
    gateway = Gateway(api_key="test-key")
    assert gateway is not None


def test_process_payment():
    gateway = Gateway(api_key="test-key")
    mock_response = {"id": "pay_001", "status": "ok"}

    with patch.object(gateway._http, "request", return_value=mock_response) as mock_req:
        result = gateway.process_payment(100.0, "USD")

    assert result == mock_response
    mock_req.assert_called_once_with(
        "POST", "/payments", {"amount": 100.0, "currency": "USD"}
    )


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
    

def test_process_payment_async():
    import asyncio
    from unittest.mock import AsyncMock

    gateway = Gateway(api_key="test-key")
    mock_response = {"id": "pay_002", "status": "ok"}

    with patch.object(gateway._async_http, "request", new_callable=AsyncMock, return_value=mock_response) as mock_req:
        result = asyncio.run(gateway.process_payment_async(50.0, "EUR"))

    assert result == mock_response
    mock_req.assert_called_once_with(
        "POST", "/payments", {"amount": 50.0, "currency": "EUR"}
    )
