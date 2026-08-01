"""Pydantic response models for the Humanitix Public API (Phase 3)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HumanitixModel(BaseModel):
    """Base model that tolerates unknown API fields.

    Unknown fields are retained on the instance so that the raw-payload escape
    hatch can return them, but formatting helpers deliberately select only the
    stable, trimmed fields for normal output.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Pagination(HumanitixModel):
    """Pagination envelope returned by list endpoints."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    page: int | None = None
    page_size: int | None = Field(default=None, alias="pageSize")


class EventDate(HumanitixModel):
    """A single date within a multi-date event."""

    id: str | None = None
    name: str | None = None
    start_date: datetime | None = Field(default=None, alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    door_time: datetime | None = Field(default=None, alias="doorTime")


class Venue(HumanitixModel):
    """Venue / location information for an event."""

    name: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None


class Location(HumanitixModel):
    """Event location wrapper."""

    venue: Venue | None = None
    latitude: float | None = None
    longitude: float | None = None


class EventClassification(HumanitixModel):
    """Event classification metadata."""

    type: str | None = None
    category: str | None = None
    subcategory: str | None = None


class Event(HumanitixModel):
    """A Humanitix event."""

    id: str | None = None
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    start_date: datetime | None = Field(default=None, alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    timezone: str | None = None
    published: bool | None = None
    status: str | None = None
    location: Location | None = None
    venue: Venue | None = None
    classification: EventClassification | None = None
    event_dates: list[EventDate] = Field(default_factory=list, alias="eventDates")


class TicketTypeBreakdown(HumanitixModel):
    """Summary of tickets sold for a single ticket type within an order."""

    ticket_type_id: str | None = Field(default=None, alias="ticketTypeId")
    ticket_type_name: str | None = Field(default=None, alias="ticketTypeName")
    quantity: int | None = None
    unit_price: float | None = Field(default=None, alias="unitPrice")
    total_price: float | None = Field(default=None, alias="totalPrice")


class Buyer(HumanitixModel):
    """Buyer / contact details attached to an order."""

    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    email: str | None = None
    phone: str | None = None


class Order(HumanitixModel):
    """A ticket order."""

    id: str | None = None
    order_number: str | None = Field(default=None, alias="orderNumber")
    status: str | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    currency: str | None = None
    total: float | None = None
    quantity: int | None = None
    buyer: Buyer | None = None
    ticket_types: list[TicketTypeBreakdown] = Field(
        default_factory=list,
        alias="ticketTypes",
    )


class Attendee(HumanitixModel):
    """Attendee details on a ticket."""

    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    email: str | None = None


class AdditionalAnswer(HumanitixModel):
    """Answer to an additional question asked during checkout."""

    question: str | None = None
    answer: str | None = None


class TicketOrderReference(HumanitixModel):
    """Reference to the order that owns a ticket."""

    id: str | None = None
    order_number: str | None = Field(default=None, alias="orderNumber")


class Ticket(HumanitixModel):
    """A single ticket / attendee record."""

    id: str | None = None
    ticket_type_id: str | None = Field(default=None, alias="ticketTypeId")
    ticket_type_name: str | None = Field(default=None, alias="ticketTypeName")
    status: str | None = None
    checked_in: bool | None = Field(default=None, alias="checkedIn")
    check_in_time: datetime | None = Field(default=None, alias="checkInTime")
    attendee: Attendee | None = None
    order: TicketOrderReference | None = None
    event_date_id: str | None = Field(default=None, alias="eventDateId")
    additional_answers: list[AdditionalAnswer] = Field(
        default_factory=list,
        alias="additionalAnswers",
    )


class CheckInCountEntry(HumanitixModel):
    """Check-in total for a single ticket type."""

    ticket_type_id: str | None = Field(default=None, alias="ticketTypeId")
    ticket_type_name: str | None = Field(default=None, alias="ticketTypeName")
    checked_in: int | None = Field(default=None, alias="checkedIn")
    sold: int | None = None


class CheckInCount(HumanitixModel):
    """Check-in count totals for an event date.

    .. warning::
        The ``/check-in-count`` endpoint is BETA and its response shape may
        change without notice.
    """

    total_checked_in: int | None = Field(default=None, alias="totalCheckedIn")
    total_sold: int | None = Field(default=None, alias="totalSold")
    by_ticket_type: list[CheckInCountEntry] = Field(
        default_factory=list,
        alias="byTicketType",
    )
