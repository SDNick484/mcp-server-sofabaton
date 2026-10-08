# CLAUDE.md

## What this is

`mcp-server-sofabaton`: an MCP server for Sofabaton X1/X1S/X2 hubs. Every model works through
**sofabaton-x-server** (REST), which owns the hub session via the `sofabaton-x` library. An
**X2** can also be reached over **MQTT** (its own broker link), alone or alongside the server.
Sibling of `mcp-server-onkyo`, `mcp-server-shieldtv` and `mcp-server-harmony`.

**This is a learning project.** The owner wants to understand how MCP servers are built, so
explain the reasoning behind non-obvious changes instead of only making them.

## Layout

- `src/sofabaton_mcp/config.py`: settings (`SOFABATON_URL`, `SOFABATON_HUB`, `SOFABATON_MQTT_*`,
  `SOFABATON_DRY_RUN`), validated into `Settings.problems`; the button allow-list
  (`ButtonName`, `BUTTON_CODES`)
- `src/sofabaton_mcp/api.py`: REST transport; TypedDicts from the server's OpenAPI; `_request`
  allow-lists routes
- `src/sofabaton_mcp/protocol.py`: X2 MQTT topics and payloads (pure data, each tagged with its
  assumption id)
- `src/sofabaton_mcp/mqtt.py`: `MqttHub`, one broker connection: reconnect, request/reply,
  pushed state, presses ring, settle window
- `src/sofabaton_mcp/client.py`: `SofabatonClient`: path choice per call (server in control mode,
  else MQTT), names -> ids per path, confirmation, capabilities/limitations, rate limits
- `src/sofabaton_mcp/server.py`: MCP tools (`MCPServer` from `mcp` 2.x)
- `src/sofabaton_mcp/assumptions.py`: every unverified protocol claim (id, source, confidence,
  status)
- `src/sofabaton_mcp/limits.py`, `logsafe.py`, `remote.py` (shared with siblings, keep identical)
- `src/sofabaton_mcp/doctor.py`, `cli.py`: `serve`, `check`, `call`, `doctor`, `simulate`
- `src/sofabaton_mcp/sim/`: `FakeServer` (Starlette), `Broker` (MQTT 3.1.1), `FakeX2`, shared
  `FakeHubState`, fixtures. Used by tests *and* `simulate`
- `tests/`: `conftest.py` (fakes, `broker`/`state`/`x2` fixtures; `MQTT_TEST_BROKER` swaps in a
  real broker), `test_openapi_contract` (vs the recorded 0.2.4 OpenAPI), `test_client`,
  `test_mqtt`, `test_tools` (in-process MCP `Client`), `test_safety`, `test_tooling`,
  `test_recorded` (replays `doctor --dump` captures), `test_assumptions`, `test_stdio`,
  `test_remote`, `test_api`, `test_config`. Async tests use anyio's plugin.

## Rules for changes

- **Never hold a sofabaton-x-server token.** Reads and control are free on the server; edits,
  deletes, erase and restore need a token. `api._CONTROL_POSTS` is the second wall;
  `test_openapi_contract` pins it to exactly the four free control routes.
- **MQTT: only the topics in `protocol.py`.** Adding one means a PROTOCOL.md row, an
  assumption, a fake-X2 handler and a test.
- **Ids never cross transports.** Resolve names on the path you'll use (`S-MQTT-IDS`); the tests
  give MQTT a different id space on purpose.
- **Tell the model what this hub can do.** New features must show up in `_capabilities()`
  (capability when available, limitation with the fix when not), and errors on a path that can't
  do something say why and what would enable it. MQTT features are X2-only; say so.
- **Don't invent protocol details.** Anything not confirmed on hardware is an `Assumption`, cited
  as `# ASSUMPTION <id>` where code depends on it, in PROTOCOL.md, README's table and
  HARDWARE_VALIDATION.md. `test_assumptions` enforces it. The fake X2 writes payloads out
  independently of `protocol.py`.
- No `POWER_ON`/`POWER_OFF` in `press_button`; activities go through `start_activity` /
  `power_off`, which confirm. Don't trust `accepted: true`.
- Over MQTT, presses wait out the settle window after an activity change (`S-MQTT-SETTLE`).
- Raise `SofabatonError` (a `ToolError`) for anything the model or user can act on.
- Log to **stderr only**, through `logsafe` (IPs, MACs, URL passwords redacted).
- Every tool has a `title`, explicit `ToolAnnotations`, and constrained args. `test_tools.py`
  enforces this. Nothing is destructive.

## sofabaton-x-server facts (checked against 0.2.4, 2026-10)

- API under `/api/v1`; `/api/v1/openapi.json` is served but not listed in itself. Hub ids come from
  `GET /hubs`.
- `mode`: `control`, `observe` (the app holds the proxy; sends 409), `disconnected` (catalog reads
  and send/start/stop 503, since they look the id up first; find-remote 409). Read from
  `routes_hub_data.py`.
- Errors are RFC 9457 problem bodies with `detail` and `mode`. `HubConfig.name` and
  `HubView.hub_name` may be absent (`NotRequired`).
- `POST /send {entity_id, command_id}`; favorites go to the favorite's *device*.

## X2 MQTT (see PROTOCOL.md for sources and confidence)

Hub publishes `<MAC>/up` (presses), `activity/<MAC>/activity_control_up` (every change, early;
255 = all off; drop retained) and list replies (which echo the requested id at the top level).
We publish `activity_control_down`, `keys_control`, `macro_keys_control`,
`favorites_keys_control` (device id in `activity_id`), `device/<MAC>/keys_control`, and the
`*_request` topics, one at a time 200 ms apart. MAC: uppercase bare hex. X1/X1S: no MQTT.

## Status

Verified against the simulator only. HARDWARE_VALIDATION.md is the checklist. Captures from
`doctor --dump` go in `tests/fixtures/recorded/`.

## Commands

```sh
pip install -e ".[dev]" && pytest && ruff check . && ruff format --check . && mypy
mcp-server-sofabaton simulate            # fake hub; prints the export lines for another terminal
mcp-server-sofabaton doctor | check | call <tool> k=v | serve [--http]
```
