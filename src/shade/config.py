from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Environment(str, Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


@dataclass(frozen=True)
class EnvironmentConfig:
    horizon_url: str
    network_passphrase: str
    api_base_url: str


_ENVIRONMENT_CONFIGS = {
    Environment.SANDBOX: EnvironmentConfig(
        horizon_url="https://horizon-testnet.stellar.org",
        network_passphrase="Test SDF Network ; September 2015",
        api_base_url="https://api.sandbox.shadeprotocol.io",
    ),
    Environment.PRODUCTION: EnvironmentConfig(
        horizon_url="https://horizon.stellar.org",
        network_passphrase="Public Global Stellar Network ; September 2015",
        api_base_url="https://api.shadeprotocol.io",
    ),
}


def parse_environment(value: Environment | str) -> Environment:
    if isinstance(value, Environment):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        for environment in Environment:
            if environment.value == normalized:
                return environment

    valid_options = ", ".join(environment.value for environment in Environment)
    raise ValueError(
        f"Invalid Shade environment {value!r}. Valid options: {valid_options}"
    )


def get_environment_config(value: Environment | str) -> EnvironmentConfig:
    return _ENVIRONMENT_CONFIGS[parse_environment(value)]
