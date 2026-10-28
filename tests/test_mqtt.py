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
from sofabaton_mcp.mqtt import MqttHub
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


async def test_the_first_call_waits_for_the_broker(make, x2, monkeypatch):
    """A client may call get_status the instant it launches us; don't answer 'connecting'."""
    monkeypatch.setattr(SofabatonClient, "mqtt_startup_grace", 3.0)
    c = await make()  # no connected() wait
    assert (await c.status())["via"] == "mqtt"


async def test_later_calls_dont_wait(make, broker, state, monkeypatch):
    monkeypatch.setattr(SofabatonClient, "mqtt_startup_grace", 0.2)
    c = await make()  # no fake X2 needed: we only look at the path choice
    in_package(broker)
    await broker[2].stop()  # the broker goes away before we ever connect... (in-package only)
    await c.status()  # ...the first call waits out the grace,
    loop = asyncio.get_running_loop()
    t = loop.time()
    st = await c.status()  # and the next one answers at once
    assert loop.time() - t < 0.15 and st["via"] is None


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


async def test_buttons_macros_and_favorites_over_mqtt(make, x2, state):
    """S-MQTT-KEYS, S-MQTT-MACRO, S-MQTT-FAVORITE (device id in activity_id)"""
    c = await connected(await make(settle_s=0))
    await c.start_activity("Watch Shield")
    await c.press("VOL_UP", None, repeat=2)
    await c.send_command("Movie Mode", None)
    await c.send_command("Netflix", None)
    await eventually(lambda: len(state.executed) == 5)
    assert executed(state)[1:] == [
        ("mqtt", "key", WATCH_SHIELD, 182),
        ("mqtt", "key", WATCH_SHIELD, 182),
        ("mqtt", "macro", WATCH_SHIELD, 40),
        ("mqtt", "favorite", SHIELD, 9),
    ]


async def test_device_commands_over_mqtt_say_why_not_at_once(make, x2):
    """S-MQTT-DEVICE-KEYS: a real X2 never answers device/<MAC>/keys_request, so don't wait 5 s to find out."""
    c = await connected(await make(settle_s=0))
    loop = asyncio.get_running_loop()
    t = loop.time()
    with pytest.raises(SofabatonError, match="doesn't list a device's own commands.*set up sofabaton-x-server"):
        await c.send_command("Input HDMI 2", "Onkyo Receiver")
    with pytest.raises(SofabatonError, match="doesn't list a device's own commands"):
        await c.commands("Onkyo Receiver")
    assert loop.time() - t < 0.5
    assert not [t for t, _ in x2.received if t.endswith("/keys_request") and t.startswith("device/")]
    assert any("a device's own commands" in lim for lim in (await c.status())["limitations"])
    # An activity's commands still work by name:
    _, opts = await c.commands("Watch Shield")
    assert {o.label for o in opts} == {"Movie Mode", "Netflix"}


async def test_device_commands_over_mqtt_if_a_firmware_lists_them(make, x2, state, monkeypatch):
    """If an X2 ever answers (doctor reports it), flipping the flag brings back S-MQTT-DEVICE sends."""
    from sofabaton_mcp import protocol

    monkeypatch.setattr(protocol, "DEVICE_KEYS_OVER_MQTT", True)
    x2.answers_device_keys = True
    c = await connected(await make(settle_s=0))
    await c.send_command("Input HDMI 2", "Onkyo Receiver")
    await eventually(lambda: executed(state) == [("mqtt", "device_key", 1, 7)])
    assert not any("a device's own commands" in lim for lim in (await c.status())["limitations"])


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


# --- recovering a dropped connection before sending -------------------------------------------
def hub_for(broker, state, *, password: str | None = None, factory=None) -> MqttHub:
    m = MqttSettings(broker[0], broker[1], False, "u" if password else None, password, state.mac)
    return MqttHub(m, state.mac, client_factory=factory)


