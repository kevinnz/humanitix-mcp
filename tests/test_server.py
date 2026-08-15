"""Tests for the Humanitix MCP server (Phase 4)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
import respx

from mcp.types import CallToolRequestParams

from humanitix_mcp.client import HumanitixClient
from humanitix_mcp.server import (
    DOTENV_PATH_ENV,
    DEFAULT_MAX_ITEMS,
    _TOOLS,
    app_lifespan,
    _handle_error,
    create_server,
    load_server_config,
    on_call_tool,
    on_list_tools,
)
from humanitix_mcp.client import (
    AuthenticationError,
    ConfigurationError,
)
from humanitix_mcp import server as server_module

FAKE_API_KEY = "test-api-key-redacted"
DEFAULT_BASE_URL = "https://api.humanitix.com"
SERVER_CONFIG_FIXTURE = (
    Path(__file__).parent / "fixtures" / "server-config.env"
)
SERVER_NO_API_KEY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "server-no-api-key.env"
)


def _client() -> HumanitixClient:
    return HumanitixClient(api_key=FAKE_API_KEY)


def _fake_ctx(client: HumanitixClient) -> Any:
    """Return a minimal context object that the server handlers expect."""

    class _RequestContext:
        lifespan_context = {"client": client}

    class _Ctx:
        request_context = _RequestContext()

    return _Ctx()


async def _call_tool(
    client: HumanitixClient,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call a server tool and parse its JSON result."""
    params = CallToolRequestParams(name=name, arguments=arguments or {})
    result = await on_call_tool(_fake_ctx(client), params)
    text = result.content[0].text
    return json.loads(text)


def _tool_names() -> list[str]:
    return [tool.name for tool in _TOOLS]


def _tool_by_name(name: str) -> Any:
    return next(tool for tool in _TOOLS if tool.name == name)


# -----------------------------------------------------------------------------
# Tool registration
# -----------------------------------------------------------------------------


def test_server_registers_exactly_nine_tools() -> None:
    names = _tool_names()
    expected = [
        "humanitix_list_events",
        "humanitix_get_event",
        "humanitix_list_event_dates",
        "humanitix_list_orders",
        "humanitix_get_order",
        "humanitix_list_tickets",
        "humanitix_get_ticket",
        "humanitix_get_check_in_count",
        "humanitix_sales_summary",
    ]
    assert names == expected


def test_create_server_returns_server() -> None:
    from mcp.server import Server

    server = create_server()
    assert isinstance(server, Server)
    assert server.server_info.name == "humanitix-mcp"


async def test_list_tools_returns_nine_tools() -> None:
    result = await on_list_tools()
    assert len(result["tools"]) == 9


def test_event_scoped_tool_schemas_require_unambiguous_identifiers() -> None:
    required_by_tool = {
        "humanitix_list_orders": {"event_id", "event_date_id"},
        "humanitix_get_order": {"event_id", "order_id"},
        "humanitix_list_tickets": {"event_id", "event_date_id"},
        "humanitix_get_ticket": {"event_id", "ticket_id"},
        "humanitix_get_check_in_count": {"event_id", "event_date_id"},
        "humanitix_sales_summary": {"event_id", "event_date_id"},
    }

    for name, required in required_by_tool.items():
        schema = _tool_by_name(name).input_schema
        assert set(schema["required"]) == required
        assert required <= set(schema["properties"])


def test_override_location_schema_matches_supported_endpoints() -> None:
    supporting_tools = {
        "humanitix_list_events",
        "humanitix_get_event",
        "humanitix_list_event_dates",
        "humanitix_list_orders",
        "humanitix_get_order",
        "humanitix_list_tickets",
        "humanitix_get_ticket",
        "humanitix_sales_summary",
    }
    for name in supporting_tools:
        assert "override_location" in _tool_by_name(name).input_schema["properties"]

    assert "override_location" not in _tool_by_name(
        "humanitix_get_check_in_count"
    ).input_schema["properties"]


def test_load_server_config_uses_override_and_preserves_process_environment() -> None:
    with mock.patch.dict(
        os.environ,
        {DOTENV_PATH_ENV: str(SERVER_CONFIG_FIXTURE)},
        clear=True,
    ):
        load_server_config()

        assert os.environ["HUMANITIX_API_KEY"] == "dotenv-test-api-key"
        assert os.environ["HUMANITIX_BASE_URL"] == "https://dotenv.example.test"

        os.environ["HUMANITIX_API_KEY"] = "process-api-key"
        os.environ["HUMANITIX_BASE_URL"] = "https://process.example.test"
        load_server_config()

        assert os.environ["HUMANITIX_API_KEY"] == "process-api-key"
        assert os.environ["HUMANITIX_BASE_URL"] == "https://process.example.test"


