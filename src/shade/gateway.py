from .config import Environment, get_environment_config, parse_environment


class Gateway:
    """
    Main entry point for the Shade Payment Gateway.
    """

    def __init__(self, environment: Environment | str = Environment.SANDBOX):
        self.environment = environment

    @property
    def environment(self) -> Environment:
        return self._environment

    @environment.setter
    def environment(self, value: Environment | str) -> None:
        self._environment = parse_environment(value)
        self._environment_config = get_environment_config(self._environment)

    @property
    def horizon_url(self) -> str:
        return self._environment_config.horizon_url

    @property
    def network_passphrase(self) -> str:
        return self._environment_config.network_passphrase

    @property
    def api_base_url(self) -> str:
        return self._environment_config.api_base_url

    def process_payment(self, amount: float, currency: str):
        """
        Process a payment (placeholder).
        """
        print(f"Processing payment of {amount} {currency}...")
        return True
