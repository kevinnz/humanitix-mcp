# humanitix-mcp

A read-only MCP (Model Context Protocol) server for the Humanitix Public API.

This repository contains the completed **Phase 1-4** implementation: a `uv`
project, an async read-only Humanitix HTTP client, Pydantic response models,
deterministic trimmed formatting helpers, and a stdio MCP server that exposes
nine read-only tools. Phase 5 live smoke testing is pending a real API key.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) and requires Python 3.11+.

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` and add a real API key when available:

```bash
cp .env.example .env
```

The server reads `HUMANITIX_API_KEY` and `HUMANITIX_BASE_URL` from the
environment. `HUMANITIX_BASE_URL` defaults to `https://api.humanitix.com`.

## Running the server

The server communicates over stdio and is the MCP transport expected by
Claude Desktop and MCP Inspector:

```bash
uv run humanitix-mcp
```

With the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run humanitix-mcp
```

The server creates one `HumanitixClient` per stdio connection and closes it
when the connection ends.

## Project structure

- `src/humanitix_mcp/` — Python package.
  - `server.py` — MCP server entry point and tool handlers (Phase 4).
  - `client.py` — Async Humanitix API HTTP client with auth, retry, and
    pagination (Phase 2).
  - `models.py` — Pydantic response models (Phase 3).
  - `formatting.py` — Deterministic response trimming helpers with a raw-payload
    opt-in escape hatch (Phase 3).
- `tests/` — Test suite.
- `tests/fixtures/` — Redacted JSON fixtures for model and formatting tests.

## Tool catalog

The server exposes nine read-only tools:

| Tool | Purpose |
|------|---------|
| `humanitix_list_events` | List events with optional future-only and `since` filters. |
| `humanitix_get_event` | Fetch one event by `event_id`. |
| `humanitix_list_event_dates` | List the dates/sessions for an event; event-date IDs are required by the order, ticket, and check-in tools. |
| `humanitix_list_orders` | List orders for a specific event date. |
| `humanitix_get_order` | Fetch one order by `order_id`. |
| `humanitix_list_tickets` | List tickets for a specific event date, optionally filtered by `status`. |
| `humanitix_get_ticket` | Fetch one ticket by `ticket_id`. |
| `humanitix_get_check_in_count` | Get check-in totals for an event date (BETA endpoint). |
| `humanitix_sales_summary` | Aggregate tickets for an event date into sold/cancelled counts, gross revenue (when price data is present), and capacity utilisation (when capacity is present). |

All list tools support `page_size` (1-100) and `max_items` (default 500). All
data-returning tools support `raw=true` to return the full Humanitix payload
instead of the trimmed summary.

## Read-only boundary

The server only implements read-only tools. No write endpoints, check-in/check-out
actions, transfers, or network listeners are exposed.

## Structured errors

Expected failures are returned as JSON dictionaries with `success: false` and a
human-readable `error` message. This covers missing configuration, invalid
arguments, authentication, authorization, not-found, validation, server, and
transport failures. Error messages never include the API key or raw tracebacks.

## Running tests

```bash
uv run pytest
```

## Implementation status

- [x] Phase 1: Scaffold
- [x] Phase 2: HTTP client (`client.py`)
- [x] Phase 3: Response models and trimming (`models.py`, `formatting.py`)
- [x] Phase 4: MCP server and tools (`server.py`)
- [ ] Phase 5: Live smoke test (requires a real `HUMANITIX_API_KEY` and is not
      part of this phase)

## License

MIT
