from .config import Environment, EnvironmentConfig, parse_environment
import sys
from types import ModuleType
from typing import Optional
from .gateway import Gateway
from .http import AsyncHTTPClient, SyncHTTPClient
from .errors import (
    AuthenticationError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    HTTPError,
    RateLimitError,
    ShadeError,
)

__version__ = "0.1.0"

# ShadeClient is an alias for Gateway.
ShadeClient = Gateway

__all__ = [
    "AsyncHTTPClient",
    "AuthenticationError",
    "Environment",
    "EnvironmentConfig",
    "Gateway",
    "HTTPError",
    "InvalidRequestError",
    "NetworkError",
    "NotFoundError",
    "RateLimitError",
    "ShadeClient",
    "ShadeError",
    "SyncHTTPClient",
    "api_base",
    "parse_environment",
]


class _ShadeModule(ModuleType):
    """Module subclass that exposes api_base as a settable attribute backed by config."""

    @property
    def api_base(self) -> Optional[str]:
        from . import config as _config
        return _config.api_base

    @api_base.setter
    def api_base(self, value: Optional[str]) -> None:
        from . import config as _config
        _config.api_base = value


sys.modules[__name__].__class__ = _ShadeModule
