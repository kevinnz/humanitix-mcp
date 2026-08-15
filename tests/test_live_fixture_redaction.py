"""Offline validation for redacted live-smoke endpoint fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from humanitix_mcp.formatting import (
    format_check_in_count,
    format_event,
    format_order,
    format_ticket,
)
from humanitix_mcp.models import CheckInCount, Event, Order, Ticket

FIXTURES = Path(__file__).parent / "fixtures"
_BANNED_FIELD_NAMES = {
    "accesscode",
    "additionalfields",
    "additionalanswers",
    "address",
    "attendee",
    "barcode",
    "businessname",
    "businesstaxid",
    "buyer",
    "customscanningcode",
    "email",
    "firstname",
    "lastname",
    "mobile",
    "notes",
    "organisation",
    "ordername",
    "ordernumber",
    "paymentprocessorid",
    "phone",
    "qrcodedata",
    "seatinglocation",
}
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _load(name: str) -> dict[str, Any]:
    with (FIXTURES / name).open(encoding="utf-8") as fixture:
        return json.load(fixture)


def _walk(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [
            (key, nested)
            for key, nested_value in value.items()
            for key, nested in [(key, nested_value), *_walk(nested_value)]
        ]
    if isinstance(value, list):
        return [item for nested_value in value for item in _walk(nested_value)]
    return []


@pytest.mark.parametrize(
    "fixture",
    [
        "events-list.json",
        "event-response.json",
        "orders-list.json",
        "order-response.json",
        "tickets-list.json",
        "ticket-response.json",
        "check-in-count-response.json",
    ],
)
def test_live_smoke_fixture_excludes_pii_like_data(fixture: str) -> None:
    data = _load(fixture)
    keys_and_values = _walk(data)
    assert not {key.casefold() for key, _ in keys_and_values} & _BANNED_FIELD_NAMES
    assert not _EMAIL_PATTERN.search(json.dumps(data))


@pytest.mark.parametrize(
    ("fixture", "model_cls", "formatter"),
    [
        ("event.json", Event, format_event),
        ("order.json", Order, format_order),
        ("ticket.json", Ticket, format_ticket),
        ("check-in-count-response.json", CheckInCount, format_check_in_count),
    ],
)
def test_single_endpoint_fixtures_validate_and_format(
    fixture: str,
    model_cls: type,
    formatter: Any,
) -> None:
    assert formatter(model_cls.model_validate(_load(fixture)))


@pytest.mark.parametrize(
    ("fixture", "collection_key"),
    [
        ("events-list.json", "events"),
        ("orders-list.json", "orders"),
        ("tickets-list.json", "tickets"),
    ],
)
def test_live_list_endpoint_fixture_shapes(
    fixture: str,
    collection_key: str,
) -> None:
    response = _load(fixture)
    assert isinstance(response[collection_key], list)
    assert isinstance(response["total"], int)
    assert response[collection_key]
    assert "_id" in response[collection_key][0]


@pytest.mark.parametrize(
    ("fixture", "collection_key", "model_cls", "formatter", "identifier"),
    [
        (
            "orders-list.json",
            "orders",
            Order,
            format_order,
            "REDACTED_ORDER_ID",
        ),
        (
            "tickets-list.json",
            "tickets",
            Ticket,
            format_ticket,
            "REDACTED_TICKET_ID",
        ),
    ],
)
def test_live_list_identifier_aliases_are_trimmed(
    fixture: str,
    collection_key: str,
    model_cls: type,
    formatter: Any,
    identifier: str,
) -> None:
    record = _load(fixture)[collection_key][0]
    assert formatter(model_cls.model_validate(record))["id"] == identifier


@pytest.mark.parametrize(
    "fixture",
    [
        "event-response.json",
        "order-response.json",
        "ticket-response.json",
    ],
)
def test_live_single_resource_fixture_uses_openapi_identifier(fixture: str) -> None:
    assert "_id" in _load(fixture)


def test_live_check_in_fixture_uses_openapi_shape() -> None:
    response = _load("check-in-count-response.json")
    assert {"eventId", "eventDateId", "checkedIn", "ticketTypes"} <= set(response)
    assert isinstance(response["ticketTypes"], list)
