# humanitix-mcp development instructions

## Commands

- Install development dependencies: `uv sync --extra dev`
- Run the full suite: `uv run pytest`
- Run one test module: `uv run pytest tests/test_client.py`
- Run one test: `uv run pytest tests/test_client.py::test_pagination_yields_all_pages`
- Run the stdio server: `uv run humanitix-mcp`

During an edit cycle, run the affected test module after a cohesive change.
Run the full suite once for final validation rather than repeatedly alongside
overlapping targeted runs.

## Architecture

- `src/humanitix_mcp/client.py` owns asynchronous, read-only HTTP access:
  authentication, timeouts, retry behavior, pagination, throttling, and
  Humanitix-specific exceptions.
- `src/humanitix_mcp/models.py` provides Pydantic models. Models retain
  unknown API fields so `raw=true` can return the original payload shape.
- `src/humanitix_mcp/formatting.py` trims models into stable MCP responses.
  Normal tool output must use these helpers; raw output is opt-in only.
- `src/humanitix_mcp/server.py` owns the stdio MCP server, tool schemas,
  argument validation, client lifecycle, structured error responses, and the
  sales-summary composite tool.
- Tests use `pytest-asyncio` and `respx`; redacted fixtures live under
  `tests/fixtures/`.

## Humanitix API contract

Before changing the HTTP client, MCP tool schemas, or API-facing tests, fetch
the current OpenAPI document at
`https://api.humanitix.com/v1/documentation/json`. Treat it as the source of
truth for endpoint paths, query versus path parameters, response collection
keys, and supported status values. Do not rely on plan documents or existing
tests when they conflict with the live contract.

`respx` tests must mirror that contract: assert the exact request path and
query parameters and use the documented response envelope. In particular,
list endpoints use endpoint-specific collection fields rather than a generic
`items` field. Update client and server tests together when the contract
changes.

## Repository policies

- The MCP server is strictly read-only. Do not expose Humanitix write,
  transfer, check-in, or check-out endpoints.
- Never log, return, fixture, stage, or commit a real API key or attendee PII.
  Keep fixtures redacted.
- `plan/` is intentionally local-only and gitignored. Never stage, commit, or
  push files from that directory.
