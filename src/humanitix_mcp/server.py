"""MCP server entry point for the Humanitix Public API (Phase 4).

This module implements a read-only stdio MCP server using the official
``mcp`` Python SDK. It exposes nine tools that wrap the existing async
client, Pydantic models, and deterministic formatting helpers.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from humanitix_mcp.client import (
    AuthenticationError,
    AuthorizationError,
    BadRequestError,
    ConfigurationError,
    HumanitixClient,
    HumanitixError,
    NotFoundError,
    ServerError,
    TransportError,
    ValidationError,
)
from humanitix_mcp.formatting import (
    format_check_in_count,
    format_event,
    format_order,
    format_ticket,
)
from humanitix_mcp.models import (
    CheckInCount,
    Event,
    Order,
    Ticket,
)

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_ITEMS = 500
MAX_PAGE_SIZE = 100


class _ValidationFailure(HumanitixError):
    """Raised when tool arguments fail server-side validation."""


@asynccontextmanager
async def app_lifespan(server: Server) -> AsyncIterator[dict[str, Any]]:
    """Create one HumanitixClient for the lifetime of the stdio connection."""
    client = HumanitixClient(
        api_key=os.environ.get("HUMANITIX_API_KEY") or None,
        base_url=os.environ.get("HUMANITIX_BASE_URL") or None,
    )
    try:
        yield {"client": client}
    finally:
        await client.close()


def _get_client(ctx: Any) -> HumanitixClient:
    """Return the lifespan-managed client from a request context."""
    return ctx.request_context.lifespan_context["client"]


def _error_response(message: str, *, status_code: int | None = None) -> dict[str, Any]:
    """Return a consistent structured error dictionary.

    The returned dictionary never includes the API key or traceback details.
    """
    result: dict[str, Any] = {"success": False, "error": message}
    if status_code is not None:
        result["statusCode"] = status_code
    return result


def _handle_error(exc: Exception) -> dict[str, Any]:
    """Map client and validation exceptions to structured error dictionaries."""
    if isinstance(exc, ConfigurationError):
        return _error_response(
            "Server configuration error: HUMANITIX_API_KEY is not set.",
            status_code=exc.status_code,
        )
    if isinstance(exc, AuthenticationError):
        return _error_response(
            "Authentication failed: the configured API key was rejected by Humanitix.",
            status_code=exc.status_code,
        )
    if isinstance(exc, AuthorizationError):
        return _error_response(
            "Authorization failed: the API key cannot access the requested resource.",
            status_code=exc.status_code,
        )
    if isinstance(exc, NotFoundError):
        return _error_response(
            "The requested resource was not found in Humanitix.",
            status_code=exc.status_code,
        )
    if isinstance(exc, BadRequestError):
        return _error_response(
            f"Bad request: {exc.message}",
            status_code=exc.status_code,
        )
    if isinstance(exc, ValidationError | _ValidationFailure):
        return _error_response(
            f"Invalid request: {exc.message}",
            status_code=getattr(exc, "status_code", None),
        )
    if isinstance(exc, ServerError):
        return _error_response(
            "Humanitix returned a server error. Please retry later.",
            status_code=exc.status_code,
        )
    if isinstance(exc, TransportError):
        return _error_response(
            "Network or transport failure reaching Humanitix. Please retry later.",
            status_code=exc.status_code,
        )
    if isinstance(exc, HumanitixError):
        return _error_response(exc.message, status_code=exc.status_code)
    return _error_response("An unexpected error occurred while handling the request.")


def _validate_identifier(name: str, value: Any) -> str:
    """Return a non-empty identifier string or raise a validation failure."""
    if not isinstance(value, str) or not value.strip():
        raise _ValidationFailure(f"{name} is required and must be a non-empty string.")
    return value.strip()


def _validate_pagination(args: dict[str, Any]) -> tuple[int, int]:
    """Return a validated (page_size, max_items) tuple."""
    page_size = int(args.get("page_size", DEFAULT_PAGE_SIZE))
    max_items = int(args.get("max_items", DEFAULT_MAX_ITEMS))

    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise _ValidationFailure(
            f"page_size must be between 1 and {MAX_PAGE_SIZE}.",
        )
    if max_items < 1:
        raise _ValidationFailure("max_items must be at least 1.")

    return page_size, max_items


def _format_result(data: Any, *, raw: bool = False) -> dict[str, Any]:
    """Format a model or list of models using Phase 3 helpers.

    Raw mode returns the full Pydantic dump including unknown/extra fields.
    """
    if isinstance(data, list):
        return {"success": True, "items": [_format_item(item, raw=raw) for item in data]}
    return {"success": True, "data": _format_item(data, raw=raw)}


def _format_item(item: Any, *, raw: bool = False) -> dict[str, Any]:
    if isinstance(item, Event):
        return format_event(item, raw=raw)
    if isinstance(item, Order):
        return format_order(item, raw=raw)
    if isinstance(item, Ticket):
        return format_ticket(item, raw=raw)
    if isinstance(item, CheckInCount):
        return format_check_in_count(item, raw=raw)
    return dict(item) if isinstance(item, dict) else {}


def _tool(name: str, description: str, input_schema: dict[str, Any]) -> Tool:
    """Build a Tool definition with a JSON schema for its arguments."""
    return Tool(name=name, description=description, inputSchema=input_schema)


# -----------------------------------------------------------------------------
# Tool definitions
# -----------------------------------------------------------------------------

_TOOLS: list[Tool] = [
    _tool(
        "humanitix_list_events",
        (
            "List Humanitix events accessible to the configured API key. "
            "Returns trimmed event summaries by default; set raw=true to receive "
            "the full Humanitix payload. Results are paginated and capped at "
            "max_items (default 500). Use in_future_only=true to hide past events, "
            "or provide an ISO date string via since to filter by start date."
        ),
        {
            "type": "object",
            "properties": {
                "page_size": {
                    "type": "integer",
                    "description": "Number of events per page (1-100).",
                    "default": DEFAULT_PAGE_SIZE,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum total events to return (>=1).",
                    "default": DEFAULT_MAX_ITEMS,
                },
                "in_future_only": {
                    "type": "boolean",
                    "description": "Only return events that start in the future.",
                    "default": False,
                },
                "since": {
                    "type": "string",
                    "description": "ISO date/datetime string; only return events starting on or after this value.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw Humanitix payload instead of the trimmed summary.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_get_event",
        (
            "Fetch a single Humanitix event by its event identifier. "
            "Returns the trimmed event by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw Humanitix payload instead of the trimmed summary.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_list_event_dates",
        (
            "List the event dates (sessions) belonging to a Humanitix event. "
            "Event dates are required identifiers for orders, tickets, and check-in tools. "
            "Returns a projection of each date with its id, name, and start/end times."
        ),
        {
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw event payload instead of the date projection.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_list_orders",
        (
            "List orders for a specific Humanitix event date. "
            "The event_date_id is required; obtain it from humanitix_list_event_dates. "
            "Returns trimmed orders by default; set raw=true for full payloads."
        ),
        {
            "type": "object",
            "required": ["event_date_id"],
            "properties": {
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of orders per page (1-100).",
                    "default": DEFAULT_PAGE_SIZE,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum total orders to return (>=1).",
                    "default": DEFAULT_MAX_ITEMS,
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return raw order payloads instead of trimmed summaries.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_get_order",
        (
            "Fetch a single order by its order identifier. "
            "Returns a trimmed order by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Humanitix order identifier.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw Humanitix payload instead of the trimmed summary.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_list_tickets",
        (
            "List tickets for a specific Humanitix event date. "
            "The event_date_id is required; obtain it from humanitix_list_event_dates. "
            "Optionally filter by ticket status. Returns trimmed tickets by default; "
            "set raw=true for full payloads."
        ),
        {
            "type": "object",
            "required": ["event_date_id"],
            "properties": {
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (e.g. 'complete' or 'cancelled').",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of tickets per page (1-100).",
                    "default": DEFAULT_PAGE_SIZE,
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum total tickets to return (>=1).",
                    "default": DEFAULT_MAX_ITEMS,
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return raw ticket payloads instead of trimmed summaries.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_get_ticket",
        (
            "Fetch a single ticket by its ticket identifier. "
            "Returns a trimmed ticket by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["ticket_id"],
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Humanitix ticket identifier.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw Humanitix payload instead of the trimmed summary.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_get_check_in_count",
        (
            "Get check-in totals for a Humanitix event date. "
            "The event_date_id is required; obtain it from humanitix_list_event_dates. "
            "Warning: the /check-in-count endpoint is BETA and its response shape may change. "
            "Returns trimmed counts by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["event_date_id"],
            "properties": {
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw Humanitix payload instead of the trimmed summary.",
                    "default": False,
                },
            },
        },
    ),
    _tool(
        "humanitix_sales_summary",
        (
            "Build a sales summary for a Humanitix event date by aggregating tickets. "
            "Reports sold/complete counts, cancelled counts, gross revenue (when price data "
            "is available), and capacity utilisation (when a trustworthy capacity value is "
            "present). The event_date_id is required; obtain it from humanitix_list_event_dates."
        ),
        {
            "type": "object",
            "required": ["event_date_id"],
            "properties": {
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Maximum total tickets to scan (>=1).",
                    "default": DEFAULT_MAX_ITEMS,
                },
                "raw": {
                    "type": "boolean",
                    "description": "Return the raw aggregated numbers without trimming.",
                    "default": False,
                },
            },
        },
    ),
]


# -----------------------------------------------------------------------------
# Tool handlers
# -----------------------------------------------------------------------------

async def _list_events(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    page_size, max_items = _validate_pagination(args)
    raw = bool(args.get("raw", False))
    in_future_only = bool(args.get("in_future_only", False))
    since = args.get("since")

    params: dict[str, Any] = {}
    if since is not None:
        params["since"] = since

    items = [
        Event.model_validate(item)
        async for item in client.paginate(
            "/v1/events",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
    ]

    if in_future_only:
        now = datetime.now(timezone.utc)
        items = [e for e in items if e.start_date is not None and e.start_date >= now]

    return _format_result(items, raw=raw)


async def _get_event(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(f"/v1/events/{event_id}")
    return _format_result(Event.model_validate(data), raw=raw)


async def _list_event_dates(
    client: HumanitixClient,
    args: dict[str, Any],
) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(f"/v1/events/{event_id}")
    event = Event.model_validate(data)

    if raw:
        return _format_result(event, raw=True)

    return {
        "success": True,
        "eventId": event.id,
        "eventName": event.name,
        "eventDates": [
            {
                "id": date.id,
                "name": date.name,
                "startDate": date.start_date.isoformat() if date.start_date else None,
                "endDate": date.end_date.isoformat() if date.end_date else None,
            }
            for date in event.event_dates
        ],
    }


async def _list_orders(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    page_size, max_items = _validate_pagination(args)
    raw = bool(args.get("raw", False))

    items = [
        Order.model_validate(item)
        async for item in client.paginate(
            f"/v1/event-dates/{event_date_id}/orders",
            page_size=page_size,
            max_items=max_items,
        )
    ]
    return _format_result(items, raw=raw)


async def _get_order(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    order_id = _validate_identifier("order_id", args.get("order_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(f"/v1/orders/{order_id}")
    return _format_result(Order.model_validate(data), raw=raw)


async def _list_tickets(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    page_size, max_items = _validate_pagination(args)
    raw = bool(args.get("raw", False))
    status = args.get("status")

    params: dict[str, Any] = {}
    if status is not None:
        params["status"] = status

    items = [
        Ticket.model_validate(item)
        async for item in client.paginate(
            f"/v1/event-dates/{event_date_id}/tickets",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
    ]
    return _format_result(items, raw=raw)


async def _get_ticket(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    ticket_id = _validate_identifier("ticket_id", args.get("ticket_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(f"/v1/tickets/{ticket_id}")
    return _format_result(Ticket.model_validate(data), raw=raw)


async def _get_check_in_count(
    client: HumanitixClient,
    args: dict[str, Any],
) -> dict[str, Any]:
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(f"/v1/event-dates/{event_date_id}/check-in-count")
    return _format_result(CheckInCount.model_validate(data), raw=raw)


def _ticket_price(ticket: Ticket) -> float | None:
    """Extract a numeric price from ticket extras if available."""
    extras = ticket.model_dump(by_alias=True, exclude_none=False)
    for key in ("totalPrice", "price", "unitPrice"):
        value = extras.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return None


def _ticket_capacity(ticket: Ticket) -> int | None:
    """Extract a trustworthy capacity value from ticket extras if available."""
    extras = ticket.model_dump(by_alias=True, exclude_none=False)
    for key in ("capacity", "ticketTypeCapacity", "maxCapacity", "totalCapacity"):
        value = extras.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _is_sold_status(status: str | None) -> bool:
    """Return True for statuses that represent a sold/complete ticket."""
    return status is not None and status.lower() in {"complete", "sold", "paid", "active"}


def _is_cancelled_status(status: str | None) -> bool:
    """Return True for statuses that represent a cancelled ticket."""
    return status is not None and status.lower() == "cancelled"


async def _sales_summary(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    max_items = int(args.get("max_items", DEFAULT_MAX_ITEMS))
    raw = bool(args.get("raw", False))

    if max_items < 1:
        raise _ValidationFailure("max_items must be at least 1.")

    tickets = [
        Ticket.model_validate(item)
        async for item in client.paginate(
            f"/v1/event-dates/{event_date_id}/tickets",
            page_size=DEFAULT_PAGE_SIZE,
            max_items=max_items,
        )
    ]

    groups: dict[tuple[str | None, str | None], dict[str, Any]] = defaultdict(
        lambda: {
            "sold": 0,
            "cancelled": 0,
            "revenue": 0.0,
            "revenueAvailable": False,
            "capacity": None,
            "capacityAvailable": False,
        }
    )

    for ticket in tickets:
        key = (ticket.ticket_type_id, ticket.ticket_type_name)
        group = groups[key]

        if _is_sold_status(ticket.status):
            group["sold"] += 1
        elif _is_cancelled_status(ticket.status):
            group["cancelled"] += 1

        price = _ticket_price(ticket)
        if price is not None:
            group["revenue"] += price
            group["revenueAvailable"] = True

        capacity = _ticket_capacity(ticket)
        if capacity is not None and not group["capacityAvailable"]:
            group["capacity"] = capacity
            group["capacityAvailable"] = True

    by_ticket_type: list[dict[str, Any]] = []
    total_sold = 0
    total_cancelled = 0
    total_revenue = 0.0
    revenue_available = False
    overall_capacity_available = False
    overall_capacity: int | None = None

    for (ticket_type_id, ticket_type_name), group in sorted(groups.items()):
        entry: dict[str, Any] = {
            "ticketTypeId": ticket_type_id,
            "ticketTypeName": ticket_type_name,
            "sold": group["sold"],
            "cancelled": group["cancelled"],
        }

        if group["revenueAvailable"]:
            entry["grossRevenue"] = round(group["revenue"], 2)
            entry["revenueAvailable"] = True
        else:
            entry["revenueAvailable"] = False

        if group["capacityAvailable"] and group["capacity"]:
            entry["capacity"] = group["capacity"]
            entry["capacityPercentage"] = round(
                (group["sold"] / group["capacity"]) * 100, 2
            )
            entry["capacityAvailable"] = True
            if not overall_capacity_available:
                overall_capacity = group["capacity"]
                overall_capacity_available = True
        else:
            entry["capacityAvailable"] = False

        total_sold += group["sold"]
        total_cancelled += group["cancelled"]
        if group["revenueAvailable"]:
            total_revenue += group["revenue"]
            revenue_available = True

        if raw:
            by_ticket_type.append(group)
        else:
            by_ticket_type.append(entry)

    totals: dict[str, Any] = {
        "sold": total_sold,
        "cancelled": total_cancelled,
    }
    if revenue_available:
        totals["grossRevenue"] = round(total_revenue, 2)
    if overall_capacity_available and overall_capacity:
        totals["capacity"] = overall_capacity
        totals["capacityPercentage"] = round(
            (total_sold / overall_capacity) * 100, 2
        )

    result: dict[str, Any] = {
        "success": True,
        "eventDateId": event_date_id,
        "totals": totals,
        "revenueAvailable": revenue_available,
        "capacityAvailable": overall_capacity_available,
        "byTicketType": by_ticket_type,
    }

    return result


_TOOL_HANDLERS: dict[str, Any] = {
    "humanitix_list_events": _list_events,
    "humanitix_get_event": _get_event,
    "humanitix_list_event_dates": _list_event_dates,
    "humanitix_list_orders": _list_orders,
    "humanitix_get_order": _get_order,
    "humanitix_list_tickets": _list_tickets,
    "humanitix_get_ticket": _get_ticket,
    "humanitix_get_check_in_count": _get_check_in_count,
    "humanitix_sales_summary": _sales_summary,
}


async def on_list_tools() -> dict[str, Any]:
    return {"tools": _TOOLS}


async def on_call_tool(
    ctx: Any,
    params: Any,
) -> CallToolResult:
    name = params.name
    arguments = dict(params.arguments or {})

    if name not in _TOOL_HANDLERS:
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps(_error_response(f"Unknown tool: {name}")))],
        )

    client = _get_client(ctx)
    handler = _TOOL_HANDLERS[name]

    try:
        result = await handler(client, arguments)
    except Exception as exc:
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps(_handle_error(exc)))],
        )

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result))],
    )


def create_server() -> Server:
    """Create and configure the Humanitix MCP server."""
    server = Server(
        "humanitix-mcp",
        version="0.1.0",
        lifespan=app_lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    return server


async def run_server() -> None:
    """Run the server over stdio."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Run the Humanitix MCP server."""
    import asyncio

    asyncio.run(run_server())
