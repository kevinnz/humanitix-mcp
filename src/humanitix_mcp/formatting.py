"""Deterministic response trimming helpers for MCP tool outputs (Phase 3).

.. warning::
    The ``/check-in-count`` endpoint is BETA and its response shape may change
    without notice. Trimmed check-in output is provided verbatim because the
    exact field names are still unstable.
"""

from __future__ import annotations

from typing import Any

from humanitix_mcp.models import (
    CheckInCount,
    CheckInCountEntry,
    Event,
    EventDate,
    Order,
    Ticket,
    TicketTypeBreakdown,
)


def _venue_or_location(event: Event) -> dict[str, Any]:
    """Return a consistent venue dict from either ``location.venue`` or ``venue``."""
    venue = None
    if event.location and event.location.venue:
        venue = event.location.venue
    elif event.venue:
        venue = event.venue

    if venue is None:
        return {}

    result: dict[str, Any] = {}
    if venue.name is not None:
        result["name"] = venue.name
    if venue.address is not None:
        result["address"] = venue.address
    if venue.city is not None:
        result["city"] = venue.city
    if venue.country is not None:
        result["country"] = venue.country
    return result


def _format_event_date(date: EventDate) -> dict[str, Any]:
    return {
        "id": date.id,
        "name": date.name,
        "startDate": date.start_date.isoformat() if date.start_date else None,
        "endDate": date.end_date.isoformat() if date.end_date else None,
    }


def format_event(event: Event, *, raw: bool = False) -> dict[str, Any]:
    """Return a trimmed event dictionary."""
    if raw:
        return event.model_dump(by_alias=True, exclude_none=False)

    result: dict[str, Any] = {
        "id": event.id,
        "name": event.name,
        "slug": event.slug,
        "startDate": event.start_date.isoformat() if event.start_date else None,
        "endDate": event.end_date.isoformat() if event.end_date else None,
        "timezone": event.timezone,
        "published": event.published,
        "status": event.status,
        "venue": _venue_or_location(event),
    }

    if event.event_dates:
        result["eventDates"] = [_format_event_date(d) for d in event.event_dates]
    else:
        result["eventDates"] = []

    return result


def _format_buyer(order: Order) -> dict[str, Any]:
    if order.buyer is None:
        return {}
    return {
        "firstName": order.buyer.first_name,
        "lastName": order.buyer.last_name,
        "email": order.buyer.email,
    }


def _format_ticket_type_breakdown(tt: TicketTypeBreakdown) -> dict[str, Any]:
    return {
        "ticketTypeId": tt.ticket_type_id,
        "ticketTypeName": tt.ticket_type_name,
        "quantity": tt.quantity,
        "unitPrice": tt.unit_price,
        "totalPrice": tt.total_price,
    }


def format_order(order: Order, *, raw: bool = False) -> dict[str, Any]:
    """Return a trimmed order dictionary without payment processor internals."""
    if raw:
        return order.model_dump(by_alias=True, exclude_none=False)

    result: dict[str, Any] = {
        "id": order.id,
        "orderNumber": order.order_number,
        "status": order.status,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
        "currency": order.currency,
        "total": order.total,
        "quantity": order.quantity,
        "buyer": _format_buyer(order),
    }

    if order.ticket_types:
        result["ticketTypes"] = [
            _format_ticket_type_breakdown(tt) for tt in order.ticket_types
        ]
    else:
        result["ticketTypes"] = []

    return result


def _format_attendee(ticket: Ticket) -> dict[str, Any]:
    if ticket.attendee is None:
        return {}
    return {
        "firstName": ticket.attendee.first_name,
        "lastName": ticket.attendee.last_name,
        "email": ticket.attendee.email,
    }


def _format_order_reference(ticket: Ticket) -> dict[str, Any]:
    if ticket.order is None:
        return {}
    return {
        "id": ticket.order.id,
        "orderNumber": ticket.order.order_number,
    }


def _format_additional_answers(ticket: Ticket) -> list[dict[str, Any]]:
    if not ticket.additional_answers:
        return []
    return [
        {"question": answer.question, "answer": answer.answer}
        for answer in ticket.additional_answers
    ]


def format_ticket(ticket: Ticket, *, raw: bool = False) -> dict[str, Any]:
    """Return a trimmed ticket dictionary."""
    if raw:
        return ticket.model_dump(by_alias=True, exclude_none=False)

    return {
        "id": ticket.id,
        "ticketTypeName": ticket.ticket_type_name,
        "status": ticket.status,
        "attendee": _format_attendee(ticket),
        "order": _format_order_reference(ticket),
        "checkedIn": ticket.checked_in,
        "additionalAnswers": _format_additional_answers(ticket),
    }


def _format_check_in_entry(entry: CheckInCountEntry) -> dict[str, Any]:
    return {
        "ticketTypeId": entry.ticket_type_id,
        "ticketTypeName": entry.ticket_type_name,
        "checkedIn": entry.checked_in,
        "sold": entry.sold,
    }


def format_check_in_count(count: CheckInCount, *, raw: bool = False) -> dict[str, Any]:
    """Return a check-in count dictionary.

    The BETA check-in-count endpoint is treated as verbatim because its shape
    may change without notice.
    """
    if raw:
        return count.model_dump(by_alias=True, exclude_none=False)

    return {
        "totalCheckedIn": count.total_checked_in,
        "totalSold": count.total_sold,
        "byTicketType": [_format_check_in_entry(e) for e in count.by_ticket_type],
    }