async def test_lifespan_without_api_key_starts_and_tools_return_configuration_error(
) -> None:
    with mock.patch.dict(
        os.environ,
        {DOTENV_PATH_ENV: str(SERVER_NO_API_KEY_FIXTURE)},
        clear=True,
    ):
        async with app_lifespan(create_server()) as lifespan_context:
            assert lifespan_context["client"]._client is None
            assert len((await on_list_tools())["tools"]) == 9

            result = await on_call_tool(
                _fake_ctx(lifespan_context["client"]),
                CallToolRequestParams(name="humanitix_list_events", arguments={}),
            )

    response = json.loads(result.content[0].text)
    assert result.is_error is True
    assert response == {
        "success": False,
        "error": "Server configuration error: HUMANITIX_API_KEY is not set.",
    }


async def test_lifespan_reuses_and_closes_its_lazy_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[Any] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.closed = False
            clients.append(self)

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(server_module, "HumanitixClient", FakeClient)

    with mock.patch.dict(
        os.environ,
        {DOTENV_PATH_ENV: str(SERVER_CONFIG_FIXTURE)},
        clear=True,
    ):
        async with app_lifespan(create_server()) as lifespan_context:
            manager = lifespan_context["client"]
            assert manager.get() is manager.get()
            assert len(clients) == 1

    assert clients[0].closed is True


# -----------------------------------------------------------------------------
# Event tools
# -----------------------------------------------------------------------------


@respx.mock
async def test_list_events_calls_events_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events",
        params={"page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "evt_1",
                        "name": "Future Con",
                        "startDate": "2026-12-01T09:00:00+13:00",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_list_events")

    assert route.called
    assert result["success"] is True
    assert result["items"][0]["id"] == "evt_1"
    await client.close()


@respx.mock
async def test_list_events_forwards_pagination_params() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events",
        params={"overrideLocation": "NZ", "page": 1, "pageSize": 50},
    ).mock(
        return_value=httpx.Response(200, json={"events": [], "total": 0}),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_events",
        {"page_size": 50, "max_items": 10, "override_location": "NZ"},
    )

    assert route.called
    assert result["success"] is True
    assert result["items"] == []
    await client.close()


@respx.mock
async def test_list_events_forwards_in_future_only_without_local_filtering() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events",
        params={"inFutureOnly": True, "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "evt_past",
                        "name": "Past Con",
                        "startDate": "2020-01-01T09:00:00+13:00",
                    },
                    {
                        "id": "evt_future",
                        "name": "Future Con",
                        "startDate": "2030-01-01T09:00:00+13:00",
                    },
                ],
                "total": 2,
            },
        ),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_list_events", {"in_future_only": True})

    assert result["success"] is True
    assert [item["id"] for item in result["items"]] == ["evt_past", "evt_future"]
    assert route.called
    await client.close()


@respx.mock
async def test_list_events_since_filter() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events",
        params={"since": "2026-01-01T00:00:00Z", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(200, json={"events": [], "total": 0}),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_events",
        {"since": "2026-01-01T00:00:00Z"},
    )

    assert route.called
    assert result["success"] is True
    await client.close()


@respx.mock
async def test_get_event_calls_event_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123",
        params={"overrideLocation": "NZ"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_123",
                "name": "CHCon",
                "startDate": "2026-11-18T09:00:00+13:00",
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_event",
        {"event_id": "evt_123", "override_location": "NZ"},
    )

    assert route.called
    assert result["success"] is True
    assert result["data"]["id"] == "evt_123"
    await client.close()


@respx.mock
async def test_list_event_dates_returns_projection() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123",
        params={"overrideLocation": "NZ"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_123",
                "name": "CHCon",
                "eventDates": [
                    {
                        "id": "ed_111",
                        "name": "Day 1",
                        "startDate": "2026-11-18T09:00:00+13:00",
                        "endDate": "2026-11-18T17:00:00+13:00",
                    },
                    {
                        "id": "ed_222",
                        "name": "Day 2",
                        "startDate": "2026-11-19T09:00:00+13:00",
                        "endDate": "2026-11-19T17:00:00+13:00",
                    },
                ],
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_event_dates",
        {"event_id": "evt_123", "override_location": "NZ"},
    )

    assert route.called
    assert result["success"] is True
    assert result["eventId"] == "evt_123"
    assert len(result["eventDates"]) == 2
    assert result["eventDates"][0]["id"] == "ed_111"
    await client.close()


