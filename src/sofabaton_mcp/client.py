"""Domain layer: names the model uses -> ids the hub uses, plus confirmation.

The REST API speaks ids (activity 101, device 3, command 17). The model should
speak names ("Watch Shield", "Onkyo", "Netflix"). This module does that
translation against the hub's own catalog, which also makes the catalog the
allow-list: an id never comes from the model.

It also refuses to trust a 200. "accepted" from the server means the hub took
the frame, not that the activity came up, so starts and stops are confirmed by
watching the running activity change (the same lesson as the Shield's
launch_app: a sent command proves nothing).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from typing_extensions import TypedDict

from .api import Activity, Command, Device, HubMode, PressPage, RunningActivity, ServerAPI, SofabatonError
from .config import ALLOWED_BUTTONS, BUTTON_CODES, Settings

log = logging.getLogger(__name__)


class ActivityRef(TypedDict):
    id: int
    name: str | None


class Status(TypedDict):
    server_url: str
    hub_id: str | None
    hub_name: str | None
    model: str | None
    firmware_outdated: bool | None
    hub_connected: bool
    mode: HubMode
    controllable: bool
    app_connected: bool
    running_activity: ActivityRef | None


@dataclass(frozen=True)
class Target:
    """Where a button or command goes: an activity (routes it) or a device."""

    kind: Literal["activity", "device"]
    entity_id: int
    name: str


def _norm(name: str) -> str:
    """Case-, space- and punctuation-insensitive matching key ("Watch-Shield" == "watch shield")."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _names(items: list[str]) -> str:
    return ", ".join(items) if items else "(none)"