async def test_ensure_connected_wakes_a_hub_sleeping_in_backoff(broker, state, monkeypatch):
    """With a 30 s backoff, only the wake-up can explain a reconnect within the 2 s ensure_connected allows."""
    b = in_package(broker)
    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)
    hub = hub_for(broker, state)
    await hub.start()
    try:
        await eventually(lambda: hub.connected)
        await b.kick()
        await eventually(lambda: not hub.connected, timeout=2)
        loop = asyncio.get_running_loop()
        t = loop.time()
        await hub.ensure_connected()
        assert hub.connected and loop.time() - t < 1.5
    finally:
        await hub.stop()


async def test_publish_after_a_drop_reconnects_first(broker, state, monkeypatch):
    b = in_package(broker)
    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)
    hub = hub_for(broker, state)
    await hub.start()
    try:
        await eventually(lambda: hub.connected)
        await b.kick()
        await eventually(lambda: not hub.connected, timeout=2)
        await hub.publish("key_ctl", {"data": 1})
        await eventually(lambda: [m.topic for m in b.published].count(hub.t["key_ctl"]) == 1)  # QoS 0: async
    finally:
        await hub.stop()


async def test_ensure_connected_doesnt_hammer_a_broker_that_rejected_the_login(broker, state):
    b = in_package(broker)
    b.users = {"u": "right"}
    hub = hub_for(broker, state, password="wrong")
    await hub.start()
    try:
        await eventually(lambda: hub.auth_failed)
        loop = asyncio.get_running_loop()
        t = loop.time()
        with pytest.raises(SofabatonError, match="rejected the login"):
            await hub.ensure_connected()
        assert loop.time() - t < 0.5  # no 2 s wait, and (auth_retry_delay) no extra login attempt
    finally:
        await hub.stop()


async def test_a_failed_publish_is_reported_and_the_connection_replaced(broker, state, monkeypatch):
    """The wedge this guards against: aiomqtt still thinks it's connected, we've marked it down, and nothing
    ever sets it up again. The reconnect request has to end the old connection, not just shorten a sleep."""
    import aiomqtt

    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)  # only the reconnect request can bring it back in time
    made: list[object] = []
    failures = 1

    class FlakyPublish(aiomqtt.Client):
        async def publish(self, *args, **kwargs):
            nonlocal failures
            if failures:
                failures -= 1
                raise aiomqtt.MqttError("simulated publish failure")
            return await super().publish(*args, **kwargs)

    def factory(*args, **kwargs):
        made.append(object())
        return FlakyPublish(*args, **kwargs)

    hub = hub_for(broker, state, factory=factory)
    await hub.start()
    try:
        await eventually(lambda: hub.connected)
        with pytest.raises(SofabatonError, match=r"MQTT publish failed \(simulated publish failure\)"):
            await hub.publish("key_ctl", {"data": 1})
        await eventually(lambda: hub.connected and len(made) == 2, timeout=3)
        await hub.publish("key_ctl", {"data": 2})
    finally:
        await hub.stop()


async def test_a_press_after_an_idle_drop_recovers_the_path(make, x2, broker, monkeypatch):
    b = in_package(broker)
    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)
    c = await connected(await make())
    await b.kick()
    await eventually(lambda: c.mqtt is not None and not c.mqtt.connected, timeout=2)
    assert await c._control_path() == "mqtt"
    assert c.mqtt is not None and c.mqtt.connected


async def test_status_reports_a_dropped_connection_without_waiting_for_it(make, x2, broker, monkeypatch):
    b = in_package(broker)
    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)
    c = await connected(await make())
    await c.status()  # the first call may wait out the startup grace (test_the_first_call_waits_for_the_broker)
    await b.kick()
    await eventually(lambda: c.mqtt is not None and not c.mqtt.connected, timeout=2)
    loop = asyncio.get_running_loop()
    t = loop.time()
    st = await c.status()
    assert loop.time() - t < 0.5 and st["via"] is None and st["mqtt"]["connected"] is False


