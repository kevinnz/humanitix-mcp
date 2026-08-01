# humanitix-mcp

A read-only MCP (Model Context Protocol) server for the Humanitix Public API.

This repository currently contains the **Phase 1 scaffold**: package layout,
project metadata, dependencies, and placeholder modules. The actual MCP server,
API client, Pydantic models, and formatting logic will be implemented in later
phases.

## Setup

This project uses [uv](https://docs.astral.sh/uv/) and requires Python 3.11+.

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` and add a real API key when available:

```bash
cp .env.example .env
```

## Project structure

- `src/humanitix_mcp/` — Python package.
  - `server.py` — MCP server entry point (Phase 2).
  - `client.py` — Humanitix API HTTP client (Phase 3).
  - `models.py` — Pydantic response models (Phase 3).
  - `formatting.py` — Response formatting helpers (Phase 4).
- `tests/` — Test suite.
- `tests/fixtures/` — Test fixtures.

## License

MIT
