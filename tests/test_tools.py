"""MCP tools through a real in-process MCP client: the contract the model sees.

Two setups:
  mcp_client       SOFABATON_URL only (any model), against the fake sofabaton-x-server
  mcp_client_mqtt  an X2 over MQTT only (SOFABATON_URL=none), against the broker + fake X2

What matters here is what the *model* gets back: structured results it can
branch on (outcome, via), and, when something can't work on this hub, an error
or limitation that says why and what would fix it.
"""

from __future__ import annotations

import pytest
from mcp import Client

from sofabaton_mcp import server
from sofabaton_mcp.api import ServerAPI
from sofabaton_mcp.config import ALLOWED_BUTTONS
from sofabaton_mcp.sim.fake_server import FakeServer
from sofabaton_mcp.sim.hub_state import load_state

from .conftest import HUB, SHIELD, WATCH_SHIELD, eventually

pytestmark = pytest.mark.anyio

TOOL_NAMES = {
    "get_status",
    "list_activities",
    "list_devices",
    "list_commands",
    "start_activity",
    "power_off",
    "press_button",
    "send_command",
    "find_remote",
    "get_recent_presses",
}


def use_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeServer) -> None:
    # The lifespan builds a ServerAPI from SOFABATON_URL; give it the fake transport.
    monkeypatch.setattr(server, "ServerAPI", lambda url: ServerAPI(url, transport=fake.transport))


@pytest.fixture
async def mcp_client(env, fake, monkeypatch):
    use_fake(monkeypatch, fake)
    async with Client(server.mcp) as c:
        yield c


@pytest.fixture
async def mcp_client_mqtt(env, monkeypatch, broker, state, x2):
    monkeypatch.setenv("SOFABATON_URL", "none")
    monkeypatch.setenv("SOFABATON_MQTT_URL", f"mqtt://{broker[0]}:{broker[1]}")
    monkeypatch.setenv("SOFABATON_MQTT_MAC", state.mac)
    async with Client(server.mcp) as c:
        mqtt = server.client().mqtt
        await eventually(lambda: mqtt is not None and mqtt.connected)
        yield c


async def tools(c: Client) -> dict:
    return {t.name: t for t in (await c.list_tools()).tools}


def text(result) -> str:
    return result.content[0].text


async def call(c: Client, name: str, args: dict | None = None) -> dict:
    result = await c.call_tool(name, args or {})
    assert not result.is_error, text(result)
    return result.structured_content


# --- tools/list ----------------------------------------------------------------------------
async def test_tool_names(mcp_client):
    assert set(await tools(mcp_client)) == TOOL_NAMES


async def test_every_tool_has_title_description_and_annotations(mcp_client):
    for t in (await tools(mcp_client)).values():
        assert t.title and t.description, t.name
        a = t.annotations
        assert a is not None and a.read_only_hint is not None and a.open_world_hint is False, t.name
        if not a.read_only_hint:
            # Nothing here can edit the hub, so nothing is destructive.
            assert a.destructive_hint is False and a.idempotent_hint is not None, t.name


async def test_annotations_match_behavior(mcp_client):
    t = await tools(mcp_client)
    for name in ("get_status", "list_activities", "list_devices", "list_commands", "get_recent_presses"):
        assert t[name].annotations.read_only_hint is True, name
    # Starting what's running, or powering off when nothing is, changes nothing: idempotent.
    assert t["start_activity"].annotations.idempotent_hint is True
    assert t["power_off"].annotations.idempotent_hint is True
    # Two VOL_UPs are not one.
    assert t["press_button"].annotations.idempotent_hint is False
    assert t["send_command"].annotations.idempotent_hint is False


async def test_press_button_schema_is_the_allow_list(mcp_client):
    props = (await tools(mcp_client))["press_button"].input_schema["properties"]
    assert set(props["button"]["enum"]) == ALLOWED_BUTTONS
    assert (props["repeat"]["minimum"], props["repeat"]["maximum"]) == (1, 10)
    assert (props["delay_ms"]["anyOf"][0]["minimum"], props["delay_ms"]["anyOf"][0]["maximum"]) == (100, 2000)


