# Shade

A Python-based payment gateway system for Shade Protocol.

## Environments

Shade defaults to the sandbox environment so local development uses Stellar
testnet and the staging Shade backend.

```python
from shade import Gateway

shade = Gateway()
shade.environment = "production"

print(shade.horizon_url)
print(shade.network_passphrase)
print(shade.api_base_url)
```

Supported values are `"sandbox"` and `"production"`. Invalid values raise a
`ValueError` that lists the accepted options.