def _drop_publishes_from(c: SofabatonClient, monkeypatch, n: int) -> None:
    """From the n-th publish on (1-based), the hub fails the way a lost broker connection does."""
    assert c.mqtt is not None
    real, calls = c.mqtt.publish, 0

    async def dropping(key, payload):
        nonlocal calls
        calls += 1
        if calls >= n:
            raise SofabatonError("MQTT publish failed (Not connected)")
        await real(key, payload)

    monkeypatch.setattr(c.mqtt, "publish", dropping)


async def test_an_mqtt_burst_cut_by_a_drop_says_how_many_presses_went_out(make, x2, state, monkeypatch):
    c = await connected(await make(settle_s=0))
    await c.start_activity("Watch Shield")
    _drop_publishes_from(c, monkeypatch, 3)
    with pytest.raises(SofabatonError, match=r"Sent 2 of 5 presses before disconnect: MQTT publish failed"):
        await c.press("VOL_UP", None, repeat=5)
    await eventually(lambda: len(state.executed) == 3)  # the start, then the two presses that got out
    assert executed(state)[1:] == [("mqtt", "key", WATCH_SHIELD, 182)] * 2


async def test_an_mqtt_burst_failing_on_the_first_press_keeps_the_original_error(make, x2, state, monkeypatch):
    c = await connected(await make(settle_s=0))
    await c.start_activity("Watch Shield")
    _drop_publishes_from(c, monkeypatch, 1)
    with pytest.raises(SofabatonError) as err:
        await c.press("VOL_UP", None, repeat=5)
    assert str(err.value) == "MQTT publish failed (Not connected)"
    assert [e.what for e in state.executed] == ["start"]


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
    st = await c.status()
    assert st["via"] == "mqtt"
    # The model is told what still works and what waits for the app to close.
    assert "control_while_app_open" in st["capabilities"] and "find_remote" not in st["capabilities"]
    assert any("commands go over MQTT meanwhile" in lim for lim in st["limitations"])
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


# --- shutdown --------------------------------------------------------------------------------------
class SwallowingClient:
    """An MQTT client whose subscribe can drop a cancellation, as asyncio.wait_for does on Python 3.11.

    aiomqtt waits for every acknowledgement with asyncio.wait_for. On 3.11, if the awaited reply and a
    cancellation land in the same loop iteration, wait_for returns the reply and the cancel is lost
    (CPython PR #28149; fixed in 3.12). CI hit it about one run in three: `call` with bad arguments
    exits at once, so shutdown cancelled the MQTT task mid-subscribe, the task carried on into its
    message loop, and stop() waited for it forever.
    """

    def __init__(self, *_: object, swallow_all: bool = False, **__: object) -> None:
        self.swallowed = 0
        self.swallow_all = swallow_all
        self._queue: asyncio.Queue[object] = asyncio.Queue()

    async def __aenter__(self) -> SwallowingClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def subscribe(self, topic: str, qos: int = 0) -> None:
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            if self.swallowed and not self.swallow_all:
                raise
            self.swallowed += 1  # the reply "won": the cancel is dropped

    @property
    def messages(self):  # noqa: ANN201
        return self._iterate()

    async def _iterate(self):  # noqa: ANN202
        while True:
            try:
                yield await self._queue.get()
            except asyncio.CancelledError:
                if not self.swallow_all:
                    raise


def _hub(client: SwallowingClient) -> MqttHub:
    return MqttHub(MqttSettings("broker", 1883, False, None, None, None), "02AB34CD56EF", lambda *a, **k: client)


async def test_stop_survives_a_dropped_cancellation():
    client = SwallowingClient()
    hub = _hub(client)
    await hub.start()
    await asyncio.sleep(0.05)  # mid-subscribe, where 3.11 can drop the cancel
    # stop() in its own task: a timeout around it would work by cancelling *this* task, and the old
    # stop() swallowed its caller's cancellation too, so that check passed while stop() hung.
    stopping = asyncio.create_task(hub.stop())
    done, _ = await asyncio.wait({stopping}, timeout=3)
    assert stopping in done, "stop() hung"
    assert client.swallowed == 1 and hub._task is not None and hub._task.done()