async def test_output_schemas_let_the_model_plan(mcp_client):
    t = await tools(mcp_client)
    status = t["get_status"].output_schema
    assert {"model", "via", "capabilities", "limitations", "running_activity", "transition"} <= set(status["required"])
    action = t["start_activity"].output_schema
    assert action["properties"]["outcome"]["enum"] == ["done", "unchanged", "dry_run"]
    assert action["properties"]["via"]["enum"] == ["server", "mqtt"]


async def test_instructions_say_mqtt_is_x2_only(mcp_client):
    instructions = mcp_client.instructions or ""
    assert "X1, X1S or X2" in instructions and "exist only on the X2" in instructions


# --- over sofabaton-x-server (any model) -----------------------------------------------------
async def test_get_status(mcp_client, fake):
    fake.running = WATCH_SHIELD
    st = await call(mcp_client, "get_status")
    assert (st["model"], st["via"], st["running_activity"]) == ("X2", "server", "Watch Shield")
    assert st["server"]["mode"] == "control" and st["mqtt"] is None
    # An X2 without MQTT: the model is told what MQTT would add.
    assert any("MQTT isn't set up" in lim for lim in st["limitations"])


async def test_start_then_press_through_the_activity(mcp_client, fake):
    r = await call(mcp_client, "start_activity", {"activity": "Watch Shield"})
    assert (r["outcome"], r["detail"], r["via"]) == ("done", "Start Watch Shield", "server")
    assert r["sent"] == [f"POST /api/v1/hubs/{HUB}/activities/{WATCH_SHIELD}/start"]
    r = await call(mcp_client, "press_button", {"button": "VOL_UP", "repeat": 2})
    assert r["detail"] == "Press VOL_UP x2 on activity Watch Shield"
    assert fake.sends() == [(WATCH_SHIELD, 182)] * 2


async def test_starting_the_running_activity_is_unchanged(mcp_client, fake):
    fake.running = WATCH_SHIELD
    r = await call(mcp_client, "start_activity", {"activity": "watch shield"})
    assert (r["outcome"], r["sent"]) == ("unchanged", []) and fake.posts == []


async def test_send_command_finds_a_favorite(mcp_client, fake):
    fake.running = WATCH_SHIELD
    r = await call(mcp_client, "send_command", {"command": "netflix"})
    assert r["detail"] == "Send Netflix x1 via activity Watch Shield"
    assert fake.sends() == [(SHIELD, 9)]


async def test_list_commands_says_what_and_where(mcp_client, fake):
    fake.running = WATCH_SHIELD
    r = await call(mcp_client, "list_commands")
    assert r == {
        "source": "activity Watch Shield",
        "commands": [
            {"label": "Movie Mode", "kind": "macro", "via": "activity"},
            {"label": "Netflix", "kind": "favorite", "via": "Shield"},
        ],
    }


async def test_list_activities_and_devices(mcp_client, fake):
    fake.running = WATCH_SHIELD
    acts = (await call(mcp_client, "list_activities"))["result"]
    assert {"name": "Watch Shield", "running": True} in acts
    devs = (await call(mcp_client, "list_devices"))["result"]
    assert {d["name"] for d in devs} >= {"Onkyo Receiver", "Shield"}


@pytest.mark.parametrize(
    "args",
    [
        {"button": "POWER_OFF"},  # not in the enum: the SDK rejects it before our code runs
        {"button": "VOL_UP", "repeat": 11},
        {"button": "VOL_UP", "delay_ms": 50},
        {"button": "VOL_UP", "target": "Xbox"},
        {"button": "VOL_UP"},  # nothing running and no target
    ],
)
async def test_bad_presses_never_reach_the_server(mcp_client, fake, args):
    result = await mcp_client.call_tool("press_button", args)
    assert result.is_error
    assert fake.posts == []


