"""Tests for the Humanitix async HTTP client."""

from __future__ import annotations

import os
from unittest import mock

import httpx
import pytest
import respx
from respx import Route

from humanitix_mcp.client import (
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    ConfigurationError,
    DEFAULT_BASE_URL,
    HumanitixClient,
    NotFoundError,
    ServerError,
    TransportError,
    ValidationError,
)

FAKE_API_KEY = "test-api-key-redacted"


def _client(env_key: str | None = None) -> HumanitixClient:
    return HumanitixClient(api_key=env_key or FAKE_API_KEY)


@respx.mock
async def test_sends_api_key_header() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        return_value=httpx.Response(200, json={"events": [], "total": 0}),
    )
    client = _client()
    await client.get("/v1/events")
    assert route.called
    assert route.calls.last.request.headers["x-api-key"] == FAKE_API_KEY
    await client.close()


async def test_missing_api_key_raises_configuration_error() -> None:
    with mock.patch.dict(os.environ, clear=True):
        with pytest.raises(ConfigurationError, match="HUMANITIX_API_KEY is required"):
            HumanitixClient()


async def test_missing_api_key_from_environment_only() -> None:
    """A provided key should satisfy the configuration check."""
    with mock.patch.dict(os.environ, {"HUMANITIX_API_KEY": ""}, clear=True):
        client = HumanitixClient(api_key=FAKE_API_KEY)
        assert client.base_url == DEFAULT_BASE_URL
        await client.close()


async def test_base_url_from_environment() -> None:
    custom = "https://example.com"
    with mock.patch.dict(os.environ, {"HUMANITIX_BASE_URL": custom}, clear=True):
        client = HumanitixClient(api_key=FAKE_API_KEY)
        assert client.base_url == custom
        await client.close()


async def test_base_url_trailing_slash_removed() -> None:
    client = HumanitixClient(api_key=FAKE_API_KEY, base_url="https://example.com/")
    assert client.base_url == "https://example.com"
    await client.close()


@respx.mock
async def test_retry_on_500_then_success() -> None:
    route: Route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(500, text="boom"),
            httpx.Response(200, json={"events": [], "total": 0}),
        ],
    )
    client = _client()
    result = await client.get("/v1/events")
    assert result == {"events": [], "total": 0}
    assert route.call_count == 3
    await client.close()


@respx.mock
async def test_retry_on_500_exhausted_raises_server_error() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        return_value=httpx.Response(500, text="boom"),
    )
    client = _client()
    with pytest.raises(ServerError) as exc_info:
        await client.get("/v1/events")
    assert exc_info.value.status_code == 500
    assert route.call_count == 3
    await client.close()


@respx.mock
async def test_no_retry_on_401() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"}),
    )
    client = _client()
    with pytest.raises(AuthenticationError) as exc_info:
        await client.get("/v1/events")
    assert exc_info.value.status_code == 401
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_no_retry_on_404() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events/missing").mock(
        return_value=httpx.Response(404, json={"error": "Not found"}),
    )
    client = _client()
    with pytest.raises(NotFoundError):
        await client.get("/v1/events/missing")
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_error_mapping_400_403_422() -> None:
    cases = [
        (400, BadRequestError),
        (403, AuthorizationError),
        (422, ValidationError),
    ]
    for status, exc_type in cases:
        with respx.mock:
            route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
                return_value=httpx.Response(status, json={"detail": "error"}),
            )
            client = _client()
            with pytest.raises(exc_type) as exc_info:
                await client.get("/v1/events")
            assert exc_info.value.status_code == status
            assert route.call_count == 1
            await client.close()


@respx.mock
async def test_timeout_retried_as_transport_error() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        side_effect=[httpx.ConnectTimeout("timed out"), httpx.Response(200, json={})],
    )
    client = _client()
    result = await client.get("/v1/events")
    assert result == {}
    assert route.call_count == 2
    await client.close()


@respx.mock
async def test_network_error_retried_then_raises() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        side_effect=httpx.ConnectError("no route"),
    )
    client = _client()
    with pytest.raises(TransportError) as exc_info:
        await client.get("/v1/events")
    assert route.call_count == 3
    assert "Network error" in str(exc_info.value)
    await client.close()


