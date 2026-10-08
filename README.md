# mcp-server-sofabaton

[![CI](https://github.com/SDNick484/mcp-server-sofabaton/actions/workflows/ci.yml/badge.svg)](https://github.com/SDNick484/mcp-server-sofabaton/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server for **Sofabaton X1, X1S and X2** universal
remote hubs. It lets an MCP client such as Claude start and stop activities, press remote
buttons, send macros, favorites and device commands, find the remote, and read the presses of
buttons you've bound to it.

All three models work through [sofabaton-x-server](https://pypi.org/project/sofabaton-x-server/).
An **X2** can also be reached over **MQTT**, either on its own or alongside the server. That adds
live activity state, and keeps the hub controllable while the Sofabaton app is open. Every
response tells the model which model it's talking to and what that hub can and can't do.

> **Status: verified against the simulator only.** Nothing here has run against a real hub yet.
> Every protocol detail that hasn't been confirmed on hardware is a named assumption
> ([PROTOCOL.md](PROTOCOL.md)), and [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) is the
> checklist for confirming them.

Not affiliated with Sofabaton.

## What works with which hub

|                                                  | X1 / X1S         | X2 + server     | X2 + server + MQTT          | X2, MQTT only    |
| ------------------------------------------------ | ---------------- | --------------- | --------------------------- | ---------------- |
| Activities, buttons, macros, favorites, commands | yes              | yes             | yes                         | yes              |
| Find the remote; model and firmware              | yes              | yes             | yes                         | no               |
| Presses of buttons bound to a Wifi Device        | yes (HTTP)       | yes             | yes                         | yes (MQTT)       |
| Live activity state (pushed by the hub)          | no               | no              | yes                         | yes              |
| Control while the Sofabaton app is open          | no               | no              | yes: falls back to MQTT     | yes              |
| Needs                                            | server           | server          | server + broker             | broker + the MAC |

**MQTT is X2-only.** The X1 and X1S have no MQTT; on those models, the server is the only way in
and everything in the first three rows works through it. `get_status` reports this as
`model`, `capabilities` and `limitations`, so the model can plan around it. For example, on an
X1S it says *"This is an X1S: live activity state, control while the Sofabaton app is open,
and MQTT presses need an X2."*

## How it reaches the hub

```
Claude ──► mcp-server-sofabaton ──┬──► sofabaton-x-server ──► hub (X1, X1S, X2)
          (picks a path per call) └──► MQTT broker ◄────────► hub (X2 only)
```

- **Through sofabaton-x-server (any model).** Sofabaton publishes no API. The
  [sofabaton-x](https://pypi.org/project/sofabaton-x/) library reverse-engineers it by
  sitting between the hub and the official app as a proxy, so one process owns the hub at a
  time. sofabaton-x-server is that process, with a REST API. Going through it means many
  clients can share the hub. It also means a model can only reach the control routes: editing,
  erasing and restoring the hub need a token this project never holds.
- **Over MQTT (X2).** The X2 keeps its own connection to the broker you set in the Sofabaton
  app (Me → Connect to Home Assistant). It answers list requests and takes commands on topics
  under its MAC, and it announces every activity change. This doesn't go through the proxy, so
  it keeps working while the app holds it.

With both configured, each call uses the server when it's in control mode and MQTT otherwise.
Names are resolved on whichever path is used, so ids never cross from one to the other.

## Install

```sh
git clone https://github.com/SDNick484/mcp-server-sofabaton.git
cd mcp-server-sofabaton
python -m venv .venv && . .venv/bin/activate
pip install -e ".[mqtt]"     # or `pip install -e .` for server-only (X1/X1S, or an X2 without MQTT)
```

Requires Python 3.11+. The `[mqtt]` extra adds one dependency, `aiomqtt` (which brings
`paho-mqtt`).

**Claim sofabaton-x-server.** Open its control panel and set up an admin account (Server
settings > Access). Until you do, it lets anyone on the LAN edit and erase hubs. `doctor` warns
while it's unclaimed.

## Configuration

Environment variables (an MCP client may not pass your shell's environment, so set them in the
client's config):

| Setting                         | Default                 | What it does                                                                    |
| ------------------------------- | ----------------------- | ------------------------------------------------------------------------------- |
| `SOFABATON_URL`                 | `http://localhost:8480` | sofabaton-x-server's address. `none` for MQTT only                              |
| `SOFABATON_HUB`                 | *(the only hub)*        | Hub id or name, when the server manages more than one                          |
| `SOFABATON_MQTT_URL`            | *(off)*                 | `mqtt://[user:pass@]host[:1883]` or `mqtts://...` (X2 only)                     |
| `SOFABATON_MQTT_USERNAME`       |                         | If not in the URL                                                              |
| `SOFABATON_MQTT_PASSWORD`       |                         | If not in the URL                                                              |
| `SOFABATON_MQTT_PASSWORD_FILE`  |                         | Read the password from a file (wins over the others)                           |
| `SOFABATON_MQTT_MAC`            | *(from the server)*     | The X2's MAC. Needed for MQTT only; with a server it's learned                |
| `SOFABATON_MQTT_SETTLE_S`       | `6`                     | After an activity change over MQTT, hold presses off this long                  |
| `SOFABATON_DRY_RUN`             | off                     | Read the hub, send nothing that changes anything (`serve --dry-run` too)        |
| `SOFABATON_LOG_UNREDACTED`      | off                     | Don't mask IPs, MACs and passwords in logs (`--no-redact`)                      |

There is deliberately no sofabaton-x-server token setting. Bad values are reported by name at
startup and by `doctor`, rather than failing later.

Typical setups:

```sh
# X1 / X1S, or an X2 without MQTT
SOFABATON_URL=http://192.168.1.70:8480

# X2, both paths (recommended)
SOFABATON_URL=http://192.168.1.70:8480
SOFABATON_MQTT_URL=mqtt://sofabaton@192.168.1.10:1883
SOFABATON_MQTT_PASSWORD_FILE=/etc/mcp/sofabaton-mqtt.pw

# X2, MQTT only (no sofabaton-x-server)
SOFABATON_URL=none
SOFABATON_MQTT_URL=mqtt://sofabaton:secret@192.168.1.10:1883
SOFABATON_MQTT_MAC=02:AB:34:CD:56:EF
```

## First contact

```sh
mcp-server-sofabaton doctor     # each layer, with a hint for whatever fails
mcp-server-sofabaton check      # the model, the path, capabilities, and the names the model will use
mcp-server-sofabaton call start_activity activity="Watch Shield"    # one tool, as the model calls it
```

`doctor` checks the server (reachable, version, claimed, hub session, mode, identity,
catalog) and MQTT (login, the X2 answering on its topics). If the X2 doesn't answer on the
uppercase-MAC topics, it tries lowercase and tells you. `doctor --listen 30` prints everything
the X2 publishes while you press keys on the remote. `doctor --dump DIR` saves a redacted
capture for `tests/fixtures/recorded/`.

## Use it with an MCP client

Local (stdio): Claude Code and Claude Desktop launch it themselves.

```json
{
  "mcpServers": {
    "sofabaton": {
      "command": "/path/to/mcp-server-sofabaton/.venv/bin/mcp-server-sofabaton",
      "env": {
        "SOFABATON_URL": "http://192.168.1.70:8480",
        "SOFABATON_MQTT_URL": "mqtt://sofabaton:secret@192.168.1.10:1883"
      }
    }
  }
}
```

For Claude Code: `claude mcp add sofabaton -e SOFABATON_URL=http://192.168.1.70:8480 -- /path/to/.venv/bin/mcp-server-sofabaton`.

As a service (Streamable HTTP), to share one instance between Claude Code, Claude Desktop and
the mobile app through a Cloudflare Tunnel with Cloudflare Access in front:

```sh
CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com CF_ACCESS_AUD=<aud tag> \
  mcp-server-sofabaton serve --http --public-host mcp.example.com    # 127.0.0.1:8714/sofabaton/mcp
```

Every request must carry a valid Access JWT (`Cf-Access-Jwt-Assertion`). The checks and the
flags are documented in `src/sofabaton_mcp/remote.py`.

## Tools

| Tool                 | What it does                                                                                    |
| -------------------- | ----------------------------------------------------------------------------------------------- |
| `get_status`         | Start here: model, running activity, `via` (server or MQTT), `capabilities`, `limitations`, `transition` |
| `list_activities`    | Activities, marking the running one                                                             |
| `list_devices`       | Devices (brand and class over the server; names only over MQTT)                                 |
| `list_commands`      | Named commands for an activity (macros, favorites) or a device (default: the running activity)  |
| `start_activity`     | Start an activity and wait for the hub to confirm; `unchanged` if it's already running          |
| `power_off`          | Power off the running activity and confirm                                                      |
| `press_button`       | A remote hard button (`VOL_UP`, `PAUSE`, `OK`, ...) 1-10 times, through the running activity    |
| `send_command`       | A macro, favorite or device command by name, 1-10 times                                         |
| `find_remote`        | Make the remote beep (needs the server)                                                         |
| `get_recent_presses` | Presses of remote buttons bound to a Wifi Device: the remote as an input to the agent          |

Actions return `{outcome: done | unchanged | dry_run, detail, via, sent}`. `sent` lists the
exact requests or publishes, with the MAC shown as `<MAC>`. When something can't work on this
hub, the error says why and what would enable it.

### A session against the simulator

An X2 with both paths. Someone opens the Sofabaton app partway through, and the server
switches to MQTT. (Output from `simulate`, trimmed.)

```
> get_status
  {"model": "X2", "via": "server", "running_activity": null, "transition": null,
   "capabilities": ["catalog", "activities", "buttons", "commands", "presses", "find_remote",
                    "hub_info", "live_activity_state", "control_while_app_open"],
   "limitations": [], ...}

> start_activity {"activity": "Watch Shield"}
  {"outcome": "done", "detail": "Start Watch Shield", "via": "server",
   "sent": ["POST /api/v1/hubs/a1b2c3/activities/101/start"]}

> press_button {"button": "VOL_UP", "repeat": 3}
  {"outcome": "done", "detail": "Press VOL_UP x3 on activity Watch Shield", "via": "server", ...}

# (someone opens the Sofabaton app: sofabaton-x-server goes to observe mode)

> start_activity {"activity": "Listen to Music"}
  {"outcome": "done", "via": "mqtt",
   "detail": "Start Listen to Music. The X2 announced it early; its power macro may run for a
              few more seconds (presses wait 6s).",
   "sent": ["publish activity/<MAC>/activity_control_down {\"data\": {\"activity_id\": 102, \"state\": \"on\"}}"]}

> send_command {"command": "Input HDMI 2", "target": "Onkyo Receiver"}
  ERROR: The X2 just changed activity and its power macro may still be running; a press now
  could interrupt it. Try again in 5s (SOFABATON_MQTT_SETTLE_S sets this window).

> get_status
  {"model": "X2", "via": "mqtt", "running_activity": "Listen to Music",
   "transition": "power macro may still be running (presses wait 5s more)",
   "capabilities": ["catalog", "activities", "buttons", "commands", "presses", "hub_info",
                    "live_activity_state", "control_while_app_open"],
   "limitations": ["The Sofabaton app holds sofabaton-x-server's proxy (observe mode): commands
                    go over MQTT meanwhile, and find_remote waits until the app is closed."], ...}
```

The same calls on an X1S go through the server, and `limitations` says the MQTT features need
an X2.

## The remote as an input

A "Wifi Device" on the hub is a virtual device whose commands do nothing but report the press.
On the X2 it can report to `<MAC>/up` on your broker, and on any model it can report over HTTP
to sofabaton-x-server. Bind one to a key (say, a long press of a color button) and
`get_recent_presses` sees it. A remote button can then mean "ask the assistant" or "run my
bedtime routine" to an agent that watches for it. Wifi Devices are set up in the Sofabaton app
or the server's control panel: that's editing the hub, which this server doesn't do.

## Safety design

- **No token, so no edits.** sofabaton-x-server lets anyone read and control, and requires a
  token for editing, deleting, erasing and restoring. This project never holds one.
- **A local route allow-list too.** The REST client can only make GETs and the four control
  POSTs (`send`, activity `start`/`stop`, `find-remote`). Anything else raises before a request
  leaves the process. `test_openapi_contract.py` checks that the allow-list matches exactly
  those routes in the server's published API.
- **MQTT topics are fixed.** Only the topics in `protocol.py` are ever published to, with
  payloads built from names resolved against the hub's own lists.
- **Names, not ids.** The model never supplies an id. Buttons are an enum. `POWER_ON` and
  `POWER_OFF` are left out, so starting and stopping always go through the tools that confirm.
- **Confirmation, not trust.** `accepted: true` means the hub took the frame. Starts and stops
  wait for the hub to report the change.
- **Don't interrupt a macro.** Over MQTT the X2 announces a change at the *start* of its power
  macro and never says when it's done, so presses wait out a settle window.
- **Rate limits and caps.** Per call: at most 10 repeats, 100-2000 ms apart. Across calls (a
  model in a loop): 20 presses in a burst, then 4 a second; 4 activity changes in a burst, then
  one per 15 s. A call over the limit is refused whole, never half-sent.
- **Dry run.** `serve --dry-run` (or `call --dry-run`) reads the hub and reports what it would
  send.
- **Logs are redacted** (IP addresses, MACs, passwords in URLs) and go to stderr; stdout is the
  MCP transport.
- Every tool has a title and explicit MCP annotations. Nothing is destructive.

## Troubleshooting

| Message                                                    | Meaning                                                                                    |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `Can't reach sofabaton-x-server at ...`                    | It isn't running, or `SOFABATON_URL` is wrong for the environment the client launched us in |
| `... close the app on every phone and tablet`              | The app holds the proxy (observe mode). On an X2, MQTT would keep it controllable          |
| `has no session with the hub right now`                    | The hub is off the network, or its IP changed. Check the server's control panel            |
| `didn't report it finished within 30s`                     | A long power macro, or a device didn't respond. Check `get_status` before retrying         |
| `The X2 didn't answer the ... request over MQTT`           | The X2 isn't linked to this broker, or the MAC is wrong. Run `doctor`                      |
| `the MQTT broker at ... rejected the login`                | Wrong user or password in `SOFABATON_MQTT_URL`                                             |
| `power macro may still be running`                         | Expected for a few seconds after a change over MQTT                                        |
| `Rate limit: refusing to ...`                              | Many presses in a short time. Wait and retry if it was deliberate                          |

## Development

```sh
pip install -e ".[dev]"
pytest -q           # everything, no hardware: fakes for the server, the broker and the X2
ruff check . && ruff format --check . && mypy
```

The simulator runs the same fakes as real services, so you can point a client (or `doctor`)
at them:

```sh
mcp-server-sofabaton simulate                 # X2: fake server + broker + X2; type `help` at sim>
mcp-server-sofabaton simulate --model X1S     # server only
mcp-server-sofabaton simulate --no-server     # X2 over MQTT only
```

It prints the `export` lines for another terminal. At its `sim>` prompt you act as the
physical remote (`off`, `start <activity>`, `key <device> <key>`) or change the scene
(`app on` puts the server in observe mode).

The MQTT tests use an in-package broker by default. To run them against mosquitto, as CI does:

```sh
docker compose -f dev/compose.yaml up -d
MQTT_TEST_BROKER=mqtt://127.0.0.1:1883 pytest -q tests/test_mqtt.py tests/test_tools.py tests/test_tooling.py -rs
```

What the tests cover:

| File                       | Covers                                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------------------------ |
| `test_openapi_contract.py` | Our routes, types and the fake's payloads against the OpenAPI document recorded from sofabaton-x-server 0.2.4 |
| `test_client.py`           | The server path for every model, capabilities, confirmation                                            |
| `test_mqtt.py`             | The X2 over MQTT, alone and with the server, against a real broker over sockets                        |
| `test_tools.py`            | The MCP contract the model sees (schemas, annotations, structured results)                            |
| `test_safety.py`           | Dry run, rate limits, caps, config validation, redaction                                               |
| `test_tooling.py`          | `doctor`, `check`, `call` and `simulate`, including as real processes                                   |
| `test_recorded.py`         | Replays captures from a real hub (`doctor --dump`) through the real parsers                            |
| `test_assumptions.py`      | Every assumption appears here and in HARDWARE_VALIDATION.md                                           |

## Verification status

Everything is **verified against the simulator only**. These are the protocol assumptions the
code depends on (details and sources in [PROTOCOL.md](PROTOCOL.md)):

| Assumption             | Confidence | Status         |
| ---------------------- | ---------- | -------------- |
| `S-REST-API`           | high       | simulator-only |
| `S-REST-MODES`         | medium     | simulator-only |
| `S-REST-START`         | low        | simulator-only |
| `S-X1-NO-MQTT`         | high       | simulator-only |
| `S-MQTT-UP`            | high       | simulator-only |
| `S-MQTT-STATE`         | high       | simulator-only |
| `S-MQTT-MAC-CASE`      | medium     | simulator-only |
| `S-MQTT-CONTROL`       | medium     | simulator-only |
| `S-MQTT-KEYS`          | medium     | simulator-only |
| `S-MQTT-MACRO`         | medium     | simulator-only |
| `S-MQTT-FAVORITE`      | low        | simulator-only |
| `S-MQTT-DEVICE`        | high       | simulator-only |
| `S-MQTT-LISTS`         | medium     | simulator-only |
| `S-MQTT-IDS`           | low        | simulator-only |
| `S-MQTT-SERIAL`        | medium     | simulator-only |
| `S-MQTT-REPLY-TIMEOUT` | low        | simulator-only |
| `S-MQTT-SETTLE`        | low        | simulator-only |

`test_assumptions.py` fails if this table and `assumptions.py` disagree.

## Roadmap

- Validate on hardware ([HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md))
- Push instead of poll: surface activity changes and presses as MCP resource updates
- Publish to PyPI and the MCP registry

## License

MIT. See [LICENSE](LICENSE).
