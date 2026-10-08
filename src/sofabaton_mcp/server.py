"""MCP tool definitions.

Same shape as the Harmony sibling, on purpose: activity-first, with buttons
and commands going through the running activity unless a device is named.
Differences come from the Sofabaton model:
  - The remote's hard buttons are a fixed set (ButtonName), so press_button
    takes an enum the model can only pick from, sent to an activity that
    routes it.
  - Named commands come from the hub: a device's command list, or an
    activity's macros and favorites (send_command).
  - find_remote makes the physical remote beep.
  - get_recent_presses reads button presses the hub reported to
    sofabaton-x-server from a Wifi Device (over HTTP, or MQTT on the X2), so
    a button on the remote can be a signal *to* the model.

Docstrings are the model's documentation; signatures become JSON Schema.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import TypedDict

from .api import PressPage, ServerAPI
from .client import SofabatonClient, Status
from .config import ButtonName, load_settings

log = logging.getLogger(__name__)

_client: SofabatonClient | None = None


def client() -> SofabatonClient:
    assert _client is not None, "server lifespan has not started"
    return _client


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
    """One HTTP connection pool for the server's life. Unlike the Shield, there's no
    device connection to keep alive here: sofabaton-x-server holds the hub session."""
    global _client
    settings = load_settings()
    api = ServerAPI(settings.url)
    _client = SofabatonClient(settings, api)
    try:
        yield
    finally:
        await api.aclose()
        _client = None


mcp = MCPServer(
    "sofabaton",
    instructions=(
        "Controls a Sofabaton X1/X1S/X2 universal remote hub through sofabaton-x-server. Prefer activities: "
        "start_activity powers the right devices; power_off turns the running activity off. press_button sends a "
        "remote button (VOL_UP, PAUSE, ...) through the running activity; send_command sends a named command, "
        "macro or favorite. Call get_status first."
    ),
    lifespan=lifespan,
)

# Explicit annotations everywhere (a tool without them is assumed destructive,
# non-idempotent and open-world). open_world is False: one server on the LAN.
# Nothing is destructive: this server can't edit the hub (see config.py).
_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_ACT_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


class ActivityInfo(TypedDict):
    name: str
    running: bool


class DeviceInfo(TypedDict):
    name: str
    brand: str | None
    device_class: str | None


class CommandInfo(TypedDict):
    label: str
    via: str  # "activity" (a macro) or the device a favorite goes to


Target = Annotated[
    str | None,
    Field(description="An activity or device name. Omit to use the running activity."),
]
Repeat = Annotated[int, Field(ge=1, le=10, description="How many times to press it (1-10).")]


@mcp.tool(title="Get hub status", annotations=_READ)
async def get_status() -> Status:
    """Report the hub's connection, whether it can be controlled, and the running activity.

    mode is "control" when commands work, "observe" when the Sofabaton app holds the hub (close the app), and
    "disconnected" when the server has no session with it.
    """
    return await client().status()


@mcp.tool(title="List activities", annotations=_READ)
async def list_activities() -> list[ActivityInfo]:
    """List the activities set up on the hub, marking the running one."""
    return [{"name": a["name"], "running": a["active"]} for a in await client().activities()]


@mcp.tool(title="List devices", annotations=_READ)
async def list_devices() -> list[DeviceInfo]:
    """List the devices the hub controls."""
    return [
        {"name": d["name"], "brand": d["brand"], "device_class": d["device_class"]} for d in await client().devices()
    ]


@mcp.tool(title="List commands", annotations=_READ)
async def list_commands(target: Target = None) -> list[CommandInfo]:
    """List the named commands send_command accepts for an activity or device (default: the running activity).

    For an activity these are its macros and favorites; for a device, its own command list. The remote's hard
    buttons are separate: use press_button for those.
    """
    c = client()
    t = await c.target(target)
    devices = {d["device_id"]: d["name"] for d in await c.devices()} if t.kind == "activity" else {}
    return [
        {"label": label, "via": "activity" if entity == t.entity_id else devices.get(entity, str(entity))}
        for label, entity, _cmd in await c.commands(t)
    ]


@mcp.tool(title="Start an activity", annotations=_ACT_IDEMPOTENT)
async def start_activity(
    activity: Annotated[str, Field(description="Activity name from list_activities, e.g. 'Watch Shield'.")],
) -> str:
    """Start an activity and wait until the hub reports it running (its power-on macro can take a while).

    Starting the activity that is already running does nothing.
    """
    c = client()
    a = await c.find_activity(activity)
    return f"Started {a['name']}" if await c.start_activity(a) else f"{a['name']} was already running"


@mcp.tool(title="Turn the running activity off", annotations=_ACT_IDEMPOTENT)
async def power_off() -> str:
    """Power off the running activity and wait until the hub confirms."""
    name = await client().stop()
    return f"Powered off {name}" if name else "Nothing was running"


@mcp.tool(title="Press a remote button", annotations=_ACT)
async def press_button(button: ButtonName, target: Target = None, repeat: Repeat = 1) -> str:
    """Press one of the remote's hard buttons, e.g. VOL_UP with repeat=3, or PAUSE.

    Sent to the running activity by default, which routes it to the device it binds that button to.
    """
    c = client()
    t = await c.target(target)
    await c.press(button, t, repeat)
    return f"Pressed {button} x{repeat} on {t.kind} {t.name}"


@mcp.tool(title="Send a named command", annotations=_ACT)
async def send_command(
    command: Annotated[str, Field(description="A label from list_commands, e.g. 'Netflix' or 'Input HDMI 2'.")],
    target: Target = None,
    repeat: Repeat = 1,
) -> str:
    """Send a named command: an activity macro or favorite, or a device command. Names come from list_commands."""
    c = client()
    t = await c.target(target)
    label = await c.send_command(command, t, repeat)
    return f"Sent {label} x{repeat} via {t.kind} {t.name}"


@mcp.tool(title="Find the remote", annotations=_ACT)
async def find_remote() -> str:
    """Make the physical remote beep so it can be found (in the couch, again)."""
    await client().find_remote()
    return "The remote is beeping"


@mcp.tool(title="Get recent button presses", annotations=_READ)
async def get_recent_presses(
    after_seq: Annotated[
        int | None, Field(ge=0, description="Only presses newer than this seq (from a previous call's last_seq).")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> PressPage:
    """Recent presses of remote buttons bound to a Wifi Device on the hub (newest first, or oldest first with
    after_seq). Lets a button on the remote ask for something. Empty unless a Wifi Device is set up in
    sofabaton-x-server; if instance_id changes, the server restarted and older seqs are gone.
    """
    return await client().presses(after_seq, limit)