async def test_observe_mode_is_explained(mcp_client, fake):
    fake.mode = "observe"
    result = await mcp_client.call_tool("find_remote", {})
    assert result.is_error and "close the app" in text(result)
    assert "SOFABATON_MQTT_URL" in text(result)  # an X2: MQTT would keep it controllable


async def test_recent_presses_pass_through_extra_fields_safely(mcp_client, fake):
    # The real server sends more fields than our TypedDict declares.
    fake.presses = [
        {
            "seq": 1,
            "hub_id": HUB,
            "device_id": 9,
            "command_id": 2,
            "slot": 2,
            "label": "Ask Claude",
            "press_type": "short",
            "resolution": "resolved",
            "transport": "mqtt",
            "source": "192.0.2.40",
            "received_at": "2026-10-07T20:00:00Z",
            "device_key": "claude",
        }
    ]
    page = await call(mcp_client, "get_recent_presses")
    assert page["presses"][0]["label"] == "Ask Claude" and page["last_seq"] == 1


async def test_unreachable_server_is_reported_not_raised(mcp_client, fake):
    # get_status is where the model looks first, so it answers even when nothing else can.
    fake.unreachable = True
    st = await call(mcp_client, "get_status")
    assert st["via"] is None and st["server"]["reachable"] is False
    assert any("Can't reach sofabaton-x-server" in lim for lim in st["limitations"])
    result = await mcp_client.call_tool("start_activity", {"activity": "Watch Shield"})
    assert result.is_error and "Can't reach sofabaton-x-server" in text(result)


@pytest.mark.parametrize("model", ["X1", "X1S"])
async def test_x1_family_through_mcp(env, monkeypatch, model):
    fake = FakeServer(load_state(model=model), hub_id=HUB)
    use_fake(monkeypatch, fake)
    async with Client(server.mcp) as c:
        st = await call(c, "get_status")
        assert st["model"] == model and "live_activity_state" not in st["capabilities"]
        assert any("need an X2" in lim for lim in st["limitations"])
        r = await call(c, "start_activity", {"activity": "Listen to Music"})
        assert (r["outcome"], r["via"]) == ("done", "server")


async def test_dry_run_sends_nothing(env, fake, monkeypatch):
    monkeypatch.setenv("SOFABATON_DRY_RUN", "1")
    use_fake(monkeypatch, fake)
    async with Client(server.mcp) as c:
        r = await call(c, "start_activity", {"activity": "Watch Shield"})
        assert r["outcome"] == "dry_run" and r["detail"].startswith("DRY RUN, nothing sent")
        assert r["sent"] == [f"POST /api/v1/hubs/{HUB}/activities/{WATCH_SHIELD}/start"]
        assert fake.posts == []
        assert (await call(c, "get_status"))["running_activity"] is None


# --- an X2 over MQTT only --------------------------------------------------------------------
async def test_mqtt_only_status(mcp_client_mqtt):
    st = await call(mcp_client_mqtt, "get_status")
    assert (st["model"], st["via"], st["server"]) == ("X2", "mqtt", None)
    assert "live_activity_state" in st["capabilities"] and "find_remote" not in st["capabilities"]


async def test_mqtt_only_start_and_send(mcp_client_mqtt, state):
    r = await call(mcp_client_mqtt, "start_activity", {"activity": "Watch Shield"})
    assert (r["outcome"], r["via"]) == ("done", "mqtt")
    assert r["sent"][0].startswith("publish activity/<MAC>/activity_control_down")
    assert state.mac not in " ".join(r["sent"])  # the MAC is never echoed to the model
    assert (await call(mcp_client_mqtt, "get_status"))["running_activity"] == "Watch Shield"


async def test_mqtt_only_find_remote_says_why_not(mcp_client_mqtt):
    result = await mcp_client_mqtt.call_tool("find_remote", {})
    assert result.is_error and "find_remote needs sofabaton-x-server" in text(result)