@respx.mock
async def test_exception_does_not_leak_api_key() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        return_value=httpx.Response(401, json={"error": "bad"}),
    )
    client = HumanitixClient(api_key="super-secret-key")
    with pytest.raises(AuthenticationError) as exc_info:
        await client.get("/v1/events")
    message = str(exc_info.value)
    assert "super-secret-key" not in message
    assert "<redacted>" not in message  # no query-param key to redact here
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_pagination_yields_all_pages() -> None:
    page1 = respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 1, "pageSize": 2}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "1"}, {"id": "2"}], "total": 5},
        ),
    )
    page2 = respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 2, "pageSize": 2}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "3"}, {"id": "4"}], "total": 5},
        ),
    )
    page3 = respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 3, "pageSize": 2}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "5"}], "total": 5},
        ),
    )

    client = _client()
    items = [
        item async for item in client.paginate("/v1/events", result_key="events", page_size=2)
    ]
    assert [item["id"] for item in items] == ["1", "2", "3", "4", "5"]
    assert page1.called
    assert page2.called
    assert page3.called
    await client.close()


@respx.mock
async def test_pagination_reads_orders_collection_key() -> None:
    path = f"{DEFAULT_BASE_URL}/v1/events/evt_123/orders"
    page1 = respx.get(
        path,
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 1},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"orders": [{"id": "ord_1"}], "total": 2},
        ),
    )
    page2 = respx.get(
        path,
        params={"eventDateId": "ed_111", "page": 2, "pageSize": 1},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"orders": [{"id": "ord_2"}], "total": 2},
        ),
    )

    client = _client()
    orders = [
        item
        async for item in client.paginate(
            "/v1/events/evt_123/orders",
            result_key="orders",
            params={"eventDateId": "ed_111"},
            page_size=1,
        )
    ]

    assert [order["id"] for order in orders] == ["ord_1", "ord_2"]
    assert page1.called
    assert page2.called
    await client.close()


@respx.mock
@pytest.mark.parametrize(
    ("result_key", "response"),
    [
        ("events", {"total": 0}),
        ("tickets", {"tickets": {"id": "tkt_1"}, "total": 1}),
    ],
)
async def test_pagination_rejects_invalid_collection_envelopes(
    result_key: str,
    response: dict[str, object],
) -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={"page": 1, "pageSize": 100},
    ).mock(return_value=httpx.Response(200, json=response))

    client = _client()
    expected = "missing the required" if result_key == "events" else "non-list"
    with pytest.raises(ValidationError, match=expected):
        async for _ in client.paginate(
            "/v1/events/evt_123/tickets",
            result_key=result_key,
        ):
            pass
    await client.close()


@respx.mock
async def test_pagination_max_items_cap() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 1, "pageSize": 3}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "total": 10},
        ),
    )
    client = _client()
    items = [
        item
        async for item in client.paginate(
            "/v1/events",
            result_key="events",
            page_size=3,
            max_items=2,
        )
    ]
    assert [item["id"] for item in items] == ["1", "2"]
    await client.close()


@respx.mock
async def test_pagination_default_max_items_500() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 1, "pageSize": 100}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": str(i)} for i in range(501)], "total": 1000},
        ),
    )
    client = _client()
    items = [item async for item in client.paginate("/v1/events", result_key="events")]
    assert len(items) == 500
    await client.close()


@respx.mock
async def test_pagination_throttle_between_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("humanitix_mcp.client.asyncio.sleep", _fake_sleep)

    respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 1, "pageSize": 1}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "1"}], "total": 2},
        ),
    )
    respx.get(f"{DEFAULT_BASE_URL}/v1/events", params={"page": 2, "pageSize": 1}).mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"id": "2"}], "total": 2},
        ),
    )

    client = _client()
    items = [
        item
        async for item in client.paginate(
            "/v1/events",
            result_key="events",
            page_size=1,
            max_items=2,
        )
    ]
    assert len(items) == 2
    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.25, abs=0.01)
    await client.close()


@respx.mock
async def test_pagination_invalid_page_size() -> None:
    client = _client()
    with pytest.raises(ValidationError, match="page_size must be between 1 and 100"):
        async for _ in client.paginate("/v1/events", result_key="events", page_size=101):
            pass
    await client.close()


@respx.mock
async def test_context_manager_closes_client() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events").mock(
        return_value=httpx.Response(200, json={"events": [], "total": 0}),
    )
    async with HumanitixClient(api_key=FAKE_API_KEY) as client:
        await client.get("/v1/events")
    assert client._client.is_closed
