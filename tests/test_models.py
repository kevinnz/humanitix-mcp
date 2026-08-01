"""Pydantic model validation tests using redacted fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from humanitix_mcp.models import (
    CheckInCount,
    Event,
    EventDate,
    Order,
    Ticket,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


def test_event_model_parses_fixture() -> None:
    data = load_fixture("event.json")
    event = Event.model_validate(data)

    assert event.id == "evt_12345"
    assert event.name == "CHCon 2026"
    assert event.slug == "chcon-2026"
    assert event.published is True
    assert event.timezone == "Pacific/Auckland"
    assert len(event.event_dates) == 2
    assert all(isinstance(d, EventDate) for d in event.event_dates)
    assert event.event_dates[0].id == "ed_111"
    assert event.location is not None
    assert event.location.venue is not None
    assert event.location.venue.name == "Christchurch Town Hall"


def test_event_model_retains_unknown_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("event.json")
    event = Event.model_validate(data)
    assert event.model_dump()["internalField"] == "should be ignored"


def test_order_model_parses_fixture() -> None:
    data = load_fixture("order.json")
    order = Order.model_validate(data)

    assert order.id == "ord_98765"
    assert order.order_number == "CHC-2026-0001"
    assert order.status == "complete"
    assert order.currency == "NZD"
    assert order.total == 450.0
    assert order.quantity == 3
    assert order.buyer is not None
    assert order.buyer.email == "alex@example.com"
    assert len(order.ticket_types) == 2
    assert order.ticket_types[0].ticket_type_name == "General Admission"
    assert order.ticket_types[0].quantity == 2


def test_order_model_retains_payment_internals_for_raw_escape_hatch() -> None:
    data = load_fixture("order.json")
    order = Order.model_validate(data)
    dumped = order.model_dump()
    assert dumped["paymentProcessorId"] == "pi_secret"
    assert dumped["paymentMethod"] == "card"
    assert dumped["internalNotes"] == "do not surface"


def test_ticket_model_parses_fixture() -> None:
    data = load_fixture("ticket.json")
    ticket = Ticket.model_validate(data)

    assert ticket.id == "tkt_11111"
    assert ticket.ticket_type_name == "General Admission"
    assert ticket.status == "complete"
    assert ticket.checked_in is False
    assert ticket.attendee is not None
    assert ticket.attendee.first_name == "Sam"
    assert ticket.order is not None
    assert ticket.order.order_number == "CHC-2026-0001"
    assert len(ticket.additional_answers) == 2
    assert ticket.additional_answers[0].question == "Dietary requirements"
    assert ticket.additional_answers[0].answer == "Vegetarian"


def test_ticket_model_retains_internal_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("ticket.json")
    ticket = Ticket.model_validate(data)
    dumped = ticket.model_dump()
    assert dumped["barcode"] == "secret-barcode"
    assert dumped["internalField"] == "ignored"


def test_check_in_count_model_parses_fixture() -> None:
    data = load_fixture("check-in-count.json")
    count = CheckInCount.model_validate(data)

    assert count.total_checked_in == 42
    assert count.total_sold == 300
    assert len(count.by_ticket_type) == 2
    assert count.by_ticket_type[0].ticket_type_name == "General Admission"
    assert count.by_ticket_type[0].checked_in == 30
    assert count.by_ticket_type[0].sold == 200


def test_check_in_count_model_retains_unknown_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("check-in-count.json")
    count = CheckInCount.model_validate(data)
    assert count.model_dump()["betaField"] == "may change"


@pytest.mark.parametrize(
    "model_cls,fixture",
    [
        (Event, "event.json"),
        (Order, "order.json"),
        (Ticket, "ticket.json"),
        (CheckInCount, "check-in-count.json"),
    ],
)
def test_all_models_round_trip(model_cls: type, fixture: str) -> None:
    data = load_fixture(fixture)
    instance = model_cls.model_validate(data)
    assert instance.model_validate(instance.model_dump()) is not None
