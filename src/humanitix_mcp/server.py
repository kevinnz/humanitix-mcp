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
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv
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
DOTENV_PATH_ENV = "HUMANITIX_DOTENV_PATH"


class _ValidationFailure(HumanitixError):
    """Raised when tool arguments fail server-side validation."""


def _default_dotenv_path() -> Path | None:
    """Find a local dotenv file without requiring the process cwd to be the repo."""
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate

    source_candidate = Path(__file__).resolve().parents[2] / ".env"
    if source_candidate.is_file():
        return source_candidate
    return None


def load_server_config() -> None:
    """Load dotenv configuration while preserving explicit process environment."""
    configured_path = os.environ.get(DOTENV_PATH_ENV)
    if configured_path is not None:
        dotenv_path = Path(configured_path).expanduser()
        if not dotenv_path.is_absolute():
            dotenv_path = Path.cwd() / dotenv_path
    else:
        dotenv_path = _default_dotenv_path()

    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)


class _ConnectionClient:
    """Lazily create and retain the client for one MCP connection."""

    def __init__(self) -> None:
        self._client: HumanitixClient | None = None

    def get(self) -> HumanitixClient:
        if self._client is None:
            self._client = HumanitixClient(
                api_key=os.environ.get("HUMANITIX_API_KEY") or None,
                base_url=os.environ.get("HUMANITIX_BASE_URL") or None,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


@asynccontextmanager
async def app_lifespan(server: Server) -> AsyncIterator[dict[str, Any]]:
    """Create a lazily initialized client manager for one stdio connection."""
    load_server_config()
    client = _ConnectionClient()
    try:
        yield {"client": client}
    finally:
        await client.close()


def _get_client(ctx: Any) -> HumanitixClient:
    """Return the lifespan-managed client from a request context."""
    lifespan_context = getattr(ctx, "lifespan_context", None)
    if lifespan_context is None:
        lifespan_context = ctx.request_context.lifespan_context
    client = lifespan_context["client"]
    if isinstance(client, _ConnectionClient):
        return client.get()
    return client


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


def _override_location_params(args: dict[str, Any]) -> dict[str, Any]:
    """Return the optional documented location-override query parameter."""
    override_location = args.get("override_location")
    return {"overrideLocation": override_location} if override_location is not None else {}


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
            "max_items (default 500). Use in_future_only=true to retrieve only "
            "events whose end date is in the future, or provide an ISO 8601 "
            "datetime via since."
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
                    "description": "Only return events whose end date is in the future.",
                    "default": False,
                },
                "since": {
                    "type": "string",
                    "description": "ISO 8601 datetime; return results since this time.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
            "List orders for a specific Humanitix event and event date. "
            "The event_id identifies the event; event_date_id identifies its specific "
            "date and can be obtained from humanitix_list_event_dates. "
            "Returns trimmed orders by default; set raw=true for full payloads."
        ),
        {
            "type": "object",
            "required": ["event_id", "event_date_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier that owns the order.",
                },
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO 8601 datetime; return results since this time.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
            "Fetch a single order by its event identifier and order identifier. "
            "Returns a trimmed order by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["event_id", "order_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier that owns the order.",
                },
                "order_id": {
                    "type": "string",
                    "description": "Humanitix order identifier within the event.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
            "List tickets for a specific Humanitix event and event date. "
            "The event_id identifies the event; event_date_id identifies its specific "
            "date and can be obtained from humanitix_list_event_dates. "
            "Optionally filter by ticket status. Returns trimmed tickets by default; "
            "set raw=true for full payloads."
        ),
        {
            "type": "object",
            "required": ["event_id", "event_date_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier that owns the tickets.",
                },
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (e.g. 'complete' or 'cancelled').",
                },
                "since": {
                    "type": "string",
                    "description": "ISO 8601 datetime; return results since this time.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
            "Fetch a single ticket by its event identifier and ticket identifier. "
            "Returns a trimmed ticket by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["event_id", "ticket_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier that owns the ticket.",
                },
                "ticket_id": {
                    "type": "string",
                    "description": "Humanitix ticket identifier within the event.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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
            "Get check-in totals for a Humanitix event and event date. "
            "The event_id identifies the event; event_date_id identifies its specific "
            "date and can be obtained from humanitix_list_event_dates. "
            "Warning: the /check-in-count endpoint is BETA and its response shape may change. "
            "Returns trimmed counts by default; set raw=true for the full payload."
        ),
        {
            "type": "object",
            "required": ["event_id", "event_date_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier for the check-in count.",
                },
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
            "Build a sales summary for a Humanitix event and event date by aggregating tickets. "
            "Reports sold/complete counts, cancelled counts, gross revenue (when price data "
            "is available), and capacity utilisation (when a trustworthy capacity value is "
            "present). The event_id identifies the event; event_date_id identifies its specific "
            "date and can be obtained from humanitix_list_event_dates."
        ),
        {
            "type": "object",
            "required": ["event_id", "event_date_id"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Humanitix event identifier that owns the tickets.",
                },
                "event_date_id": {
                    "type": "string",
                    "description": "Humanitix event-date identifier.",
                },
                "override_location": {
                    "type": "string",
                    "description": "ISO 3166-1 alpha-2 country code to override the account location.",
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

    params = _override_location_params(args)
    if in_future_only:
        params["inFutureOnly"] = True
    if since is not None:
        params["since"] = since

    items = [
        Event.model_validate(item)
        async for item in client.paginate(
            "/v1/events",
            result_key="events",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
    ]

    return _format_result(items, raw=raw)


async def _get_event(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(
        f"/v1/events/{event_id}",
        params=_override_location_params(args),
    )
    return _format_result(Event.model_validate(data), raw=raw)


async def _list_event_dates(
    client: HumanitixClient,
    args: dict[str, Any],
) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(
        f"/v1/events/{event_id}",
        params=_override_location_params(args),
    )
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
    event_id = _validate_identifier("event_id", args.get("event_id"))
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    page_size, max_items = _validate_pagination(args)
    raw = bool(args.get("raw", False))
    since = args.get("since")

    params = _override_location_params(args)
    params["eventDateId"] = event_date_id
    if since is not None:
        params["since"] = since

    items = [
        Order.model_validate(item)
        async for item in client.paginate(
            f"/v1/events/{event_id}/orders",
            result_key="orders",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
    ]
    return _format_result(items, raw=raw)


async def _get_order(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    order_id = _validate_identifier("order_id", args.get("order_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(
        f"/v1/events/{event_id}/orders/{order_id}",
        params=_override_location_params(args),
    )
    return _format_result(Order.model_validate(data), raw=raw)


async def _list_tickets(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    page_size, max_items = _validate_pagination(args)
    raw = bool(args.get("raw", False))
    status = args.get("status")
    since = args.get("since")

    params = _override_location_params(args)
    params["eventDateId"] = event_date_id
    if status is not None:
        params["status"] = status
    if since is not None:
        params["since"] = since

    items = [
        Ticket.model_validate(item)
        async for item in client.paginate(
            f"/v1/events/{event_id}/tickets",
            result_key="tickets",
            params=params,
            page_size=page_size,
            max_items=max_items,
        )
    ]
    return _format_result(items, raw=raw)


async def _get_ticket(client: HumanitixClient, args: dict[str, Any]) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    ticket_id = _validate_identifier("ticket_id", args.get("ticket_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(
        f"/v1/events/{event_id}/tickets/{ticket_id}",
        params=_override_location_params(args),
    )
    return _format_result(Ticket.model_validate(data), raw=raw)


async def _get_check_in_count(
    client: HumanitixClient,
    args: dict[str, Any],
) -> dict[str, Any]:
    event_id = _validate_identifier("event_id", args.get("event_id"))
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    raw = bool(args.get("raw", False))
    data = await client.get(
        f"/v1/events/{event_id}/check-in-count",
        params={"eventDateId": event_date_id},
    )
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
    event_id = _validate_identifier("event_id", args.get("event_id"))
    event_date_id = _validate_identifier("event_date_id", args.get("event_date_id"))
    max_items = int(args.get("max_items", DEFAULT_MAX_ITEMS))
    raw = bool(args.get("raw", False))

    if max_items < 1:
        raise _ValidationFailure("max_items must be at least 1.")

    params = _override_location_params(args)
    params["eventDateId"] = event_date_id

    tickets = [
        Ticket.model_validate(item)
        async for item in client.paginate(
            f"/v1/events/{event_id}/tickets",
            result_key="tickets",
            params=params,
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
    overall_capacity_available = bool(groups)
    overall_capacity = 0

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
            overall_capacity += group["capacity"]
        else:
            entry["capacityAvailable"] = False
            overall_capacity_available = False

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


async def on_list_tools(
    _ctx: Any = None,
    _params: Any = None,
) -> dict[str, Any]:
    return {
        "tools": [
            tool.model_dump(by_alias=True, exclude_none=True) for tool in _TOOLS
        ]
    }


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

    try:
        client = _get_client(ctx)
    except ConfigurationError as exc:
        return CallToolResult(
            isError=True,
            content=[TextContent(type="text", text=json.dumps(_handle_error(exc)))],
        )

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
