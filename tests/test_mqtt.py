"""The X2 over MQTT: alone, and alongside sofabaton-x-server. Real sockets, real MQTT client.

Our client (aiomqtt/paho) <-> a broker <-> the fake X2 (also an MQTT client).
The broker is the in-package one, or a real one with MQTT_TEST_BROKER set
(CI runs mosquitto). Each test uses its own random MAC, so runs against a
shared broker can't hear each other.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.config import MqttSettings, Settings
from sofabaton_mcp.sim.broker import topic_matches
from sofabaton_mcp.sim.fake_server import FakeServer
from sofabaton_mcp.sim.hub_state import Executed, FakeHubState, load_state

from .conftest import HUB, SHIELD, WATCH_SHIELD, eventually, in_package

pytestmark = pytest.mark.anyio


@pytest.fixture
async def make(broker, state):
    made: list[SofabatonClient] = []

    async def build(
        *, server: FakeServer | None = None, mac: str | None = "auto", password: str | None = None, **settings
    ) -> SofabatonClient:
        m = MqttSettings(
            broker[0], broker[1], False, "u" if password else None, password, state.mac if mac == "auto" else mac
        )
        api = ServerAPI("http://sbx.test:8480", transport=server.transport) if server else None
        c = SofabatonClient(Settings(url="http://sbx.test:8480" if server else None, hub=None, mqtt=m, **settings), api)
        await c.start()
        made.append(c)
        return c

    yield build
    for c in made:
        await c.stop()


async def connected(c: SofabatonClient) -> SofabatonClient:
    await eventually(lambda: c.mqtt is not None and c.mqtt.connected)
    return c


def executed(state: FakeHubState) -> list[tuple[str, str, int, int | None]]:
    return [(e.via, e.what, e.entity_id, e.command_id) for e in state.executed]


# --- MQTT only ------------------------------------------------------------------------------
async def test_mqtt_only_status_and_capabilities(make, x2):
    c = await connected(await make())
    st = await c.status()
    assert (st["model"], st["via"], st["server"]) == ("X2", "mqtt", None)
    assert {"activities", "buttons", "commands", "presses", "live_activity_state", "control_while_app_open"} <= set(
        st["capabilities"]
    )
    assert "find_remote" not in st["capabilities"]
    assert any("No sofabaton-x-server" in lim for lim in st["limitations"])


async def test_catalog_over_mqtt(make, x2):
    """S-MQTT-LISTS"""
    c = await connected(await make())
    assert await c.activities() == [("Watch Shield", False), ("Listen to Music", False)]
    assert ("Onkyo Receiver", None, None) in await c.devices()  # MQTT gives names only
    source, opts = await c.commands("Watch Shield")
    assert [(o.label, o.kind, o.via) for o in opts] == [
        ("Movie Mode", "macro", "activity"),
        ("Netflix", "favorite", "Shield"),
    ]


async def test_start_confirmed_by_the_hubs_own_announcement(make, x2, state):
    """S-MQTT-CONTROL, S-MQTT-STATE, S-MQTT-IDS: MQTT ids come from MQTT lists, never from REST."""
    c = await connected(await make())
    outcome, name = await c.start_activity("Watch Shield")
    assert outcome.changed and outcome.via == "mqtt" and "power macro" in outcome.note
    assert executed(state) == [("mqtt", "start", WATCH_SHIELD, None)]  # the fake translated MQTT id 1101 back
    assert outcome.sent == [
        'publish activity/<MAC>/activity_control_down {"data": {"activity_id": 1101, "state": "on"}}'
    ]
    assert (await c.status())["running_activity"] == "Watch Shield"


async def test_presses_wait_out_the_power_macro(make, x2, state):
    """S-MQTT-SETTLE: no 'macro finished' signal over MQTT, so a window."""
    c = await connected(await make(settle_s=30))
    await c.start_activity("Watch Shield")
    with pytest.raises(SofabatonError, match="power macro may still be running.*Try again in 3\\ds"):
        await c.press("VOL_UP", None)
    assert (await c.status())["transition"] is not None
    assert [e.what for e in state.executed] == ["start"]


async def test_buttons_macros_favorites_and_device_commands_over_mqtt(make, x2, state):
    """S-MQTT-KEYS, S-MQTT-MACRO, S-MQTT-FAVORITE (device id in activity_id), S-MQTT-DEVICE"""
    c = await connected(await make(settle_s=0))
    await c.start_activity("Watch Shield")
    await c.press("VOL_UP", None, repeat=2)
    await c.send_command("Movie Mode", None)
    await c.send_command("Netflix", None)
    await c.send_command("Input HDMI 2", "Onkyo Receiver")
    await eventually(lambda: len(state.executed) == 6)
    assert executed(state)[1:] == [
        ("mqtt", "key", WATCH_SHIELD, 182),
        ("mqtt", "key", WATCH_SHIELD, 182),
        ("mqtt", "macro", WATCH_SHIELD, 40),
        ("mqtt", "favorite", SHIELD, 9),
        ("mqtt", "device_key", 1, 7),
    ]


async def test_hard_buttons_to_a_device_are_refused_over_mqtt(make, x2):
    c = await connected(await make(settle_s=0))
    with pytest.raises(SofabatonError, match="takes remote buttons through an activity"):
        await c.press("VOL_UP", "Onkyo Receiver")


async def test_power_off_and_the_remotes_off_key(make, x2, state):
    c = await connected(await make(settle_s=0))
    await c.start_activity("Listen to Music")
    outcome, name = await c.power_off()
    assert outcome.changed and name == "Listen to Music" and state.running is None
    await c.start_activity("Watch Shield")
    state.press_remote_off()  # publishes activity_id 255
    await eventually(lambda: c.mqtt is not None and c.mqtt.current is None)


async def test_wifi_device_presses_over_mqtt(make, x2, state):
    """S-MQTT-UP"""
    c = await connected(await make())
    state.press_virtual_key(3, 2)  # "Bedtime" on the Ask Claude virtual device
    await eventually(lambda: c.mqtt is not None and c.mqtt.presses(None, 10)["last_seq"] == 1)
    page = await c.presses(None, 10)
    assert page["presses"][0]["device_id"] == 1003 and page["presses"][0]["command_id"] == 2  # MQTT ids
    assert page["presses"][0]["transport"] == "mqtt"


async def test_retained_announcements_are_ignored(make, x2, state, broker):
    """The hub never retains; a retained activity change is a replay and must not set state."""
    b = in_package(broker)
    await b.publish(f"activity/{state.mac}/activity_control_up", b'{"activity_id": 1101, "state": "on"}', retain=True)
    c = await connected(await make())
    await asyncio.sleep(0.2)
    assert c.mqtt is not None and c.mqtt.state_known is False


async def test_a_silent_x2_times_out_with_advice(make, x2):
    """S-MQTT-REPLY-TIMEOUT"""
    x2.faults.silent = {"list_request"}
    c = await connected(await make())
    with pytest.raises(SofabatonError, match="didn't answer the activity list request.*Connect to Home Assistant"):
        await c.activities()


async def test_a_malformed_reply_is_dropped_and_times_out(make, x2):
    x2.faults.malformed = {"list"}
    c = await connected(await make())
    with pytest.raises(SofabatonError, match="didn't answer"):
        await c.activities()


async def test_without_an_announcement_the_list_confirms(make, x2, state, caplog):
    x2.faults.no_state_push = True
    c = await connected(await make())
    outcome, _ = await c.start_activity("Watch Shield")
    assert outcome.changed and "activity list confirms it" in caplog.text


async def test_rejected_broker_login(make, x2, broker):
    b = in_package(broker)
    b.users = {"u": "right"}
    c = await make(password="wrong")
    await eventually(lambda: c.mqtt is not None and c.mqtt.auth_failed)
    st = await c.status()
    assert st["mqtt"] is not None and st["mqtt"]["login_rejected"] is True and st["via"] is None
    with pytest.raises(SofabatonError, match="rejected the login"):
        await c.start_activity("Watch Shield")


async def test_reconnects_after_a_broker_restart(make, x2, broker):
    b = in_package(broker)
    c = await connected(await make())
    await b.kick()  # drops the fake X2 too; it doesn't reconnect, so only check our side
    await eventually(lambda: c.mqtt is not None and not c.mqtt.connected, timeout=2)
    await eventually(lambda: c.mqtt is not None and c.mqtt.connected, timeout=5)


async def test_dry_run_over_mqtt_publishes_nothing(make, x2, state, broker):
    c = await connected(await make(dry_run=True))
    outcome, _ = await c.start_activity("Watch Shield")
    assert outcome.dry_run and not outcome.changed and state.executed == []
    assert outcome.sent[0].startswith("publish activity/<MAC>/activity_control_down")


# --- X2 with both sofabaton-x-server and MQTT ---------------------------------------------------
@pytest.fixture
def server(state) -> FakeServer:
    return FakeServer(state, hub_id=HUB)


async def test_hybrid_uses_the_server_and_has_every_capability(make, x2, server):
    c = await connected(await make(server=server))
    st = await c.status()
    assert st["via"] == "server"
    assert {"find_remote", "hub_info", "live_activity_state", "control_while_app_open"} <= set(st["capabilities"])
    assert st["limitations"] == []


async def test_hybrid_keeps_control_while_the_app_is_open(make, x2, server, state):
    """The headline X2 feature: the app holds the proxy (observe), MQTT takes over."""
    c = await connected(await make(server=server, settle_s=0))
    server.mode = "observe"
    assert (await c.status())["via"] == "mqtt"
    outcome, _ = await c.start_activity("Listen to Music")
    assert outcome.via == "mqtt" and state.executed[-1] == Executed("mqtt", "start", 102)
    await c.press("VOL_UP", None)
    await eventually(lambda: state.executed[-1] == Executed("mqtt", "key", 102, 182))


async def test_hybrid_learns_the_mac_from_the_server(make, x2, server, monkeypatch):
    monkeypatch.setattr(SofabatonClient, "identity_retry", 0.05)
    c = await make(server=server, mac=None)
    await connected(c)
    assert c.mqtt is not None and c.mqtt.mac == server.state.mac


async def test_mqtt_on_an_x1s_is_turned_off_with_a_clear_reason(make, broker, monkeypatch):
    monkeypatch.setattr(SofabatonClient, "identity_retry", 0.05)
    server = FakeServer(load_state(model="X1S"), hub_id=HUB)
    c = await make(server=server, mac=None)
    await eventually(lambda: c.mqtt_blocked is not None)
    st = await c.status()
    assert c.mqtt is None and "live_activity_state" not in st["capabilities"]
    assert any("this hub is an X1S" in lim or "This is an X1S" in lim for lim in st["limitations"])


async def test_hybrid_reads_over_mqtt_when_the_server_is_down(make, x2, server):
    c = await connected(await make(server=server))
    server.unreachable = True
    assert await c.activities() == [("Watch Shield", False), ("Listen to Music", False)]
    st = await c.status()
    assert st["via"] == "mqtt" and any("Can't reach sofabaton-x-server" in lim for lim in st["limitations"])


# --- the broker itself ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pattern,topic,expected",
    [
        ("activity/+/list", "activity/ABC/list", True),
        ("activity/+/list", "activity/ABC/list/x", False),
        ("activity/#", "activity/ABC/list", True),
        ("#", "activity/ABC", True),
        ("+/up", "ABC/up", True),
        ("+/up", "$SYS/up", False),
        ("a/b", "a/b", True),
        ("a/b", "a/c", False),
    ],
)
def test_topic_matching(pattern, topic, expected):
    assert topic_matches(pattern, topic) is expected


async def test_requests_are_serialized_one_at_a_time(make, x2):
    """S-MQTT-SERIAL: concurrent tool calls don't interleave requests (replies carry no request id)."""
    c = await connected(await make())
    results = await asyncio.gather(c.activities(), c.devices(), c.commands("Watch Shield"))
    assert len(results[0]) == 2 and len(results[1]) == 3 and results[2][0] == "activity Watch Shield"
    topics = [t.rsplit("/", 1)[-1] for t, _ in x2.received]
    assert topics.count("list_request") == 4  # activities, devices, and the two lookups commands() needed
    assert json.dumps(x2.received[0][1]) == '{"data": "activity_list"}'
