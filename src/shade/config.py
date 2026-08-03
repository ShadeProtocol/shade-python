from __future__ import annotations

from enum import Enum

import threading
from typing import Any, NamedTuple, Optional

from stellar_sdk import Network

from .errors import AuthenticationError


class ResolvedConfig(NamedTuple):
    api_key: str
    environment: Environment
    api_base: Optional[str]
    timeout: float
    max_retries: int
    base_url: str


class Config:
    """Thread-safe global SDK configuration.

    Note:
        Configuration assignments made on the main thread (e.g. ``shade.api_key = "..."``)
        update both process-wide defaults and thread-local state. Assignments made outside
        the main thread update ONLY thread-local state for the calling thread and do not
        alter process-wide defaults for other threads. Global configuration setup should be
        performed from the main thread during application startup.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self._generation: int = 0
        self._global_api_key: Optional[str] = None
        self._global_api_base: Optional[str] = None
        self._global_environment: Environment = Environment.SANDBOX
        self._global_timeout: float = DEFAULT_TIMEOUT
        self._global_max_retries: int = DEFAULT_MAX_RETRIES
        self._global_debug: bool = False

    def reset(self) -> None:
        """Reset configuration to defaults (useful for test teardowns)."""
        with self._lock:
            self._generation += 1
            self._global_api_key = None
            self._global_api_base = None
            self._global_environment = Environment.SANDBOX
            self._global_timeout = DEFAULT_TIMEOUT
            self._global_max_retries = DEFAULT_MAX_RETRIES
            self._global_debug = False
        self._local.__dict__.clear()

    def _get_local(self, attr_name: str) -> tuple[bool, Any]:
        with self._lock:
            current_gen = self._generation
        if getattr(self._local, "generation", None) == current_gen:
            if attr_name in self._local.__dict__:
                return True, getattr(self._local, attr_name)
        return False, None

    def _set_local(self, attr_name: str, value: Any) -> None:
        with self._lock:
            current_gen = self._generation
        if getattr(self._local, "generation", None) != current_gen:
            self._local.__dict__.clear()
            self._local.generation = current_gen
        setattr(self._local, attr_name, value)

    @property
    def api_key(self) -> Optional[str]:
        has_local, val = self._get_local("api_key")
        if has_local:
            return val
        with self._lock:
            return self._global_api_key

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        """Set the API key. Updates process-wide default if called from main thread."""
        self._set_local("api_key", value)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_api_key = value

    @property
    def api_base(self) -> Optional[str]:
        has_local, val = self._get_local("api_base")
        if has_local:
            return val
        with self._lock:
            return self._global_api_base

    @api_base.setter
    def api_base(self, value: Optional[str]) -> None:
        """Set the API base URL override. Updates process-wide default if called from main thread."""
        self._set_local("api_base", value)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_api_base = value

    @property
    def environment(self) -> Environment:
        has_local, val = self._get_local("environment")
        if has_local:
            return val
        with self._lock:
            return self._global_environment

    @environment.setter
    def environment(self, value: str | Environment) -> None:
        """Set the active environment. Updates process-wide default if called from main thread."""
        parsed = self.parse_environment(value)
        self._set_local("environment", parsed)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_environment = parsed

    @property
    def timeout(self) -> float:
        has_local, val = self._get_local("timeout")
        if has_local:
            return val
        with self._lock:
            return self._global_timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        """Set the socket timeout. Updates process-wide default if called from main thread."""
        self._set_local("timeout", value)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_timeout = value

    @property
    def max_retries(self) -> int:
        has_local, val = self._get_local("max_retries")
        if has_local:
            return val
        with self._lock:
            return self._global_max_retries

    @max_retries.setter
    def max_retries(self, value: int) -> None:
        """Set the max retries limit. Updates process-wide default if called from main thread."""
        self._set_local("max_retries", value)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_max_retries = value


    @property
    def debug(self) -> bool:
        has_local, val = self._get_local("debug")
        if has_local:
            return val
        with self._lock:
            return self._global_debug

    @debug.setter
    def debug(self, value: bool) -> None:
        self._set_local("debug", value)
        if threading.current_thread() is threading.main_thread():
            with self._lock:
                self._global_debug = value


    def parse_environment(self, value: str | Environment) -> Environment:
        if isinstance(value, Environment):
            return value
        if isinstance(value, str):
            try:
                return Environment(value.lower())
            except ValueError:
                pass
        raise ValueError("Invalid environment. Valid options are: 'sandbox', 'production'")


# Default HTTP client settings. Override via ``shade.timeout`` / ``shade.max_retries``
# or per-client constructor arguments on ``ShadeClient`` / ``Gateway``.
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_MAX_RETRIES: int = 3
MAX_RETRIES_LIMIT: int = 10


def validate_client_settings(timeout: float, max_retries: int) -> None:
    """Raise ValueError for out-of-range timeout or retry settings."""
    if timeout <= 0:
        raise ValueError(f"timeout must be greater than 0, got {timeout!r}")
    if max_retries < 0 or max_retries > MAX_RETRIES_LIMIT:
        raise ValueError(
            f"max_retries must be between 0 and {MAX_RETRIES_LIMIT}, got {max_retries!r}"
        )


class Environment(str, Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"

    @property
    def base_url(self) -> str:
        _urls: dict[str, str] = {
            "sandbox": "https://testnet.api.shadeprotocol.io/v1",
            "production": "https://api.shadeprotocol.io/v1",
        }
        return _urls[self.value]

    @property
    def network_passphrase(self) -> str:
        _passphrases: dict[str, str] = {
            "sandbox": Network.TESTNET_NETWORK_PASSPHRASE,
            "production": Network.PUBLIC_NETWORK_PASSPHRASE,
        }
        return _passphrases[self.value]

    @property
    def horizon_url(self) -> str:
        _horizons: dict[str, str] = {
            "sandbox": "https://horizon-testnet.stellar.org",
            "production": "https://horizon.stellar.org",
        }
        return _horizons[self.value]


config = Config()


def get_config(
    api_key: Optional[str] = None,
    environment: Optional[Environment | str] = None,
    api_base: Optional[str] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> ResolvedConfig:
    """Merge instance-level overrides with global defaults.

    Parameters
    ----------
    api_key : str, optional
        Instance API key. If absent/None, uses ``shade.api_key``.
    environment : str | Environment, optional
        Instance environment. If absent/None, uses ``shade.environment``.
    api_base : str, optional
        Instance API base URL override. If absent/None, uses ``shade.api_base``.
    timeout : float, optional
        Instance socket timeout. If absent/None, uses ``shade.timeout``.
    max_retries : int, optional
        Instance retry limit. If absent/None, uses ``shade.max_retries``.

    Returns
    -------
    ResolvedConfig
        A named tuple with resolved configuration values.

    Raises
    ------
    AuthenticationError
        If no valid API key is set globally or at instance level.
    ValueError
        If timeout or max_retries are invalid.
    """
    resolved_api_key = api_key if api_key is not None else config.api_key
    if not resolved_api_key:
        raise AuthenticationError(
            "No API key provided. Pass api_key= to ShadeClient, set "
            "shade.api_key, or set the SHADE_API_KEY environment variable."
        )

    resolved_env = (
        config.parse_environment(environment)
        if environment is not None
        else config.environment
    )

    resolved_api_base = api_base if api_base is not None else config.api_base
    resolved_timeout = timeout if timeout is not None else config.timeout
    resolved_max_retries = (
        max_retries if max_retries is not None else config.max_retries
    )

    validate_client_settings(resolved_timeout, resolved_max_retries)

    base_url = (resolved_api_base or resolved_env.base_url).rstrip("/")

    return ResolvedConfig(
        api_key=resolved_api_key,
        environment=resolved_env,
        api_base=resolved_api_base,
        timeout=resolved_timeout,
        max_retries=resolved_max_retries,
        base_url=base_url,
    )
