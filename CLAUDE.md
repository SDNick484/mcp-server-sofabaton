# CLAUDE.md

## What this is

`mcp-server-sofabaton`: an MCP server for Sofabaton X1/X1S/X2 hubs. It talks to
**sofabaton-x-server** (REST), which owns the hub session via the `sofabaton-x` library.
Sibling of `mcp-server-onkyo`, `mcp-server-shieldtv` and `mcp-server-harmony`.

**This is a learning project.** The owner wants to understand how MCP servers are built, so
explain the reasoning behind non-obvious changes instead of only making them.

## Layout

- `src/sofabaton_mcp/config.py`: settings (`SOFABATON_URL`, `SOFABATON_HUB`) and the button
  allow-list (`ButtonName`, `BUTTON_CODES`)
- `src/sofabaton_mcp/api.py`: transport: typed REST client; `_request` allow-lists routes
- `src/sofabaton_mcp/client.py`: domain: names -> ids, hub selection, confirmation polling
- `src/sofabaton_mcp/server.py`: MCP tools (`MCPServer` from `mcp` 2.x)
- `src/sofabaton_mcp/cli.py`: `serve` (default), `check`
- `tests/`: `conftest.py` (`FakeServer` behind `httpx.MockTransport`, payloads with the server's
  full field sets), `test_config` (button codes vs the library), `test_api`, `test_client`,
  `test_tools` (in-process MCP `Client`), `test_stdio`. Async tests use anyio's plugin.

## Rules for changes

- **Never hold a sofabaton-x-server token.** Reads and control (send, start/stop, find-remote)
  are free on the server; edits, deletes, erase and restore need a token. Not having one is
  the main wall. `api._CONTROL_POSTS` is the second: it's the complete list of non-GET routes
  this code may call. Don't add routes to it without a README and test change.
- No `POWER_ON`/`POWER_OFF` in `press_button`; activities go through `start_activity` /
  `power_off`, which confirm.
- Don't trust `accepted: true`. It means the hub took the frame. Starts and stops poll
  `GET /activity` until the change shows.
- Raise `SofabatonError` (a `ToolError`) for anything the model or user can act on.
- Log to **stderr only**. stdout is the MCP stdio transport.
- Every tool has a `title`, explicit `ToolAnnotations`, and constrained args.
  `test_tools.py` enforces this. Nothing is destructive (we can't edit the hub).

## sofabaton-x-server facts the code depends on (checked against 0.2.4, 2026-10)

- API under `/api/v1`. Hub ids come from `GET /hubs`; a manually added hub's id is its host.
- `mode`: `control` (sends work), `observe` (the official app is connected through the proxy;
  sends get 409), `disconnected` (no hub session; catalog reads get 503).
- Errors are RFC 9457 problem bodies with `detail` and often `mode`.
- `POST /send {entity_id, command_id}`: entity is an activity (101+) or device; command is a
  command id, macro id, or button code. Favorites are sent to the favorite's *device*.
- `start`/`stop` are the activity's POWER_ON/POWER_OFF buttons under the hood.
- A server with no admin account set up lets anyone write (it logs a warning). The README tells
  the user to claim it; until then only our local allow-list stands in front of writes.

## MQTT (X2 only), as far as the library documents it

The X2 *publishes* to the broker configured in the Sofabaton app; nothing documents it taking
commands over MQTT. Two topics matter:
- `<MAC>/up`: `{"device_id", "key_id"}` when a button bound to a "Wifi Device" (MQTT type) is
  pressed. sofabaton-x-server subscribes and exposes them at `GET /presses`, which
  `get_recent_presses` reads.
- `activity/<MAC>/activity_control_up`: activity transitions, early in the power macro. The
  library's `apply_external_activity_state` consumes it (drop retained messages).

## Status

Untested on hardware. Validated against a real sofabaton-x-server 0.2.4 with no reachable hub
(route paths, response shapes, 409/503 problem bodies).

## Commands

```sh
pip install -e ".[dev]" && pytest && ruff check . && ruff format --check . && mypy
SOFABATON_URL=http://<server>:8480 mcp-server-sofabaton check | (serve)
```
