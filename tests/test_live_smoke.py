"""Opt-in, read-only smoke coverage against the public CHCon 2025 event."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from mcp.types import CallToolRequestParams

from humanitix_mcp.client import HumanitixClient
from humanitix_mcp.server import _TOOLS, on_call_tool

pytestmark = pytest.mark.skipif(
    os.environ.get("HUMANITIX_LIVE_SMOKE") != "1",
    reason="set HUMANITIX_LIVE_SMOKE=1 to run read-only live smoke coverage",
)

_CHCON_EVENT_ID = "6875f6fd6e7ba808d7baa311"
_NZ_LOCATION_ARGUMENTS = {"override_location": "NZ"}
_CORE_TOOL_NAMES = {
    "humanitix_list_events",
    "humanitix_get_event",
    "humanitix_list_event_dates",
    "humanitix_list_orders",
    "humanitix_get_order",
    "humanitix_list_tickets",
    "humanitix_get_ticket",
    "humanitix_get_check_in_count",
    "humanitix_sales_summary",
}
_IDENTIFIER_FIELD_NAMES = ("_id", "id", "orderId", "ticketId")
_CHCON_EXPANDED_NAME = "christchurch hacker con"


def _context(client: HumanitixClient) -> Any:
    class _RequestContext:
        lifespan_context = {"client": client}

    class _Context:
        request_context = _RequestContext()

    return _Context()


async def _call_tool(
    client: HumanitixClient,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = await on_call_tool(
            _context(client),
            CallToolRequestParams(name=name, arguments=arguments),
        )
    except TypeError as exc:
        if arguments.get("raw") is True and "not JSON serializable" in str(exc):
            pytest.fail(
                f"acceptance blocker: {name} could not serialize its raw list response"
            )
        raise
    return json.loads(result.content[0].text)


def _require_success(tool_name: str, response: dict[str, Any]) -> None:
    if response.get("success") is not True:
        pytest.fail(f"acceptance blocker: {tool_name} did not return success")


def _require_id(value: Any, resource: str) -> str:
    if not isinstance(value, str) or not value.strip():
        pytest.fail(f"acceptance blocker: {resource} returned no usable identifier")
    return value


def _require_items(tool_name: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    items = response.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        pytest.fail(f"acceptance blocker: {tool_name} returned a malformed list result")
    return items


def _raw_identifier_fields(items: list[dict[str, Any]]) -> set[str]:
    """Return only identifier field names, never raw values or records."""
    return {
        field_name
        for item in items
        for field_name in _IDENTIFIER_FIELD_NAMES
        if isinstance(item.get(field_name), str) and item[field_name].strip()
    }


async def _find_list_identifier(
    client: HumanitixClient,
    *,
    tool_name: str,
    event_date_ids: list[str],
    resource: str,
) -> tuple[str, str] | None:
    """Inspect each date's raw list structure before relying on trimmed IDs."""
    found: tuple[str, str] | None = None

    for event_date_id in event_date_ids:
        arguments = {
            "event_id": _CHCON_EVENT_ID,
            "event_date_id": event_date_id,
            "page_size": 1,
            "max_items": 1,
            "raw": True,
            **_NZ_LOCATION_ARGUMENTS,
        }
        raw_response = await _call_tool(client, tool_name, arguments)
        _require_success(f"{tool_name} with raw=true", raw_response)
        raw_items = _require_items(f"{tool_name} with raw=true", raw_response)
        raw_identifier_fields = _raw_identifier_fields(raw_items)

        arguments["raw"] = False
        response = await _call_tool(client, tool_name, arguments)
        _require_success(tool_name, response)
        items = _require_items(tool_name, response)

        if raw_items and not raw_identifier_fields:
            pytest.fail(
                f"acceptance blocker: raw {resource} list records expose no usable "
                "identifier field"
            )
        if raw_items and not items:
            pytest.fail(
                f"acceptance blocker: {tool_name} lost raw list records during formatting"
            )

        for item in items:
            identifier = item.get("id")
            if isinstance(identifier, str) and identifier.strip() and found is None:
                found = (event_date_id, identifier)

        if raw_items and raw_identifier_fields and found is None:
            pytest.fail(
                f"acceptance blocker: {tool_name} did not map its raw identifier "
                "field to the trimmed id"
            )

    return found


