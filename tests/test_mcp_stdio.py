"""End-to-end MCP stdio transport contract tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


FAKE_API_KEY = "fake-stdio-api-key-redacted"
EXPECTED_TOOL_NAMES = [
    "humanitix_list_events",
    "humanitix_get_event",
    "humanitix_list_event_dates",
    "humanitix_list_orders",
    "humanitix_get_order",
    "humanitix_list_tickets",
    "humanitix_get_ticket",
    "humanitix_get_check_in_count",
    "humanitix_sales_summary",
]
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVER_COMMAND = str(Path(sys.executable).with_name("humanitix-mcp"))


@contextmanager
def _temporary_dotenv(contents: str) -> Iterator[Path]:
    """Create a short-lived dotenv file inside the repository, never in /tmp."""
    with tempfile.TemporaryDirectory(
        dir=REPOSITORY_ROOT,
        prefix=".mcp-stdio-test-",
    ) as directory:
        dotenv_path = Path(directory) / "server.env"
        dotenv_path.write_text(contents, encoding="utf-8")
        yield dotenv_path


def _clean_child_environment(dotenv_path: Path) -> dict[str, str]:
    """Return the only environment settings required by the child server."""
    home = dotenv_path.parent / "home"
    home.mkdir()
    return {
        "HOME": str(home),
        "HUMANITIX_DOTENV_PATH": str(dotenv_path),
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
    }


async def _respond_to_events_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    requests: list[tuple[str, str, dict[str, list[str]], dict[str, str]]],
) -> None:
    try:
        raw_request = await reader.readuntil(b"\r\n\r\n")
        request_line, *header_lines = raw_request.decode("ascii").split("\r\n")
        method, target, _ = request_line.split(" ", 2)
        headers = {
            name.lower(): value.strip()
            for line in header_lines
            if line
            for name, value in [line.split(":", 1)]
        }
        target_url = urlsplit(target)
        requests.append((method, target_url.path, parse_qs(target_url.query), headers))

        body = json.dumps(
            {
                "events": [
                    {
                        "_id": "evt-redacted",
                        "name": "REDACTED EVENT",
                        "slug": "redacted-event",
                        "timezone": "Pacific/Auckland",
                        "published": False,
                        "dates": [],
                    }
                ],
                "total": 1,
                "page": 1,
                "pageSize": 100,
            }
        ).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_stdio_transport_initializes_lists_tools_and_calls_server(
    capfd: pytest.CaptureFixture[str],
) -> None:
    requests: list[tuple[str, str, dict[str, list[str]], dict[str, str]]] = []
    http_server = await asyncio.start_server(
        lambda reader, writer: _respond_to_events_request(reader, writer, requests),
        host="127.0.0.1",
        port=0,
    )
    port = http_server.sockets[0].getsockname()[1]
    try:
        with _temporary_dotenv(
            f"HUMANITIX_API_KEY={FAKE_API_KEY}\n"
            f"HUMANITIX_BASE_URL=http://127.0.0.1:{port}\n"
        ) as dotenv_path:
            parameters = StdioServerParameters(
                command=SERVER_COMMAND,
                cwd=REPOSITORY_ROOT,
                env=_clean_child_environment(dotenv_path),
            )
            async with asyncio.timeout(15):
                async with stdio_client(parameters) as streams:
                    async with ClientSession(*streams) as session:
                        initialized = await session.initialize()
                        assert initialized.server_info.name == "humanitix-mcp"

                        tools = (await session.list_tools()).tools
                        assert [tool.name for tool in tools] == EXPECTED_TOOL_NAMES
                        for tool in tools:
                            assert tool.description
                            assert tool.input_schema["type"] == "object"
                            assert isinstance(tool.input_schema["properties"], dict)
                        assert tools[0].input_schema["properties"]["page_size"]["type"] == "integer"
                        assert tools[1].input_schema["required"] == ["event_id"]

                        result = await session.call_tool(
                            "humanitix_list_events",
                            {"max_items": 1},
                        )

        assert result.is_error is False
        assert json.loads(result.content[0].text) == {
            "success": True,
            "items": [
                {
                    "id": "evt-redacted",
                    "name": "REDACTED EVENT",
                    "slug": "redacted-event",
                    "startDate": None,
                    "endDate": None,
                    "timezone": "Pacific/Auckland",
                    "published": False,
                    "status": None,
                    "venue": {},
                    "eventDates": [],
                }
            ],
        }
        assert len(requests) == 1
        method, path, query, headers = requests[0]
        assert (method, path, query) == (
            "GET",
            "/v1/events",
            {"pageSize": ["100"], "page": ["1"]},
        )
        assert headers["x-api-key"] == FAKE_API_KEY
        assert headers["accept"] == "application/json"
        protocol_output = initialized.model_dump_json() + json.dumps(
            [tool.model_dump(by_alias=True) for tool in tools]
        ) + result.model_dump_json()
        assert FAKE_API_KEY not in protocol_output
        assert FAKE_API_KEY not in capfd.readouterr().err
    finally:
        http_server.close()
        await http_server.wait_closed()


@pytest.mark.asyncio
async def test_stdio_transport_reports_missing_api_key_after_initialization(
    capfd: pytest.CaptureFixture[str],
) -> None:
    with _temporary_dotenv("HUMANITIX_BASE_URL=http://127.0.0.1:1\n") as dotenv_path:
        parameters = StdioServerParameters(
            command=SERVER_COMMAND,
            cwd=REPOSITORY_ROOT,
            env=_clean_child_environment(dotenv_path),
        )
        async with asyncio.timeout(15):
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    assert [tool.name for tool in (await session.list_tools()).tools] == EXPECTED_TOOL_NAMES
                    result = await session.call_tool("humanitix_list_events", {})

    assert result.is_error is True
    assert json.loads(result.content[0].text) == {
        "success": False,
        "error": "Server configuration error: HUMANITIX_API_KEY is not set.",
    }
    assert FAKE_API_KEY not in result.model_dump_json()
    assert FAKE_API_KEY not in capfd.readouterr().err
