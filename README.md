# humanitix-mcp

A read-only MCP (Model Context Protocol) server for the Humanitix Public API.

This repository contains the completed **Phase 1-5** implementation: a `uv`
project, an async read-only Humanitix HTTP client, Pydantic response models,
deterministic trimmed formatting helpers, and a stdio MCP server that exposes
nine read-only tools.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) and requires Python 3.11+.

```bash
uv sync --extra dev
```

Create a Humanitix Public API key in **Account → Advanced → Public API key**,
following the [official Humanitix Public API
documentation](https://help.humanitix.com/en/articles/8888275-public-api-documentation).
Generating a replacement key invalidates the prior key. Copy `.env.example` to
`.env` and add the key locally:

```bash
cp .env.example .env
```

At startup, the server loads the project-local `.env`. Explicitly set
`HUMANITIX_API_KEY` and `HUMANITIX_BASE_URL` environment variables take
precedence; `HUMANITIX_BASE_URL` otherwise defaults to
`https://api.humanitix.com`. `HUMANITIX_DOTENV_PATH` is an advanced explicit
dotenv-path override, primarily for test or custom setups. Never put an API
key in MCP configuration, source control, logs, or issue comments.

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

### Claude Desktop configuration

Add this secret-free server entry to Claude Desktop's MCP configuration:

```json
{
  "mcpServers": {
    "humanitix": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/kevin/Documents/GitHub/humanitix-mcp",
        "run",
        "humanitix-mcp"
      ]
    }
  }
}
```

The credential stays solely in the gitignored project-local `.env`, never in
the Claude Desktop configuration. The server loads that file when started by
the command above.

### Claude Code CLI project configuration

This separate, project-scoped Claude Code CLI `.mcp.json` configuration uses a
secret-free shell wrapper that, from Claude's project-root working directory,
exports variables from the local `.env` and then executes `uv run
humanitix-mcp`. It contains no machine-specific paths, so a clone works after
creating its own `.env`; the key remains only in that untracked local
environment file. Check the Claude Code configuration with:

```bash
claude mcp list
```

The Claude-mediated `humanitix_sales_summary` verification is **not yet
complete**: the local Claude CLI returned OAuth 401 before it made an MCP call.
Re-authenticate the local Claude CLI using its normal login flow, confirm
`claude mcp list` again, then retry the sales-summary call. This is separate
from Humanitix API-key authentication.

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
| `humanitix_list_events` | List events with optional future-only, `since`, and `override_location` filters. |
| `humanitix_get_event` | Fetch one event by `event_id`. |
| `humanitix_list_event_dates` | List dates/sessions for an `event_id`. |
| `humanitix_list_orders` | List orders for an `event_id` and `event_date_id`. |
| `humanitix_get_order` | Fetch one order by `event_id` and `order_id`. |
| `humanitix_list_tickets` | List tickets for an `event_id` and `event_date_id`, optionally filtered by `status`. |
| `humanitix_get_ticket` | Fetch one ticket by `event_id` and `ticket_id`. |
| `humanitix_get_check_in_count` | Get check-in totals for an `event_id` and `event_date_id` (BETA endpoint). |
| `humanitix_sales_summary` | Summarise tickets for an `event_id` and `event_date_id`. |

Tag tools are intentionally not included: the live CHCon account returned no
tags when queried with a read-only key on 15 August 2026.

All list tools support `page_size` (1-100) and `max_items` (default 500). All
data-returning tools support `raw=true` to return the full Humanitix payload
instead of the trimmed summary.

`override_location` is forwarded to Humanitix as `overrideLocation`. Use it
when events are associated with a different market (for example, an NZ
account); the default location filter can legitimately omit an event.

## Verified public CHCon 2025 smoke target

The opt-in, read-only smoke test verified this public event identifier on 15
August 2026:

- Event: `6875f6fd6e7ba808d7baa311`

The direct API title expands the CHCon name as “Christchurch Hacker Con and
Training Day 2025”; the smoke test accepts that expanded public name or a
literal case-insensitive `chcon` name and derives date identifiers at runtime.
It exercised every read-only tool, including raw structural identifier checks
for order and ticket lists. The BETA check-in endpoint is unstable: it may
return a structured 5xx service outcome even when the tool request is valid.

## Troubleshooting

- **Humanitix 401:** Confirm `HUMANITIX_API_KEY` is set in local `.env`, was
  obtained from the official key page above, and has not been replaced; then
  restart the MCP client. Do not share the key while investigating.
- **Missing events:** Try the appropriate `override_location`; Humanitix
  applies location filtering through `overrideLocation`.
- **Cold starts:** The first `uv run` or MCP request can take longer while the
  environment starts. Wait for the stdio client to connect and retry rather
  than treating a startup delay as an API failure.
- **Empty results:** An empty event, order, ticket, or tag collection can be a
  valid account/data-availability result. The smoke account had zero tags, so
  no tag tools are exposed.
- **BETA check-in:** Treat a structured 5xx result from
  `humanitix_get_check_in_count` as an unstable service outcome, not a reason
  to retry with altered identifiers.
- **Claude OAuth 401:** This is authentication to the local Claude CLI, not
  the Humanitix API. Re-authenticate Claude, verify `claude mcp list`, and
  retry the tool call.

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
- [x] Phase 5: Live smoke test (15 August 2026)
- [ ] Phase 6: Claude Cowork sales-summary verification (blocked on local
      Claude OAuth 401 before an MCP call)
- [x] Phase 7: Documentation

## License

MIT
