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

    assert trimmed["id"] == "evt_12345"
    assert trimmed["name"] == "CHCon 2026"
    assert trimmed["slug"] == "chcon-2026"
    assert trimmed["timezone"] == "Pacific/Auckland"
    assert trimmed["published"] is True
    assert trimmed["venue"] == {
        "name": "Christchurch Town Hall",
        "address": "86 Kilmore Street",
        "city": "Christchurch",
        "country": "New Zealand",
    }
    assert len(trimmed["eventDates"]) == 2
    assert trimmed["eventDates"][0]["id"] == "ed_111"
    assert "description" not in trimmed
    assert "internalField" not in trimmed


def test_format_event_omits_payment_internal_fields() -> None:
    event = Event.model_validate(load_fixture("event.json"))
    trimmed = format_event(event)
    assert "internalField" not in trimmed
    assert "latitude" not in trimmed.get("venue", {})


def test_format_event_raw_escape_hatch() -> None:
    event = Event.model_validate(load_fixture("event.json"))
    raw = format_event(event, raw=True)
    assert raw["internalField"] == "should be ignored"
    assert "description" in raw


def test_format_order_retains_expected_fields() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    trimmed = format_order(order)

    assert trimmed["id"] == "ord_98765"
    assert trimmed["orderNumber"] == "CHC-2026-0001"
    assert trimmed["status"] == "complete"
    assert trimmed["currency"] == "NZD"
    assert trimmed["total"] == 450.0
    assert trimmed["quantity"] == 3
    assert trimmed["buyer"] == {
        "firstName": "Alex",
        "lastName": "Organiser",
        "email": "alex@example.com",
    }
    assert len(trimmed["ticketTypes"]) == 2
    assert trimmed["ticketTypes"][0]["ticketTypeName"] == "General Admission"


def test_format_order_omits_payment_internals() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    trimmed = format_order(order)
    assert "paymentProcessorId" not in trimmed
    assert "paymentMethod" not in trimmed
    assert "internalNotes" not in trimmed


def test_format_order_raw_escape_hatch() -> None:
    order = Order.model_validate(load_fixture("order.json"))
    raw = format_order(order, raw=True)
    assert raw["paymentProcessorId"] == "pi_secret"
    assert raw["paymentMethod"] == "card"


def test_format_ticket_retains_expected_fields() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    trimmed = format_ticket(ticket)

    assert trimmed["id"] == "tkt_11111"
    assert trimmed["ticketTypeName"] == "General Admission"
    assert trimmed["status"] == "complete"
    assert trimmed["checkedIn"] is False
    assert trimmed["attendee"] == {
        "firstName": "Sam",
        "lastName": "Attendee",
        "email": "sam@example.com",
    }
    assert trimmed["order"] == {"id": "ord_98765", "orderNumber": "CHC-2026-0001"}
    assert trimmed["additionalAnswers"] == [
        {"question": "Dietary requirements", "answer": "Vegetarian"},
        {"question": "T-shirt size", "answer": "L"},
    ]


def test_format_ticket_omits_internal_fields() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    trimmed = format_ticket(ticket)
    assert "barcode" not in trimmed
    assert "internalField" not in trimmed


def test_format_ticket_raw_escape_hatch() -> None:
    ticket = Ticket.model_validate(load_fixture("ticket.json"))
    raw = format_ticket(ticket, raw=True)
    assert raw["barcode"] == "secret-barcode"


def test_format_check_in_count_verbatim() -> None:
    count = CheckInCount.model_validate(load_fixture("check-in-count.json"))
    trimmed = format_check_in_count(count)

    assert trimmed["totalCheckedIn"] == 42
    assert trimmed["totalSold"] == 300
    assert len(trimmed["byTicketType"]) == 2
    assert trimmed["byTicketType"][0]["ticketTypeName"] == "General Admission"
    assert trimmed["byTicketType"][0]["checkedIn"] == 30


def test_format_check_in_count_raw_escape_hatch() -> None:
    count = CheckInCount.model_validate(load_fixture("check-in-count.json"))
    raw = format_check_in_count(count, raw=True)
    assert raw["betaField"] == "may change"


@pytest.mark.parametrize(
    "formatter,model_cls,fixture",
    [
        (format_event, Event, "event.json"),
        (format_order, Order, "order.json"),
        (format_ticket, Ticket, "ticket.json"),
        (format_check_in_count, CheckInCount, "check-in-count.json"),
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
    assert set(trimmed.keys()) <= set(raw.keys())
    assert len(trimmed) <= len(raw)
