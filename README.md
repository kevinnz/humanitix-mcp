# humanitix-mcp

A read-only MCP (Model Context Protocol) server for the Humanitix Public API.

This repository contains the **Phase 1 scaffold** plus the completed
**Phase 2 HTTP client** and **Phase 3 response models / formatting helpers**.
The actual MCP server, tool definitions, and FastMCP wiring are planned for
Phase 4 and are not yet implemented.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) and requires Python 3.11+.

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` and add a real API key when available:

```bash
cp .env.example .env
```

The client reads `HUMANITIX_API_KEY` and `HUMANITIX_BASE_URL` from the
environment. `HUMANITIX_BASE_URL` defaults to `https://api.humanitix.com`.

## Project structure

- `src/humanitix_mcp/` — Python package.
  - `server.py` — MCP server entry point (Phase 4, placeholder).
  - `client.py` — Async Humanitix API HTTP client with auth, retry, and
    pagination (Phase 2).
  - `models.py` — Pydantic response models (Phase 3).
  - `formatting.py` — Deterministic response trimming helpers with a raw-payload
    opt-in escape hatch (Phase 3).
- `tests/` — Test suite.
- `tests/fixtures/` — Redacted JSON fixtures for model and formatting tests.

## Running tests

```bash
uv run pytest
```

## Implementation status

- [x] Phase 1: Scaffold
- [x] Phase 2: HTTP client (`client.py`)
- [x] Phase 3: Response models and trimming (`models.py`, `formatting.py`)
- [ ] Phase 4: MCP server and tools (`server.py`)
- [ ] Phase 5: Live smoke test


## License

MIT
