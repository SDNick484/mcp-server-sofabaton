"""MCP tools through a real in-process MCP client: the contract the model sees."""

from __future__ import annotations

import pytest
from mcp import Client

from sofabaton_mcp import server
from sofabaton_mcp.api import ServerAPI
from sofabaton_mcp.config import ALLOWED_BUTTONS

from .conftest import SHIELD, WATCH_SHIELD

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


@pytest.fixture
async def mcp_client(env, fake, monkeypatch):
    # The lifespan builds a ServerAPI from SOFABATON_URL; give it the fake transport.
    monkeypatch.setattr(server, "ServerAPI", lambda url: ServerAPI(url, transport=fake.transport))
    async with Client(server.mcp) as c:
        yield c


async def tools(c: Client) -> dict:
    return {t.name: t for t in (await c.list_tools()).tools}


def text(result) -> str:
    return result.content[0].text


# --- tools/list --------------------------------------------------------------
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
    assert t["start_activity"].annotations.idempotent_hint is True
    assert t["power_off"].annotations.idempotent_hint is True
    assert t["press_button"].annotations.idempotent_hint is False


async def test_press_button_schema_is_the_allow_list(mcp_client):
    props = (await tools(mcp_client))["press_button"].input_schema["properties"]
    assert set(props["button"]["enum"]) == ALLOWED_BUTTONS
    assert (props["repeat"]["minimum"], props["repeat"]["maximum"]) == (1, 10)


async def test_get_status_publishes_output_schema(mcp_client):
    schema = (await tools(mcp_client))["get_status"].output_schema
    assert {"mode", "controllable", "running_activity", "hub_connected"} <= set(schema["required"])


# --- tools/call --------------------------------------------------------------
async def test_get_status(mcp_client, fake):
    fake.running = WATCH_SHIELD
    st = (await mcp_client.call_tool("get_status", {})).structured_content
    assert st["server_url"] == "http://sbx.test:8480"
    assert st["running_activity"] == {"id": WATCH_SHIELD, "name": "Watch Shield"}


async def test_start_then_press_through_the_activity(mcp_client, fake):
    assert text(await mcp_client.call_tool("start_activity", {"activity": "Watch Shield"})) == "Started Watch Shield"
    result = await mcp_client.call_tool("press_button", {"button": "VOL_UP", "repeat": 2})
    assert text(result) == "Pressed VOL_UP x2 on activity Watch Shield"
    assert fake.sends() == [(WATCH_SHIELD, 182)] * 2


async def test_send_command_finds_a_favorite(mcp_client, fake):
    fake.running = WATCH_SHIELD
    result = await mcp_client.call_tool("send_command", {"command": "netflix"})
    assert text(result) == "Sent Netflix x1 via activity Watch Shield"
    assert fake.sends() == [(SHIELD, 9)]


async def test_list_commands_says_where_each_goes(mcp_client, fake):
    fake.running = WATCH_SHIELD
    rows = (await mcp_client.call_tool("list_commands", {})).structured_content["result"]
    assert rows == [{"label": "Movie Mode", "via": "activity"}, {"label": "Netflix", "via": "Shield"}]


@pytest.mark.parametrize(
    "args",
    [
        {"button": "POWER_OFF"},
        {"button": "VOL_UP", "repeat": 11},
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


async def test_recent_presses_pass_through_extra_fields_safely(mcp_client, fake):
    # The real server sends more fields than our TypedDict declares.
    fake.presses = [
        {
            "seq": 1,
            "hub_id": "a1b2c3",
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
    page = (await mcp_client.call_tool("get_recent_presses", {})).structured_content
    assert page["presses"][0]["label"] == "Ask Claude" and page["last_seq"] == 1


async def test_unreachable_server_explains_itself(mcp_client, fake):
    fake.unreachable = True
    result = await mcp_client.call_tool("get_status", {})
    assert result.is_error and "Can't reach sofabaton-x-server" in text(result)