# -----------------------------------------------------------------------------
# Order tools
# -----------------------------------------------------------------------------


@respx.mock
async def test_list_orders_calls_event_scoped_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/orders",
        params={
            "eventDateId": "ed_111",
            "since": "2026-01-01T00:00:00Z",
            "overrideLocation": "NZ",
            "page": 1,
            "pageSize": 100,
        },
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "id": "ord_1",
                        "orderNumber": "CHC-001",
                        "status": "complete",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_orders",
        {
            "event_id": "evt_123",
            "event_date_id": "ed_111",
            "since": "2026-01-01T00:00:00Z",
            "override_location": "NZ",
        },
    )

    assert route.called
    assert result["success"] is True
    assert result["items"][0]["id"] == "ord_1"
    await client.close()


@respx.mock
async def test_get_order_calls_order_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/orders/ord_1",
        params={"overrideLocation": "NZ"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"id": "ord_1", "orderNumber": "CHC-001", "status": "complete"},
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_order",
        {"event_id": "evt_123", "order_id": "ord_1", "override_location": "NZ"},
    )

    assert route.called
    assert result["success"] is True
    assert result["data"]["id"] == "ord_1"
    await client.close()


# -----------------------------------------------------------------------------
# Ticket tools
# -----------------------------------------------------------------------------


@respx.mock
async def test_list_tickets_calls_event_scoped_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={
            "eventDateId": "ed_111",
            "overrideLocation": "NZ",
            "page": 1,
            "pageSize": 100,
        },
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tickets": [
                    {
                        "id": "tkt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_tickets",
        {
            "event_id": "evt_123",
            "event_date_id": "ed_111",
            "override_location": "NZ",
        },
    )

    assert route.called
    assert result["success"] is True
    assert result["items"][0]["id"] == "tkt_1"
    await client.close()


@respx.mock
async def test_list_tickets_forwards_status_filter() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={
            "eventDateId": "ed_111",
            "status": "cancelled",
            "since": "2026-01-01T00:00:00Z",
            "page": 1,
            "pageSize": 100,
        },
    ).mock(
        return_value=httpx.Response(200, json={"tickets": [], "total": 0}),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_tickets",
        {
            "event_id": "evt_123",
            "event_date_id": "ed_111",
            "status": "cancelled",
            "since": "2026-01-01T00:00:00Z",
        },
    )

    assert route.called
    assert result["success"] is True
    assert result["items"] == []
    await client.close()


@respx.mock
async def test_get_ticket_calls_ticket_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets/tkt_1",
        params={"overrideLocation": "NZ"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={"id": "tkt_1", "ticketTypeName": "General Admission", "status": "complete"},
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_ticket",
        {"event_id": "evt_123", "ticket_id": "tkt_1", "override_location": "NZ"},
    )

    assert route.called
    assert result["success"] is True
    assert result["data"]["id"] == "tkt_1"
    await client.close()


# -----------------------------------------------------------------------------
# Check-in count
# -----------------------------------------------------------------------------


@respx.mock
async def test_get_check_in_count_calls_beta_endpoint() -> None:
    route = respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/check-in-count",
        params={"eventDateId": "ed_111"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "eventId": "evt_123",
                "eventDateId": "ed_111",
                "checkedIn": 42,
                "ticketTypes": [
                    {
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "checkedIn": 42,
                    },
                ],
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_check_in_count",
        {"event_id": "evt_123", "event_date_id": "ed_111"},
    )

    assert route.called
    assert "overrideLocation" not in route.calls.last.request.url.params
    assert result["success"] is True
    assert result["data"] == {
        "eventId": "evt_123",
        "eventDateId": "ed_111",
        "checkedIn": 42,
        "ticketTypes": [
            {
                "ticketTypeId": "tt_1",
                "ticketTypeName": "General Admission",
                "checkedIn": 42,
            },
        ],
    }
    await client.close()


# -----------------------------------------------------------------------------
# Sales summary
# -----------------------------------------------------------------------------


