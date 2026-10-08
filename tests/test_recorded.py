"""Replay a capture from your own hub through the real code.

At home: `mcp-server-sofabaton doctor --dump captures/`, then copy
captures/sofabaton.json to tests/fixtures/recorded/ (name it anything.json).
Every file there is checked:

  server part  the info, activities and devices sofabaton-x-server returned
               validate against its OpenAPI schemas (S-REST-API)
  mqtt part    every message the X2 sent is fed to the same parsers the server
               uses: list replies must yield what's in them (S-MQTT-LISTS), state
               pushes must parse (S-MQTT-STATE), presses must land (S-MQTT-UP)

The dump replaces the MAC with <MAC>, and our topics are built from whatever
MAC string we're given, so topics("<MAC>") matches the recording directly.

Until a recording exists the per-file tests skip; the self-test below records
one from the simulator so this replay code is itself tested.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sofabaton_mcp import protocol as p
from sofabaton_mcp.api import ServerAPI
from sofabaton_mcp.config import MqttSettings, Settings
from sofabaton_mcp.doctor import run_doctor
from sofabaton_mcp.mqtt import MqttHub
from sofabaton_mcp.sim.fake_server import FakeServer

from .conftest import HUB
from .test_openapi_contract import SCHEMAS, validator

pytestmark = pytest.mark.anyio

RECORDED = sorted((Path(__file__).parent / "fixtures" / "recorded").glob("*.json"))


def _schema_errors(component: str, value: Any, many: bool = False) -> list[str]:
    schema = {"type": "array", "items": {"$ref": f"#/components/schemas/{component}"}} if many else SCHEMAS[component]
    return [f"{component}: {e.message}" for e in validator(schema).iter_errors(value)]


async def check_recorded(doc: dict[str, Any]) -> list[str]:
    """Problems found replaying one doctor --dump. Empty means everything parsed."""
    problems: list[str] = []
    if server := doc.get("server"):
        problems += _schema_errors("HubInfo", server["info"])
        problems += _schema_errors("Activity", server["activities"], many=True)
        problems += _schema_errors("Device", server["devices"], many=True)
    mqtt = doc.get("mqtt")
    if not mqtt:
        return problems
    raw: list[dict[str, Any]] = mqtt["raw"]
    hub = MqttHub(MqttSettings("recorded", 1883, False, None, None, None), "<MAC>")

    async def replay(req: str, payload: Any, reply: str, match: Any, what: str) -> Any:
        for m in raw:
            if m["topic"] == hub.t[reply] and match(m["payload"]):
                return m["payload"]
        raise LookupError(what)

    hub.request = replay  # type: ignore[method-assign]
    seen = {m["topic"] for m in raw}

    acts = await hub.activities()
    if not acts:
        problems.append("activity/<MAC>/list: no activities parsed (S-MQTT-LISTS)")
    devs = await hub.devices()
    if not devs:
        problems.append("device/<MAC>/list: no devices parsed (S-MQTT-LISTS)")
    # Each list reply that has items must parse into the same number of entries.
    for key, call in (
        ("keys_list", hub.activity_keys),
        ("macros_list", hub.macros),
        ("favorites_list", hub.favorites),
        ("device_keys_list", hub.device_keys),
    ):
        if hub.t[key] not in seen:
            continue
        reply = next(m["payload"] for m in raw if m["topic"] == hub.t[key])
        owner = reply.get("device_id" if key == "device_keys_list" else "activity_id")
        try:
            parsed = await call(int(owner))
        except (LookupError, TypeError, ValueError) as exc:
            problems.append(f"{hub.t[key]}: reply doesn't echo its id at the top level ({exc!r})")
            continue
        if len(parsed) != len(p.items(reply)):
            problems.append(f"{hub.t[key]}: {len(p.items(reply))} items, parsed {len(parsed)} (S-MQTT-LISTS)")
    for m in raw:
        if m["topic"] == hub.t["state_up"] and p.unwrap_state(m["payload"]) is None:
            problems.append(f"activity_control_up didn't parse: {m['payload']!r} (S-MQTT-STATE)")
        if m["topic"] == hub.t["press_up"]:
            before = len(hub.presses(None, 200)["presses"])
            hub._on_press(m["payload"])
            if len(hub.presses(None, 200)["presses"]) == before:
                problems.append(f"<MAC>/up didn't parse: {m['payload']!r} (S-MQTT-UP)")
    return problems


@pytest.mark.skipif(not RECORDED, reason="no recordings yet: see the module docstring")
@pytest.mark.parametrize("path", RECORDED, ids=[r.name for r in RECORDED])
async def test_recorded(path: Path) -> None:
    assert await check_recorded(json.loads(path.read_text())) == []


async def test_replay_works_on_a_simulator_recording(broker, state, x2, tmp_path):
    """Self-test: record from the simulator exactly as you would at home, then replay it."""
    fake = FakeServer(state, hub_id=HUB)
    api = ServerAPI("http://sbx.test:8480", transport=fake.transport)
    m = MqttSettings(broker[0], broker[1], False, None, None, None)
    report = await run_doctor(Settings(url="http://sbx.test:8480", hub=None, mqtt=m), api=api, dump_dir=tmp_path)
    await api.aclose()
    assert report.ok and not report.warnings, report
    doc = json.loads((tmp_path / "sofabaton.json").read_text())
    topics = {m["topic"] for m in doc["mqtt"]["raw"]}
    assert {
        "activity/<MAC>/list",
        "activity/<MAC>/keys_list",
        "activity/<MAC>/macro_keys_list",
        "activity/<MAC>/favorites_keys_list",
        "device/<MAC>/list",
        "device/<MAC>/keys_list",
    } <= topics
    assert state.mac not in json.dumps(doc)
    # Add a state push and a press, as --listen would see them, and replay everything.
    doc["mqtt"]["raw"] += [
        {"topic": "activity/<MAC>/activity_control_up", "payload": {"activity_id": 255, "state": "off"}},
        {"topic": "<MAC>/up", "payload": {"device_id": 3, "key_id": 1}},
    ]
    assert await check_recorded(doc) == []


async def test_replay_catches_a_shape_we_dont_understand():
    doc = {
        "mqtt": {
            "raw": [
                {"topic": "activity/<MAC>/list", "payload": {"data": [{"id": 1, "title": "Watch TV"}]}},
                {"topic": "device/<MAC>/list", "payload": {"data": [{"device_id": 1, "device_name": "TV"}]}},
                {"topic": "<MAC>/up", "payload": {"device": 3, "key": 1}},
            ]
        }
    }
    problems = await check_recorded(doc)
    assert any("no activities parsed" in p for p in problems)
    assert any("<MAC>/up didn't parse" in p for p in problems)
