"""Tests for deterministic response trimming helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_format_event_retains_expected_fields() -> None:
    event = Event.model_validate(load_fixture("event.json"))
    trimmed = format_event(event)

    assert trimmed["id"] == "PUBLIC_CHCON_EVENT_ID"
    assert trimmed["name"] == "CHCon 2026"
    assert trimmed["slug"] == "chcon-2026"
    assert trimmed["timezone"] == "Pacific/Auckland"
    assert trimmed["published"] is True
    assert trimmed["venue"] == {}
    assert len(trimmed["eventDates"]) == 1
    assert trimmed["eventDates"][0]["id"] == "PUBLIC_CHCON_EVENT_DATE_ID"
    assert "unmodeledSafeField" not in trimmed


def test_format_event_omits_payment_internal_fields() -> None:
    event = Event.model_validate(load_fixture("event.json"))
    trimmed = format_event(event)
    assert "unmodeledSafeField" not in trimmed
    assert "latitude" not in trimmed.get("venue", {})


def test_format_event_raw_escape_hatch() -> None:
    event = Event.model_validate(load_fixture("event.json"))
    raw = format_event(event, raw=True)
    assert raw["unmodeledSafeField"] == "redacted"


def test_format_event_handles_documented_live_location_variants() -> None:
    event = Event.model_validate(
        {
            "_id": "SYNTHETIC_EVENT_ID",
            "location": "NZ",
            "eventLocation": {
                "venueName": "Redacted venue",
                "city": "Redacted city",
                "country": "NZ",
            },
            "dates": [{"_id": "SYNTHETIC_EVENT_DATE_ID"}],
        },
    )

    assert format_event(event) == {
        "id": "SYNTHETIC_EVENT_ID",
        "name": None,
        "slug": None,
        "startDate": None,
        "endDate": None,
        "timezone": None,
        "published": None,
        "status": None,
        "venue": {
            "name": "Redacted venue",
            "city": "Redacted city",
            "country": "NZ",
        },
        "eventDates": [
            {
                "id": "SYNTHETIC_EVENT_DATE_ID",
                "name": None,
                "startDate": None,
                "endDate": None,
            },
        ],
    }


def test_format_order_retains_expected_fields() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    trimmed = format_order(order)

    assert trimmed["id"] == "REDACTED_ORDER_ID"
    assert trimmed["orderNumber"] is None
    assert trimmed["status"] == "complete"
    assert trimmed["currency"] == "NZD"
    assert trimmed["total"] == 0
    assert trimmed["quantity"] == 0
    assert trimmed["buyer"] == {}
    assert len(trimmed["ticketTypes"]) == 1
    assert trimmed["ticketTypes"][0]["ticketTypeName"] == "REDACTED_TICKET_TYPE"


def test_format_order_omits_payment_internals() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    trimmed = format_order(order)
    assert "unmodeledSafeField" not in trimmed


def test_format_order_raw_escape_hatch() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    raw = format_order(order, raw=True)
    assert raw["unmodeledSafeField"] == "redacted"


def test_format_ticket_retains_expected_fields() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    trimmed = format_ticket(ticket)

    assert trimmed["id"] == "REDACTED_TICKET_ID"
    assert trimmed["ticketTypeName"] == "REDACTED_TICKET_TYPE"
    assert trimmed["status"] == "complete"
    assert trimmed["checkedIn"] is False
    assert trimmed["attendee"] == {}
    assert trimmed["order"] == {}
    assert trimmed["additionalAnswers"] == []


def test_format_ticket_omits_internal_fields() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    trimmed = format_ticket(ticket)
    assert "unmodeledSafeField" not in trimmed


def test_format_ticket_raw_escape_hatch() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    raw = format_ticket(ticket, raw=True)
    assert raw["unmodeledSafeField"] == "redacted"


def test_format_check_in_count_preserves_documented_response() -> None:
    count = CheckInCount.model_validate(load_fixture("check-in-count-response.json"))
    trimmed = format_check_in_count(count)

    assert trimmed == {
        "eventId": "615270c8730a430b4cc5d28a",
        "eventDateId": "615270c8730a430b4cc5d293",
        "checkedIn": 0,
        "ticketTypes": [
            {
                "ticketTypeId": "REDACTED_TICKET_TYPE_ID",
                "ticketTypeName": "REDACTED_TICKET_TYPE",
                "checkedIn": 0,
            },
        ],
    }


def test_format_check_in_count_normalizes_legacy_aliases() -> None:
    count = CheckInCount.model_validate(load_fixture("check-in-count.json"))

    assert format_check_in_count(count) == {
        "eventId": None,
        "eventDateId": None,
        "checkedIn": 0,
        "ticketTypes": [
            {
                "ticketTypeId": "REDACTED_TICKET_TYPE_ID",
                "ticketTypeName": "REDACTED_TICKET_TYPE",
                "checkedIn": 0,
            },
        ],
    }


def test_format_check_in_count_raw_escape_hatch() -> None:
    count = CheckInCount.model_validate(load_fixture("check-in-count.json"))
    raw = format_check_in_count(count, raw=True)
    assert raw["unmodeledSafeField"] == "redacted"


@pytest.mark.parametrize(
    "formatter,model_cls,fixture",
    [
        (format_event, Event, "event.json"),
        (format_order, Order, "order.json"),
        (format_ticket, Ticket, "ticket.json"),
        (format_check_in_count, CheckInCount, "check-in-count-response.json"),
    ],
)
def test_trimmed_output_has_no_uncontrolled_key_expansion(
    formatter: callable,
    model_cls: type,
    fixture: str,
) -> None:
    instance = model_cls.model_validate(load_fixture(fixture))
    trimmed = formatter(instance)
    raw = formatter(instance, raw=True)
    raw_aliases = {"id": "_id", "eventDates": "dates"}
    assert all(
        key in raw or raw_aliases.get(key) in raw
        for key in trimmed
    )
    assert len(trimmed) <= len(raw)