@respx.mock
async def test_sales_summary_aggregates_by_ticket_type() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={
            "eventDateId": "ed_111",
            "overrideLocation": "NZ",
            "page": 1,
            "pageSize": 100,
        },
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tickets": [
                    {
                        "id": "tkt_1",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                        "unitPrice": 150.0,
                        "capacity": 100,
                    },
                    {
                        "id": "tkt_2",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                        "unitPrice": 150.0,
                        "capacity": 100,
                    },
                    {
                        "id": "tkt_3",
                        "ticketTypeId": "tt_2",
                        "ticketTypeName": "Workshop",
                        "status": "complete",
                        "unitPrice": 300.0,
                        "capacity": 50,
                    },
                ],
                "total": 3,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_sales_summary",
        {
            "event_id": "evt_123",
            "event_date_id": "ed_111",
            "override_location": "NZ",
        },
    )

    assert result["success"] is True
    assert result["eventDateId"] == "ed_111"
    assert result["totals"]["sold"] == 3
    assert result["totals"]["grossRevenue"] == 600.0
    assert result["totals"]["capacity"] == 150
    assert result["totals"]["capacityPercentage"] == 2.0

    by_type = {item["ticketTypeId"]: item for item in result["byTicketType"]}
    assert by_type["tt_1"]["sold"] == 2
    assert by_type["tt_1"]["grossRevenue"] == 300.0
    assert by_type["tt_1"]["capacity"] == 100
    assert by_type["tt_1"]["capacityPercentage"] == 2.0
    assert by_type["tt_2"]["sold"] == 1
    assert by_type["tt_2"]["grossRevenue"] == 300.0
    await client.close()


@respx.mock
async def test_sales_summary_omits_overall_capacity_when_any_type_lacks_it() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tickets": [
                    {
                        "id": "tkt_1",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                        "capacity": 100,
                    },
                    {
                        "id": "tkt_2",
                        "ticketTypeId": "tt_2",
                        "ticketTypeName": "Workshop",
                        "status": "complete",
                    },
                ],
                "total": 2,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_sales_summary",
        {"event_id": "evt_123", "event_date_id": "ed_111"},
    )

    assert result["capacityAvailable"] is False
    assert "capacity" not in result["totals"]
    assert "capacityPercentage" not in result["totals"]
    by_type = {item["ticketTypeId"]: item for item in result["byTicketType"]}
    assert by_type["tt_1"]["capacity"] == 100
    assert by_type["tt_1"]["capacityPercentage"] == 1.0
    assert by_type["tt_2"]["capacityAvailable"] is False
    assert "capacity" not in by_type["tt_2"]
    await client.close()


@respx.mock
async def test_sales_summary_counts_cancellations_separately() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tickets": [
                    {
                        "id": "tkt_1",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                        "unitPrice": 150.0,
                    },
                    {
                        "id": "tkt_2",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "cancelled",
                        "unitPrice": 150.0,
                    },
                ],
                "total": 2,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_sales_summary",
        {"event_id": "evt_123", "event_date_id": "ed_111"},
    )

    assert result["totals"]["sold"] == 1
    assert result["totals"]["cancelled"] == 1
    by_type = result["byTicketType"][0]
    assert by_type["sold"] == 1
    assert by_type["cancelled"] == 1
    await client.close()


@respx.mock
async def test_sales_summary_missing_capacity_marked_unavailable() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "tickets": [
                    {
                        "id": "tkt_1",
                        "ticketTypeId": "tt_1",
                        "ticketTypeName": "General Admission",
                        "status": "complete",
                    },
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_sales_summary",
        {"event_id": "evt_123", "event_date_id": "ed_111"},
    )

    assert result["capacityAvailable"] is False
    assert "capacity" not in result["totals"]
    assert result["byTicketType"][0]["capacityAvailable"] is False
    assert "capacity" not in result["byTicketType"][0]
    await client.close()


@respx.mock
async def test_sales_summary_empty_data() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/tickets",
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(200, json={"tickets": [], "total": 0}),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_sales_summary",
        {"event_id": "evt_123", "event_date_id": "ed_111"},
    )

    assert result["success"] is True
    assert result["totals"]["sold"] == 0
    assert result["totals"]["cancelled"] == 0
    assert result["byTicketType"] == []
    await client.close()


# -----------------------------------------------------------------------------
# Raw mode
# -----------------------------------------------------------------------------


@respx.mock
async def test_get_event_raw_mode_returns_extra_fields() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_123",
                "name": "CHCon",
                "internalField": "should surface in raw",
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_event",
        {"event_id": "evt_123", "raw": True},
    )

    assert result["success"] is True
    assert result["data"]["internalField"] == "should surface in raw"
    await client.close()


