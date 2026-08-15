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

    assert event.id == "PUBLIC_CHCON_EVENT_ID"
    assert event.name == "CHCon 2026"
    assert event.slug == "chcon-2026"
    assert event.published is True
    assert event.timezone == "Pacific/Auckland"
    assert len(event.event_dates) == 1
    assert all(isinstance(d, EventDate) for d in event.event_dates)
    assert event.event_dates[0].id == "PUBLIC_CHCON_EVENT_DATE_ID"
    assert event.location is None


def test_event_model_retains_unknown_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("event.json")
    event = Event.model_validate(data)
    assert event.model_dump()["unmodeledSafeField"] == "redacted"


def test_event_model_accepts_documented_live_identifier_date_and_location_shapes() -> None:
    event = Event.model_validate(
        {
            "_id": "SYNTHETIC_EVENT_ID",
            "name": "Redacted event",
            "location": "NZ",
            "dates": [
                {
                    "_id": "SYNTHETIC_EVENT_DATE_ID",
                    "startDate": "2000-01-01T00:00:00Z",
                },
            ],
            "unmodeledSafeField": "redacted",
        },
    )

    assert event.id == "SYNTHETIC_EVENT_ID"
    assert event.event_dates[0].id == "SYNTHETIC_EVENT_DATE_ID"
    assert event.location == "NZ"
    assert event.model_dump(by_alias=True)["unmodeledSafeField"] == "redacted"


def test_order_model_parses_fixture() -> None:
    data = load_fixture("order.json")
    order = Order.model_validate(data)

    assert order.id == "REDACTED_ORDER_ID"
    assert order.order_number is None
    assert order.status == "complete"
    assert order.currency == "NZD"
    assert order.total == 0
    assert order.quantity == 0
    assert order.buyer is None
    assert len(order.ticket_types) == 1
    assert order.ticket_types[0].ticket_type_name == "REDACTED_TICKET_TYPE"
    assert order.ticket_types[0].quantity == 0


def test_order_model_retains_unknown_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("order.json")
    order = Order.model_validate(data)
    dumped = order.model_dump()
    assert dumped["unmodeledSafeField"] == "redacted"


def test_ticket_model_parses_fixture() -> None:
    data = load_fixture("ticket.json")
    ticket = Ticket.model_validate(data)

    assert ticket.id == "REDACTED_TICKET_ID"
    assert ticket.ticket_type_name == "REDACTED_TICKET_TYPE"
    assert ticket.status == "complete"
    assert ticket.checked_in is False
    assert ticket.attendee is None
    assert ticket.order is None
    assert ticket.additional_answers == []


def test_ticket_model_retains_internal_fields_for_raw_escape_hatch() -> None:
    data = load_fixture("ticket.json")
    ticket = Ticket.model_validate(data)
    dumped = ticket.model_dump()
    assert dumped["unmodeledSafeField"] == "redacted"


def test_check_in_count_model_parses_documented_fixture() -> None:
    data = load_fixture("check-in-count-response.json")
    count = CheckInCount.model_validate(data)

    assert count.event_id == "615270c8730a430b4cc5d28a"
    assert count.event_date_id == "615270c8730a430b4cc5d293"
    assert count.checked_in == 0
    assert len(count.ticket_types) == 1
    assert count.ticket_types[0].ticket_type_name == "REDACTED_TICKET_TYPE"
    assert count.ticket_types[0].checked_in == 0


def test_check_in_count_model_accepts_legacy_aliases_without_overriding_documented_fields() -> None:
    data = load_fixture("check-in-count.json")
    data.update(
        {
            "eventId": "DOCUMENTED_EVENT_ID",
            "eventDateId": "DOCUMENTED_EVENT_DATE_ID",
            "checkedIn": 1,
            "ticketTypes": [
                {
                    "ticketTypeId": "DOCUMENTED_TICKET_TYPE_ID",
                    "ticketTypeName": "Documented ticket type",
                    "checkedIn": 1,
                },
            ],
        },
    )

    count = CheckInCount.model_validate(data)

    assert count.event_id == "DOCUMENTED_EVENT_ID"
    assert count.event_date_id == "DOCUMENTED_EVENT_DATE_ID"
    assert count.checked_in == 1
    assert [entry.ticket_type_id for entry in count.ticket_types] == [
        "DOCUMENTED_TICKET_TYPE_ID",
    ]
    assert count.model_dump()["unmodeledSafeField"] == "redacted"


@pytest.mark.parametrize(
    "model_cls,fixture",
    [
        (Event, "event.json"),
        (Order, "order.json"),
        (Ticket, "ticket.json"),
        (CheckInCount, "check-in-count-response.json"),
    ],
)
def test_all_models_round_trip(model_cls: type, fixture: str) -> None:
    data = load_fixture(fixture)
    instance = model_cls.model_validate(data)
    assert instance.model_validate(instance.model_dump()) is not None
