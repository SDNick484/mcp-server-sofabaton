# mcp-server-sofabaton

[![CI](https://github.com/SDNick484/mcp-server-sofabaton/actions/workflows/ci.yml/badge.svg)](https://github.com/SDNick484/mcp-server-sofabaton/actions/workflows/ci.yml)

An [MCP](https://modelcontextprotocol.io) server for **Sofabaton X1 / X1S / X2** universal
remote hubs. It lets an MCP client such as Claude start and stop activities, press remote
buttons, send macros and favorites, find the remote, and see button presses meant for it.

> **Status: early / untested on hardware.** Covered by unit tests against a fake server,
> smoke-tested over stdio, and checked against a real sofabaton-x-server 0.2.4 (with no hub
> attached), but not yet run against a real hub.

Not affiliated with Sofabaton.

## Why go through sofabaton-x-server

Sofabaton publishes no API. The [sofabaton-x](https://pypi.org/project/sofabaton-x/) library
reverse-engineers the protocol by sitting **between** the hub and the official app as a proxy,
which means one process owns the hub at a time. [sofabaton-x-server](https://pypi.org/project/sofabaton-x-server/)
is that process, with a REST API, a web remote, and MQTT support. This MCP server is one more
client of it:

|                            | Through sofabaton-x-server (this project) | Embed the library directly         |
| -------------------------- | ----------------------------------------- | ---------------------------------- |
| Sharing the hub            | Many clients (web remote, scripts, MCP)    | The MCP server owns it alone       |
| Where it runs              | Server anywhere on the LAN (an LXC fits)   | Wherever the MCP client runs       |
| What a model could reach   | Control routes only (see Safety)           | Everything, including erase/restore |
| Extra moving part          | Yes                                        | No                                 |

The deciding row is the third: the library can also edit, erase and restore the hub. Through
the server, those need a token this project never holds.

## Install

```
git clone https://github.com/SDNick484/mcp-server-sofabaton.git
cd mcp-server-sofabaton
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Requires Python 3.11+, and a running sofabaton-x-server with your hub added (see its starter
guide; close the Sofabaton app on every phone and tablet before first setup).

**Claim the server.** Open its control panel and set up an admin account (Server settings >
Access). Until you do, sofabaton-x-server lets anyone on the LAN change hubs, and it logs a
warning saying so. Once claimed, writes need a token and this MCP server can't make them.

## First contact

```
SOFABATON_URL=http://192.168.1.70:8480 mcp-server-sofabaton check
```

`check` reaches the server, picks the hub, and prints its mode, activities and devices: the
names the model will use. Commands only work in mode `control`.

## Use it with an MCP client

```json
{
  "mcpServers": {
    "sofabaton": {
      "command": "/path/to/mcp-server-sofabaton/.venv/bin/mcp-server-sofabaton",
      "env": { "SOFABATON_URL": "http://192.168.1.70:8480" }
    }
  }
}
```

For Claude Code: `claude mcp add sofabaton -e SOFABATON_URL=http://192.168.1.70:8480 -- /path/to/.venv/bin/mcp-server-sofabaton`.

## Configuration

| Setting         | Default                 | What it does                                                    |
| --------------- | ----------------------- | --------------------------------------------------------------- |
| `SOFABATON_URL` | `http://localhost:8480` | Where sofabaton-x-server is                                     |
| `SOFABATON_HUB` | *(the only hub)*        | Hub id or name, when the server manages more than one          |

There is deliberately no token setting.

## Tools

| Tool                 | What it does                                                                        |
| -------------------- | ----------------------------------------------------------------------------------- |
| `get_status`         | Hub connection, mode (`control`/`observe`/`disconnected`), running activity, model  |
| `list_activities`    | Activities, marking the running one                                                 |
| `list_devices`       | Devices, with brand and class                                                       |
| `list_commands`      | Named commands for an activity (macros, favorites) or a device (default: running)   |
| `start_activity`     | Start an activity and wait until the hub reports it running; no-op if it already is |
| `power_off`          | Power off the running activity and confirm                                          |
| `press_button`       | Press a remote hard button (`VOL_UP`, `PAUSE`, `OK`, ...) 1-10 times                |
| `send_command`       | Send a macro, favorite or device command by name, 1-10 times                        |
| `find_remote`        | Make the remote beep                                                                |
| `get_recent_presses` | Presses of remote buttons bound to a Wifi Device (see MQTT below)                   |

`press_button` and `send_command` go through the running activity unless you name a device,
so `VOL_UP` reaches whatever the activity binds volume to.

## MQTT and the remote as an input (X2)

The X2 publishes to an MQTT broker you set in the Sofabaton app. As far as the library
documents, the hub only *publishes*, so MQTT is a way for events to come in, not a way to
send commands:

- **Buttons as triggers.** A "Wifi Device" on the hub is a virtual device whose commands do
  nothing but report the press: on the X2, to `<MAC>/up` on your broker; on any model, over
  HTTP to sofabaton-x-server. Bind one to a key (say, a long press of a color button) and the
  press lands in the server's press log, which `get_recent_presses` reads. That lets a remote
  button mean "ask the assistant" or "run my bedtime routine" to an agent that polls it.
- **Faster activity state.** The X2 also publishes activity transitions to
  `activity/<MAC>/activity_control_up` early in the power macro, before the hub session
  confirms them.

Set up Wifi Devices and the broker in sofabaton-x-server's control panel. Both are writes, so
they're done there, not by this server.

## Safety design

- **No token, so no writes.** sofabaton-x-server allows reads and control without a token and
  requires one for editing, deleting, erasing and restoring. This project never has one.
- **A local route allow-list too.** The client can only issue GETs and the four control POSTs
  (`send`, activity `start`/`stop`, `find-remote`); anything else raises before a request
  leaves the process.
- **Names resolve against the hub's catalog.** The model never supplies an id. Buttons are an
  enum, and `POWER_ON`/`POWER_OFF` are left out so starting and stopping always goes through
  the tools that confirm.
- **Confirmation, not trust.** The server's `accepted: true` means the hub took the frame.
  Starts and stops wait for the running activity to change.
- Every tool carries a title and MCP annotations; nothing is destructive. Logs go to stderr.

## Troubleshooting

**"Can't reach sofabaton-x-server at ..."** It isn't running, or `SOFABATON_URL` is wrong (an
MCP client may launch this server with a different environment; set it in the client config).

**"... close the app on every phone and tablet."** The official app is connected through the
proxy, which puts the hub in `observe` mode: reads work, sends are refused.

**"The server has no session with the hub right now"** The hub is off the network, or its IP
changed. Check the server's control panel.

**"... didn't report it finished within 30s"** The activity's power-on macro is long or a
device didn't respond. Check `get_status` before retrying.

## Development

```
pip install -e ".[dev]"
pytest              # api, client, in-process MCP, and stdio tests; no hub or server needed
ruff check . && ruff format --check .
mypy                # strict
```

To poke at the tools interactively: `npx @modelcontextprotocol/inspector mcp-server-sofabaton`.

## Roadmap

- Verify on a real hub
- Push instead of poll: subscribe to the server's events WebSocket and surface activity
  changes and presses as MCP resource updates
- Optionally read `activity/<MAC>/activity_control_up` straight from the broker (X2)
- Publish to PyPI and the MCP registry

## License

MIT. See [LICENSE](LICENSE).