async def test_stop_doesnt_swallow_its_callers_cancellation():
    client = SwallowingClient(swallow_all=True)  # a task that won't exit, so stop() is still waiting
    hub = _hub(client)
    await hub.start()
    await asyncio.sleep(0.05)
    stopping = asyncio.create_task(hub.stop())
    await asyncio.sleep(0.1)
    stopping.cancel()  # e.g. the server being shut down harder
    with pytest.raises(asyncio.CancelledError):
        await stopping
    client.swallow_all = False
    await hub.stop()


async def test_stop_is_bounded_even_if_the_task_never_exits(monkeypatch, caplog):
    monkeypatch.setattr(MqttHub, "stop_timeout", 0.5)
    client = SwallowingClient(swallow_all=True)
    hub = _hub(client)
    await hub.start()
    await asyncio.sleep(0.05)
    loop = asyncio.get_running_loop()
    t = loop.time()
    await hub.stop()
    assert loop.time() - t < 1.5
    assert "didn't stop within" in caplog.text
    client.swallow_all = False  # let the test's loop close cleanly
    await hub.stop()


# --- stopped mid-connect -----------------------------------------------------------------------------
class SilentBroker:
    """Accepts TCP connections and never answers, so a client stays stuck waiting for its CONNACK.

    Records whether each connection was closed by the client: what stop() must guarantee.
    """

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
        return int(self._server.sockets[0].getsockname()[1])

    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.opened += 1
        try:
            while await reader.read(1024):  # the CONNECT arrives; we sit on it
                pass
        except ConnectionError:
            pass
        self.closed += 1
        writer.close()

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()


def _slow_connect_factory(seconds: float):  # noqa: ANN202
    """aiomqtt.Client whose blocking connect (run in an executor thread) starts late.

    Lets a test cancel while that thread is still running: the thread finishes anyway and opens the
    socket after the cancel. It overrides aiomqtt's private _client_connect, the one way to get there.
    """
    import time

    import aiomqtt

    class SlowConnect(aiomqtt.Client):
        def _client_connect(self) -> None:
            time.sleep(seconds)
            super()._client_connect()

    return SlowConnect


@pytest.mark.parametrize("where", ["waiting for CONNACK", "in the connect thread"])
async def test_stop_mid_connect_closes_the_half_made_connection(where):
    """aiomqtt doesn't undo a connection when __aenter__ is cancelled, and `async with` never calls
    __aexit__ when __aenter__ fails, so the socket stayed open (and on Python 3.12+ a broker's
    wait_closed() then waited for it forever). stop() must leave nothing connected."""
    broker = SilentBroker()
    port = await broker.start()
    factory = _slow_connect_factory(0.3) if where == "in the connect thread" else None
    hub = MqttHub(MqttSettings("127.0.0.1", port, False, None, None, None), "02AB34CD56EF", factory)
    await hub.start()
    await asyncio.sleep(0.1)  # mid-connect: the thread is sleeping, or CONNECT is sent and unanswered
    await hub.stop()
    try:
        await eventually(lambda: broker.opened == 1 and broker.closed == 1, timeout=3)
        await asyncio.sleep(0.5)  # and no later attempt reconnects
        assert broker.opened == 1
    finally:
        await broker.stop()


async def test_a_broker_can_shut_down_after_a_client_stopped_mid_connect(broker):
    """The symptom that found this: Broker.stop() waits for every connection on Python 3.12+, and hung
    forever on the half-made one (test_direct's dry-run test, about one run in thirty on 3.12)."""
    b = in_package(broker)
    for _ in range(5):  # the timing varies: stop at a few points of the connect
        hub = MqttHub(MqttSettings(broker[0], broker[1], False, None, None, None), "02AB34CD56EF")
        await hub.start()
        await asyncio.sleep(0)
        await hub.stop()
    stopping = asyncio.create_task(b.stop())
    done, _ = await asyncio.wait({stopping}, timeout=3)
    assert stopping in done, "Broker.stop() hung on a connection stop() left open"