def _require_beta_success_or_service_error(response: dict[str, Any]) -> None:
    """Accept documented BETA service instability without calling it success."""
    if response.get("success") is True:
        return

    status_code = response.get("statusCode")
    is_service_error = (
        response.get("success") is False
        and isinstance(response.get("error"), str)
        and bool(response["error"].strip())
        and isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 500 <= status_code <= 599
    )
    if not is_service_error:
        pytest.fail(
            "acceptance blocker: humanitix_get_check_in_count did not return "
            "success or a structured 5xx service error"
        )


@pytest.mark.asyncio
async def test_live_core_tools_against_public_chcon_event() -> None:
    """Exercise every registered read-only tool without persisting live records."""
    registered_names = {tool.name for tool in _TOOLS}
    if registered_names != _CORE_TOOL_NAMES:
        pytest.fail("acceptance blocker: registered core tool set changed")

    client = HumanitixClient()
    try:
        default_events_response = await _call_tool(
            client,
            "humanitix_list_events",
            {"page_size": 100, "max_items": 500},
        )
        _require_success("humanitix_list_events", default_events_response)

        nz_events_response = await _call_tool(
            client,
            "humanitix_list_events",
            {
                "page_size": 100,
                "max_items": 500,
                **_NZ_LOCATION_ARGUMENTS,
            },
        )
        _require_success(
            "humanitix_list_events with override_location=NZ",
            nz_events_response,
        )

        event_response = await _call_tool(
            client,
            "humanitix_get_event",
            {"event_id": _CHCON_EVENT_ID, **_NZ_LOCATION_ARGUMENTS},
        )
        _require_success("humanitix_get_event", event_response)
        event = event_response.get("data")
        if not isinstance(event, dict):
            pytest.fail("acceptance blocker: humanitix_get_event returned malformed data")
        event_name = str(event.get("name", "")).casefold()
        if "chcon" not in event_name and _CHCON_EXPANDED_NAME not in event_name:
            pytest.fail(
                "acceptance blocker: public event did not identify itself as CHCon"
            )

        event_dates = event.get("eventDates")
        if not isinstance(event_dates, list):
            pytest.fail(
                "acceptance blocker: humanitix_get_event returned malformed event dates"
            )
        event_date_ids = [
            _require_id(event_date.get("id"), "CHCon event date")
            for event_date in event_dates
            if isinstance(event_date, dict)
        ]
        if not event_date_ids:
            pytest.fail(
                "acceptance blocker: public CHCon event has no usable event-date id"
            )

        dates_response = await _call_tool(
            client,
            "humanitix_list_event_dates",
            {"event_id": _CHCON_EVENT_ID, **_NZ_LOCATION_ARGUMENTS},
        )
        _require_success("humanitix_list_event_dates", dates_response)

        order = await _find_list_identifier(
            client,
            tool_name="humanitix_list_orders",
            event_date_ids=event_date_ids,
            resource="order",
        )

        ticket = await _find_list_identifier(
            client,
            tool_name="humanitix_list_tickets",
            event_date_ids=event_date_ids,
            resource="ticket",
        )

        check_in_response = await _call_tool(
            client,
            "humanitix_get_check_in_count",
            {"event_id": _CHCON_EVENT_ID, "event_date_id": event_date_ids[0]},
        )
        _require_beta_success_or_service_error(check_in_response)

        sales_response = await _call_tool(
            client,
            "humanitix_sales_summary",
            {
                "event_id": _CHCON_EVENT_ID,
                "event_date_id": event_date_ids[0],
                "max_items": 1,
                **_NZ_LOCATION_ARGUMENTS,
            },
        )
        _require_success("humanitix_sales_summary", sales_response)

        if order is None:
            pytest.fail(
                "acceptance blocker: no event date returned an order with a usable id"
            )
        order_date_id, order_id = order
        order_response = await _call_tool(
            client,
            "humanitix_get_order",
            {
                "event_id": _CHCON_EVENT_ID,
                "order_id": order_id,
                **_NZ_LOCATION_ARGUMENTS,
            },
        )
        _require_success("humanitix_get_order", order_response)

        if ticket is None:
            pytest.fail(
                "acceptance blocker: no event date returned a ticket with a usable id"
            )
        ticket_date_id, ticket_id = ticket
        ticket_response = await _call_tool(
            client,
            "humanitix_get_ticket",
            {
                "event_id": _CHCON_EVENT_ID,
                "ticket_id": ticket_id,
                **_NZ_LOCATION_ARGUMENTS,
            },
        )
        _require_success("humanitix_get_ticket", ticket_response)
    finally:
        await client.close()
