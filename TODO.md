# TODO

This file mirrors the active implementation backlog.

- [x] **Fix API endpoint paths to match Humanitix OpenAPI spec** (`fix-api-paths`)
  - The code currently calls `/v1/event-dates/{eventDateId}/orders`, `/v1/orders/{orderId}`, `/v1/event-dates/{eventDateId}/tickets`, `/v1/tickets/{ticketId}`, and `/v1/event-dates/{eventDateId}/check-in-count`. The live OpenAPI spec only defines `/v1/events/{eventId}/orders`, `/v1/events/{eventId}/orders/{orderId}`, `/v1/events/{eventId}/tickets`, `/v1/events/{eventId}/tickets/{ticketId}`, and `/v1/events/{eventId}/check-in-count`. Update `client.py` and `server.py` tool handlers to use event-scoped paths with `eventDateId` as a query parameter.

- [x] **Fix pagination response key handling** (`fix-pagination-keys`)
  - Depends on: `fix-api-paths`
  - Humanitix list endpoints return endpoint-specific keys: `events` for `/v1/events`, `orders` for `/v1/events/{eventId}/orders`, and `tickets` for `/v1/events/{eventId}/tickets`. The current `paginate()` helper assumes a single `items` key. Update it to accept or detect the result key, and update tests and mock fixtures.

- [x] **Add required `event_id` parameter to event-scoped tools** (`add-event-id-tool-args`)
  - Depends on: `fix-api-paths`
  - Update `humanitix_list_orders`, `humanitix_get_order`, `humanitix_list_tickets`, `humanitix_get_ticket`, `humanitix_get_check_in_count`, and `humanitix_sales_summary` schemas and handlers to require `event_id` and use it in API paths.

- [x] **Add `overrideLocation` support for NZ account** (`add-override-location`)
  - Depends on: `fix-api-paths`
  - Add an optional location parameter to `humanitix_list_events` (and other applicable tools) and forward it as the `overrideLocation` query parameter.

- [x] **Phase 5: Live smoke test with real API key** (`phase-5-smoke-test`)
  - Depends on: `fix-api-paths`, `fix-pagination-keys`, `add-event-id-tool-args`, `add-override-location`
  - Target public event `6875f6fd6e7ba808d7baa311`, accept its expanded direct public name (“Christchurch Hacker Con and Training Day 2025”) or a literal case-insensitive `chcon` name, derive dates directly, and exercise both default and NZ-override event lists. Inspect raw order and ticket lists without persisting records before relying on trimmed identifiers.
  - Completed on 15 August 2026: default and NZ event lists, direct event/date calls, raw structural order/ticket identifier checks, trimmed order/ticket lists and individual-get calls, sales summary, and BETA check-in all completed. No raw records, PII, or secrets were saved or logged.

- [x] **Decide whether to implement tag tools** (`tags-tools`)
  - Depends on: `phase-5-smoke-test`
  - Decided on 15 August 2026: the live `GET /v1/tags?page=1&pageSize=100`
    response contained zero tags. Tag tools are not implemented.

- [ ] **Phase 6: Connect server to Claude Cowork** (`phase-6-cowork`)
  - Depends on: `phase-5-smoke-test`
  - Register the server with `claude mcp add` or MCP config, sourcing `HUMANITIX_API_KEY` from the server's `.env`. Confirm `humanitix_sales_summary` matches the Humanitix dashboard.

- [x] **Phase 7: Complete README documentation** (`phase-7-docs`)
  - Depends on: `phase-5-smoke-test`
  - Completed on 15 August 2026: README documents the verified public CHCon
    2025 event ID (with date IDs derived at runtime), official key setup,
    project-scoped secret-free MCP configuration, current tool scoping, and
    troubleshooting for Humanitix 401s, location filtering, cold starts, empty
    data, BETA check-in, and the separate blocked Claude OAuth 401 condition.

- [ ] **Run full test suite after all fixes** (`full-test-run`)
  - Depends on: `fix-api-paths`, `fix-pagination-keys`, `add-event-id-tool-args`, `add-override-location`
  - Run `uv run pytest` and verify Git history contains no real API key material.

- [x] **Keep `plan/` out of version control** (`commit-plan`)
  - Completed: `plan/` is ignored by `.gitignore` and must remain local-only.
