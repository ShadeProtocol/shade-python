"""
Tests for the internal ``_AsyncHTTPClient`` wrapper.

Covers:
* URL construction for GET, POST, PATCH, DELETE in async path
* Default headers: User-Agent, Accept, Content-Type, Authorization
* Query parameters and JSON request bodies
* Response parsing and error handling
* Lifecycle management (`aclose()`, `async with`)
* Integration with `ShadeClient` and resource classes
* Idempotency-safe retry logic using `asyncio.sleep`
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import shade
from shade import BaseResource, Environment, ShadeClient
from shade.client import API_KEY_ENV_VAR, ENVIRONMENT_ENV_VAR, reset_default_client
from shade.config import config as _config
from shade.errors import (
    AuthenticationError,
    HTTPError,
    InvalidRequestError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ShadeError,
)
from shade.http_client import _AsyncHTTPClient, _build_full_url


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv(ENVIRONMENT_ENV_VAR, raising=False)
    _config.reset()
    reset_default_client()
    yield
    _config.reset()
    reset_default_client()


def _stub_async_httpx_client(
    http_wrapper: _AsyncHTTPClient,
    responses: List[httpx.Response],
) -> List[dict]:
    """Replace the underlying ``httpx.AsyncClient.request`` and capture calls."""
    captured: List[dict] = []
    response_iter = iter(responses)

    async def fake_request(*args, **kwargs):
        captured.append({"args": args, **kwargs})
        return next(response_iter)

    http_wrapper._client.request = fake_request  # type: ignore[method-assign]
    return captured


def _resp(
    status: int = 200,
    *,
    json_body: Any = None,
    text: Optional[str] = None,
    headers: Optional[dict] = None,
    request: Optional[httpx.Request] = None,
) -> httpx.Response:
    kwargs: dict[str, Any] = {"status_code": status, "headers": headers or {}}
    if json_body is not None:
        kwargs["json"] = json_body
    elif text is not None:
        kwargs["text"] = text
    if request is not None:
        kwargs["request"] = request
    return httpx.Response(**kwargs)


def _make_async_client(**overrides) -> _AsyncHTTPClient:
    kwargs = {
        "api_key": "sk_test_xxx",
        "api_base": "https://api.example.com",
        "timeout": 5.0,
        **overrides,
    }
    return _AsyncHTTPClient(**kwargs)


# ---------------------------------------------------------------------------
# URL Construction
# ---------------------------------------------------------------------------


class TestAsyncUrlConstruction:
    @pytest.mark.anyio
    async def test_get_builds_correct_url(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        res = await client.get("/resources")

        assert captured[0]["args"] == ("GET", "https://api.example.com/resources")
        assert res == {}

    @pytest.mark.anyio
    async def test_post_builds_correct_url(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.post("/resources", json={"name": "x"})

        assert captured[0]["args"] == ("POST", "https://api.example.com/resources")

    @pytest.mark.anyio
    async def test_patch_builds_correct_url(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.patch("/resources/1", json={"name": "y"})

        assert captured[0]["args"] == ("PATCH", "https://api.example.com/resources/1")

    @pytest.mark.anyio
    async def test_delete_builds_correct_url(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.delete("/resources/1")

        assert captured[0]["args"] == ("DELETE", "https://api.example.com/resources/1")

    @pytest.mark.anyio
    async def test_handles_trailing_slash_on_api_base(self):
        client = _make_async_client(api_base="https://api.example.com/")
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("users")

        assert captured[0]["args"] == ("GET", "https://api.example.com/users")


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestAsyncHeaders:
    @pytest.mark.anyio
    async def test_authorization_bearer_api_key(self):
        client = _make_async_client(api_key="sk_live_secret")
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x")

        assert captured[0]["headers"]["Authorization"] == "Bearer sk_live_secret"

    @pytest.mark.anyio
    async def test_accept_application_json(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x")

        assert captured[0]["headers"]["Accept"] == "application/json"

    @pytest.mark.anyio
    async def test_user_agent_includes_version(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x")

        expected = f"shade-python/{shade.__version__}"
        assert captured[0]["headers"]["User-Agent"] == expected

    @pytest.mark.anyio
    async def test_content_type_json_on_post_with_body(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.post("/x", json={"k": "v"})

        assert captured[0]["headers"]["Content-Type"] == "application/json"

    @pytest.mark.anyio
    async def test_merge_headers_case_insensitive_replacement(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x", headers={"authorization": "Bearer custom_token"})

        assert "Authorization" not in captured[0]["headers"]
        assert captured[0]["headers"]["authorization"] == "Bearer custom_token"


class TestAsyncCleartextHttp:
    @pytest.mark.anyio
    async def test_rejects_non_local_cleartext_http(self):
        client = _make_async_client(api_base="http://api.shadeprotocol.io")
        with pytest.raises(ValueError, match="HTTPS is required"):
            await client.get("/x")

    @pytest.mark.anyio
    async def test_allows_local_cleartext_http_when_authorization_withheld(self):
        client = _make_async_client(api_base="http://localhost:8000")
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x", headers={"Authorization": None})

        assert "Authorization" not in captured[0]["headers"]
        assert len(captured) == 1


# ---------------------------------------------------------------------------
# Request parameters
# ---------------------------------------------------------------------------


class TestAsyncRequestParameters:
    @pytest.mark.anyio
    async def test_query_params_are_forwarded(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x", params={"limit": 10, "status": "paid"})

        assert captured[0]["params"] == {"limit": 10, "status": "paid"}

    @pytest.mark.anyio
    async def test_json_body_is_forwarded(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])
        body = {"amount": 10.0, "currency": "USD"}

        await client.post("/x", json=body)

        assert captured[0]["json"] == body

    @pytest.mark.anyio
    async def test_timeout_forwarded(self):
        client = _make_async_client(timeout=7.5)
        captured = _stub_async_httpx_client(client, [_resp(200, json_body={})])

        await client.get("/x")

        assert captured[0]["timeout"] == 7.5


# ---------------------------------------------------------------------------
# Response Parsing
# ---------------------------------------------------------------------------


class TestAsyncResponseParsing:
    @pytest.mark.anyio
    async def test_200_returns_parsed_dict(self):
        client = _make_async_client()
        _stub_async_httpx_client(
            client, [_resp(200, json_body={"id": "res_1", "status": "ok"})]
        )

        result = await client.get("/res/res_1")

        assert result == {"id": "res_1", "status": "ok"}

    @pytest.mark.anyio
    async def test_empty_body_returns_empty_dict(self):
        client = _make_async_client()
        _stub_async_httpx_client(client, [_resp(204, text="")])

        result = await client.delete("/res/1")

        assert result == {}

    @pytest.mark.anyio
    async def test_non_dict_2xx_raises_shade_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(client, [_resp(200, json_body=[1, 2, 3])])

        with pytest.raises(ShadeError, match="Invalid response from API"):
            await client.get("/x")

    @pytest.mark.anyio
    async def test_non_json_2xx_raises_shade_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(client, [_resp(200, text="not json")])

        with pytest.raises(ShadeError, match="Invalid response from API"):
            await client.get("/x")


# ---------------------------------------------------------------------------
# Error Mapping
# ---------------------------------------------------------------------------


class TestAsyncErrorResponses:
    @pytest.mark.anyio
    async def test_401_maps_to_authentication_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(
            client,
            [_resp(401, json_body={"error": {"message": "bad token"}})],
        )

        with pytest.raises(AuthenticationError) as exc:
            await client.get("/x")
        assert exc.value.status_code == 401

    @pytest.mark.anyio
    async def test_400_maps_to_invalid_request_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(
            client,
            [_resp(400, json_body={"error": {"message": "bad input"}})],
        )

        with pytest.raises(InvalidRequestError) as exc:
            await client.post("/x", json={})
        assert exc.value.status_code == 400

    @pytest.mark.anyio
    async def test_404_maps_to_not_found_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(
            client,
            [_resp(404, json_body={"error": {"message": "gone"}})],
        )

        with pytest.raises(NotFoundError) as exc:
            await client.get("/missing")
        assert exc.value.status_code == 404

    @pytest.mark.anyio
    async def test_429_maps_to_rate_limit_error(self):
        client = _make_async_client(max_retries=0)
        _stub_async_httpx_client(
            client,
            [
                _resp(
                    429,
                    json_body={"error": {"message": "slow down"}},
                    headers={"Retry-After": "5"},
                )
            ],
        )

        with pytest.raises(RateLimitError) as exc:
            await client.get("/x")
        assert exc.value.status_code == 429
        assert exc.value.retry_after == 5

    @pytest.mark.anyio
    async def test_5xx_maps_to_network_error_when_retries_exhausted(self):
        client = _make_async_client(max_retries=0)
        _stub_async_httpx_client(
            client,
            [_resp(502, json_body={"error": {"message": "upstream"}})],
        )

        with pytest.raises(NetworkError) as exc:
            await client.get("/x")
        assert exc.value.status_code == 502

    @pytest.mark.anyio
    async def test_other_non_2xx_maps_to_http_error(self):
        client = _make_async_client()
        _stub_async_httpx_client(
            client,
            [_resp(418, json_body={"error": {"message": "teapot"}})],
        )

        with pytest.raises(HTTPError) as exc:
            await client.get("/x")
        assert exc.value.status_code == 418


# ---------------------------------------------------------------------------
# Client Lifecycle & Async Context Manager
# ---------------------------------------------------------------------------


class TestAsyncClientLifecycle:
    @pytest.mark.anyio
    async def test_aclose_closes_underlying_async_client(self):
        client = _make_async_client()
        underlying = client._client

        with patch.object(underlying, "aclose", wraps=underlying.aclose) as mock_aclose:
            await client.aclose()
            mock_aclose.assert_awaited_once()

    @pytest.mark.anyio
    async def test_async_context_manager_closes_wrapper(self):
        client = _make_async_client()
        closed = []
        original_aclose = client.aclose

        async def fake_aclose():
            closed.append(True)
            await original_aclose()

        client.aclose = fake_aclose  # type: ignore[method-assign]

        async with client:
            pass

        assert len(closed) == 1

    @pytest.mark.anyio
    async def test_shade_client_lazy_async_http_initialization(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        assert sc._async_http_instance is None

        # Access property constructs instance lazily
        wrapper = sc._async_http
        assert sc._async_http_instance is not None
        assert isinstance(wrapper, _AsyncHTTPClient)
        assert wrapper.api_key == "sk_test_xxx"

    @pytest.mark.anyio
    async def test_shade_client_close_uninitialized_async_client(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        assert sc._async_http_instance is None

        # Closing sync client or async client when uninitialized does not instantiate it
        sc.close()
        assert sc._async_http_instance is None

        await sc.aclose()
        assert sc._async_http_instance is None

    @pytest.mark.anyio
    async def test_shade_client_guarded_setters_when_uninitialized(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        assert sc._async_http_instance is None

        sc.api_key = "sk_test_updated"
        sc.environment = "production"
        assert sc._async_http_instance is None

        # First access constructs async client with updated settings
        async_http = sc._async_http
        assert async_http.api_key == "sk_test_updated"
        assert async_http.environment == Environment.PRODUCTION

    @pytest.mark.anyio
    async def test_shade_client_aclose_closes_async_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        wrapper = sc._async_http

        with patch.object(wrapper, "aclose", wraps=wrapper.aclose) as mock_aclose:
            await sc.aclose()
            mock_aclose.assert_awaited_once()

    @pytest.mark.anyio
    async def test_shade_client_async_context_manager(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        closed = []

        async def fake_aclose():
            closed.append(True)

        sc.aclose = fake_aclose  # type: ignore[method-assign]

        async with sc:
            pass

        assert len(closed) == 1


# ---------------------------------------------------------------------------
# Resource Async Integration
# ---------------------------------------------------------------------------


class _TestResource(BaseResource):
    async def list_async(self, limit: int = 5):
        return await self._request_async("GET", f"/things?limit={limit}")

    async def create_async(self, data: dict):
        return await self._request_async("POST", "/things", data)


class TestAsyncResourceIntegration:
    @pytest.mark.anyio
    async def test_resource_get_async_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_async_httpx_client(sc._async_http, [_resp(200, json_body={"data": []})])
        res = _TestResource(client=sc)

        result = await res.list_async(limit=3)

        assert result == {"data": []}
        assert captured[0]["args"][0] == "GET"
        assert "/things?limit=3" in captured[0]["args"][1]

    @pytest.mark.anyio
    async def test_resource_post_async_routes_through_wrapper(self):
        sc = ShadeClient(api_key="sk_test_xxx")
        captured = _stub_async_httpx_client(sc._async_http, [_resp(201, json_body={"id": "t1"})])
        res = _TestResource(client=sc)

        result = await res.create_async({"name": "widget"})

        assert result == {"id": "t1"}
        assert captured[0]["args"][0] == "POST"
        assert captured[0]["json"] == {"name": "widget"}


# ---------------------------------------------------------------------------
# Idempotency-Safe Retry (Async)
# ---------------------------------------------------------------------------


class TestAsyncIdempotencySafeRetry:
    @pytest.mark.anyio
    async def test_get_5xx_is_retried_async(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"ok": True}),
            ],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.get("/x")

        assert result == {"ok": True}
        assert len(captured) == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.anyio
    async def test_post_5xx_is_not_retried_async(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"should": "never-reach-this"}),
            ],
        )

        with pytest.raises(NetworkError):
            await client.post("/payments", json={"amount": 10})

        assert len(captured) == 1

    @pytest.mark.anyio
    async def test_post_5xx_is_retried_when_idempotency_key_present(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(
            client,
            [
                _resp(502, json_body={"error": {"message": "bad gateway"}}),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.post(
                "/payments",
                json={"amount": 10},
                headers={"Idempotency-Key": "unique-key-abc"},
            )

        assert result == {"id": "p1"}
        assert len(captured) == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.anyio
    async def test_post_429_is_always_retried_async(self):
        client = _make_async_client()
        captured = _stub_async_httpx_client(
            client,
            [
                _resp(
                    429,
                    json_body={"error": {"message": "slow"}},
                    headers={"Retry-After": "1"},
                ),
                _resp(200, json_body={"id": "p1"}),
            ],
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.post("/payments", json={"amount": 10})

        assert result == {"id": "p1"}
        assert len(captured) == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.anyio
    async def test_get_transport_error_is_retried_async(self):
        client = _make_async_client()
        captured: List[dict] = []

        async def fake_request(*args, **kwargs):
            captured.append({"args": args, **kwargs})
            if len(captured) == 1:
                raise httpx.TimeoutException("timed out")
            return httpx.Response(status_code=200, json={"ok": True})

        client._client.request = fake_request  # type: ignore[method-assign]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.get("/items")

        assert result == {"ok": True}
        assert len(captured) == 2
        mock_sleep.assert_awaited_once()