@respx.mock
async def test_list_events_raw_mode_returns_extra_fields() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events",
        params={"page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "evt_1",
                        "name": "Con",
                        "internalField": "raw value",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_list_events", {"raw": True})

    assert result["success"] is True
    assert result["items"][0]["internalField"] == "raw value"
    await client.close()


@respx.mock
async def test_list_orders_raw_mode_serializes_datetime_fields() -> None:
    respx.get(
        f"{DEFAULT_BASE_URL}/v1/events/evt_123/orders",
        params={"eventDateId": "ed_111", "page": 1, "pageSize": 100},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "id": "ord_1",
                        "createdAt": "2026-01-01T12:34:56Z",
                        "updatedAt": "2026-01-02T12:34:56Z",
                    }
                ],
                "total": 1,
            },
        ),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_orders",
        {"event_id": "evt_123", "event_date_id": "ed_111", "raw": True},
    )

    assert result["success"] is True
    assert result["items"][0]["createdAt"] == "2026-01-01T12:34:56Z"
    assert result["items"][0]["updatedAt"] == "2026-01-02T12:34:56Z"
    await client.close()


# -----------------------------------------------------------------------------
# Validation errors
# -----------------------------------------------------------------------------


async def test_get_event_missing_event_id_returns_structured_error() -> None:
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {})

    assert result["success"] is False
    assert "event_id" in result["error"]
    await client.close()


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("humanitix_list_orders", {"event_date_id": "ed_111"}),
        ("humanitix_get_order", {"order_id": "ord_1"}),
        ("humanitix_list_tickets", {"event_date_id": "ed_111"}),
        ("humanitix_get_ticket", {"ticket_id": "tkt_1"}),
        ("humanitix_get_check_in_count", {"event_date_id": "ed_111"}),
        ("humanitix_sales_summary", {"event_date_id": "ed_111"}),
    ],
)
async def test_event_scoped_tools_require_event_id(
    tool_name: str,
    arguments: dict[str, str],
) -> None:
    client = _client()
    result = await _call_tool(client, tool_name, arguments)

    assert result["success"] is False
    assert "event_id" in result["error"]
    await client.close()


async def test_list_events_invalid_page_size_returns_structured_error() -> None:
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_events",
        {"page_size": 200},
    )

    assert result["success"] is False
    assert "page_size" in result["error"]
    await client.close()


async def test_list_events_invalid_max_items_returns_structured_error() -> None:
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_list_events",
        {"max_items": 0},
    )

    assert result["success"] is False
    assert "max_items" in result["error"]
    await client.close()


# -----------------------------------------------------------------------------
# Structured client error handling
# -----------------------------------------------------------------------------


@respx.mock
async def test_missing_api_key_returns_configuration_error() -> None:
    with mock.patch.dict(os.environ, clear=True):
        with pytest.raises(ConfigurationError):
            HumanitixClient()


@respx.mock
async def test_authentication_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(401, json={"error": "Unauthorized"}),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "API key" in result["error"]
    assert "super-secret" not in result["error"]
    await client.close()


@respx.mock
async def test_authorization_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(403, json={"error": "Forbidden"}),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "cannot access" in result["error"].lower()
    await client.close()


@respx.mock
async def test_not_found_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_missing").mock(
        return_value=httpx.Response(404, json={"error": "Not found"}),
    )
    client = _client()
    result = await _call_tool(
        client,
        "humanitix_get_event",
        {"event_id": "evt_missing"},
    )

    assert result["success"] is False
    assert "not found" in result["error"].lower()
    await client.close()


@respx.mock
async def test_bad_request_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(400, json={"error": "Bad request"}),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "Bad request" in result["error"]
    await client.close()


@respx.mock
async def test_validation_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(422, json={"error": "Invalid"}),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "Invalid request" in result["error"]
    await client.close()


@respx.mock
async def test_server_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        return_value=httpx.Response(500, text="boom"),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "server error" in result["error"].lower()
    await client.close()


@respx.mock
async def test_transport_error_returns_structured_error() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/v1/events/evt_123").mock(
        side_effect=httpx.ConnectError("no route"),
    )
    client = _client()
    result = await _call_tool(client, "humanitix_get_event", {"event_id": "evt_123"})

    assert result["success"] is False
    assert "transport" in result["error"].lower()
    await client.close()


# -----------------------------------------------------------------------------
# Security / leakage
# -----------------------------------------------------------------------------


def test_error_handler_does_not_leak_api_key() -> None:
    exc = AuthenticationError("super-secret-key was rejected", status_code=401)
    result = _handle_error(exc)
    assert "super-secret-key" not in json.dumps(result)
    assert result["success"] is False