class SofabatonClient:
    # The hub runs an activity's power-on macro (devices on, inputs set, delays)
    # before reporting it running.
    activity_timeout = 30.0
    poll_interval = 0.5
    # Gap between repeated presses, so IR receivers see separate presses.
    repeat_gap = 0.3

    def __init__(self, settings: Settings, api: ServerAPI) -> None:
        self.settings = settings
        self.api = api
        self._hub_id: str | None = None

    # --- which hub -------------------------------------------------------------
    async def hub_id(self) -> str:
        """Resolve SOFABATON_HUB (id or name) once; with one hub, just use it."""
        if self._hub_id is not None:
            return self._hub_id
        hubs = await self.api.hubs()
        wanted = self.settings.hub
        if wanted:
            key = _norm(wanted)
            match = [
                h
                for h in hubs
                if h["hub_id"] == wanted
                or key in (_norm(h.get("hub_name") or ""), _norm(h["config"].get("name") or ""))
            ]
        else:
            match = hubs
        if len(match) != 1:
            listed = _names([f"{h['hub_id']} ({h.get('hub_name') or h['config']['host']})" for h in hubs])
            if not hubs:
                raise SofabatonError("sofabaton-x-server has no hubs yet; add one in its control panel (/ui/).")
            if wanted:
                raise SofabatonError(f"No hub matches SOFABATON_HUB={wanted!r}. Hubs: {listed}")
            raise SofabatonError(f"sofabaton-x-server manages several hubs; set SOFABATON_HUB to one of: {listed}")
        self._hub_id = match[0]["hub_id"]
        return self._hub_id

    # --- status ----------------------------------------------------------------
    async def status(self) -> Status:
        hub = await self.hub_id()
        view = await self.api.status(hub)
        st = view["status"]
        name = model = None
        outdated: bool | None = None
        if st and st["hub_connected"]:
            try:
                info = await self.api.info(hub)
                name, model, outdated = info["name"], info["model"], info["firmware_outdated"]
            except SofabatonError as exc:  # identity is nice-to-have; don't fail status over it
                log.info("hub info unavailable: %s", exc)
        running = st["running_activity"] if st else None
        return {
            "server_url": self.settings.url,
            "hub_id": hub,
            "hub_name": name,
            "model": model,
            "firmware_outdated": outdated,
            "hub_connected": bool(st and st["hub_connected"]),
            "mode": st["mode"] if st else "disconnected",
            "controllable": bool(st and st["controllable"]),
            "app_connected": bool(st and st["app_connected"]),
            "running_activity": {"id": running["activity_id"], "name": running["name"]} if running else None,
        }

    # --- lookups ---------------------------------------------------------------
    async def activities(self) -> list[Activity]:
        return await self.api.activities(await self.hub_id())

    async def devices(self) -> list[Device]:
        return await self.api.devices(await self.hub_id())

    async def find_activity(self, name: str) -> Activity:
        acts = await self.activities()
        found = next((a for a in acts if _norm(a["name"]) == _norm(name)), None)
        if found is None:
            raise SofabatonError(f"Unknown activity {name!r}. Activities: {_names([a['name'] for a in acts])}")
        return found

    async def target(self, name: str | None) -> Target:
        """Resolve an activity or device name; None means the running activity."""
        hub = await self.hub_id()
        if name is None:
            running = await self.api.running_activity(hub)
            if running is None:
                raise SofabatonError("No activity is running. Name a device, or start an activity first.")
            return Target("activity", running["activity_id"], running["name"] or str(running["activity_id"]))
        key = _norm(name)
        acts = await self.api.activities(hub)
        if a := next((a for a in acts if _norm(a["name"]) == key), None):
            return Target("activity", a["activity_id"], a["name"])
        devs = await self.api.devices(hub)
        if d := next((d for d in devs if _norm(d["name"]) == key), None):
            return Target("device", d["device_id"], d["name"])
        raise SofabatonError(
            f"No activity or device named {name!r}. Activities: {_names([a['name'] for a in acts])}. "
            f"Devices: {_names([d['name'] for d in devs])}."
        )

    async def commands(self, target: Target) -> list[tuple[str, int, int]]:
        """(label, entity_id, command_id) for every named command the target offers.

        A device has its own command list. An activity offers its macros (sent
        to the activity) and its favorites (each sent to the favorite's device).
        """
        hub = await self.hub_id()
        if target.kind == "device":
            cmds: list[Command] = await self.api.commands(hub, target.entity_id)
            return [(c["label"], target.entity_id, c["command_id"]) for c in cmds]
        out = [
            (m["label"], target.entity_id, m["command_id"])
            for m in await self.api.macros(hub, target.entity_id)
            if m["label"]
        ]
        out += [
            (f["label"], f["device_id"], f["command_id"])
            for f in await self.api.favorites(hub, target.entity_id)
            if f["label"]
        ]
        return out

    # --- control -----------------------------------------------------------------
    async def start_activity(self, activity: Activity) -> bool:
        """Start it and confirm it's running. False if it already was (nothing sent)."""
        hub = await self.hub_id()
        running = await self.api.running_activity(hub)
        if running and running["activity_id"] == activity["activity_id"]:
            return False
        await self.api.start_activity(hub, activity["activity_id"])
        await self._confirm(
            hub, lambda r: r is not None and r["activity_id"] == activity["activity_id"], activity["name"]
        )
        return True

    async def stop(self) -> str | None:
        """Power off the running activity and confirm. Returns its name, or None if nothing was on."""
        hub = await self.hub_id()
        running = await self.api.running_activity(hub)
        if running is None:
            return None
        aid = running["activity_id"]
        name = running["name"] or str(aid)
        await self.api.stop_activity(hub, aid)
        await self._confirm(hub, lambda r: r is None or r["activity_id"] != aid, f"power off {name}")
        return name

    async def _confirm(self, hub: str, done: Callable[[RunningActivity | None], bool], what: str) -> None:
        """Poll the running activity until done(it) holds; a few looks a second is plenty."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.activity_timeout
        while loop.time() < deadline:
            if done(await self.api.running_activity(hub)):
                return
            await asyncio.sleep(self.poll_interval)
        raise SofabatonError(
            f"The hub accepted '{what}' but didn't report it finished within {self.activity_timeout:.0f}s. "
            "It may still be running its macro; check get_status before retrying."
        )

    async def press(self, button: str, target: Target, repeat: int = 1) -> None:
        # Re-check even though the tool schema is an enum: last line of defense.
        if button not in ALLOWED_BUTTONS:
            raise SofabatonError(f"Button {button!r} is not allowed.")
        await self._send_n(target.entity_id, BUTTON_CODES[button], repeat)

    async def send_command(self, label: str, target: Target, repeat: int = 1) -> str:
        options = await self.commands(target)
        key = _norm(label)
        hit = next((o for o in options if _norm(o[0]) == key), None)
        if hit is None:
            raise SofabatonError(
                f"{target.kind} {target.name} has no command {label!r}. Use list_commands to see what it accepts."
            )
        await self._send_n(hit[1], hit[2], repeat)
        return hit[0]

    async def _send_n(self, entity_id: int, command_id: int, repeat: int) -> None:
        hub = await self.hub_id()
        for i in range(repeat):
            if i:
                await asyncio.sleep(self.repeat_gap)
            await self.api.send(hub, entity_id, command_id)

    async def find_remote(self) -> None:
        await self.api.find_remote(await self.hub_id())

    async def presses(self, after: int | None, limit: int) -> PressPage:
        return await self.api.presses(await self.hub_id(), after, limit)
