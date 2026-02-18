import pytest
from shade import Gateway

def test_gateway_initialization():
    gateway = Gateway()
    assert gateway is not None

def test_process_payment():
    gateway = Gateway()
    result = gateway.process_payment(100.0, "USD")
    assert result is True
