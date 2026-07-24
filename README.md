# Shade

A Python-based payment gateway system for Shade Protocol.

## Async requests

`Gateway` provides async resource methods backed by a shared
`httpx.AsyncClient`.  Close the gateway when it is no longer needed, either
explicitly or by using the async HTTP client as a context manager.

```python
from shade import Gateway

gateway = Gateway(api_key="sk_test_...")
try:
    payment = await gateway.process_payment_async(100.0, "USD")
finally:
    await gateway.aclose()
```

For direct transport use, `AsyncHTTPClient.request()` has the same arguments
and returns the same decoded-dictionary response shape as `SyncHTTPClient`:

```python
from shade import AsyncHTTPClient

async with AsyncHTTPClient("https://api.example.com", "sk_test_...") as client:
    payments = await client.request("GET", "/payments")
```
