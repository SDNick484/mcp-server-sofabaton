"""SofabatonClient over sofabaton-x-server (any model): names, confirmation, capabilities."""

from __future__ import annotations

import pytest

from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.config import Settings
from sofabaton_mcp.sim.fake_server import FakeServer
from sofabaton_mcp.sim.hub_state import Executed, load_state

from .conftest import HUB, MUSIC, ONKYO, SHIELD, WATCH_SHIELD

pytestmark = pytest.mark.anyio


@pytest.fixture
async def make(fake):
    made: list[SofabatonClient] = []

    def build(hub: str | None = None, server: FakeServer | None = None, **settings) -> SofabatonClient:
        api = ServerAPI("http://sbx.test:8480", transport=(server or fake).transport)
        c = SofabatonClient(Settings(url="http://sbx.test:8480", hub=hub, **settings), api)
        made.append(c)
        return c

    yield build
    for c in made:
        await c.stop()


def sent(fake) -> list[tuple[str, str, int, int | None]]:
    return [(e.via, e.what, e.entity_id, e.command_id) for e in fake.state.executed]


# --- which hub -----------------------------------------------------------------
async def test_single_hub_needs_no_setting(make):
    assert await make().hub_id() == HUB


@pytest.mark.parametrize("wanted", [HUB, "Living Room", "living room x2"])
async def test_hub_by_id_or_name(make, wanted):
    assert await make(wanted).hub_id() == HUB


async def test_several_hubs_need_a_choice(make, fake):
    fake.hubs.append({**fake.hubs[0], "hub_id": "d4e5f6", "hub_name": "Den"})
    with pytest.raises(SofabatonError, match="set SOFABATON_HUB"):
        await make().hub_id()
    with pytest.raises(SofabatonError, match="No hub matches SOFABATON_HUB='Garage'"):
        await make("Garage").hub_id()


# --- status and capabilities, per model ------------------------------------------------
async def test_x2_without_mqtt_says_what_mqtt_would_add(make, fake):
    fake.running = WATCH_SHIELD
    st = await make().status()
    assert (st["model"], st["via"], st["running_activity"]) == ("X2", "server", "Watch Shield")
    assert {"activities", "buttons", "commands", "presses", "find_remote", "hub_info"} <= set(st["capabilities"])
    assert "live_activity_state" not in st["capabilities"]
    assert any("MQTT isn't set up" in lim for lim in st["limitations"])


@pytest.mark.parametrize("model", ["X1", "X1S"])
async def test_x1_family_is_fully_supported_over_rest_and_says_mqtt_is_x2_only(make, model):
    server = FakeServer(load_state(model=model), hub_id=HUB)
    c = make(server=server)
    st = await c.status()
    assert st["model"] == model and st["via"] == "server"
    assert {"activities", "buttons", "commands", "find_remote"} <= set(st["capabilities"])
    assert any(f"This is an {model}" in lim and "need an X2" in lim for lim in st["limitations"])
    # and it really works:
    outcome, name = await c.start_activity("Listen to Music")
    assert outcome.changed and name == "Listen to Music"


async def test_status_without_a_hub_session(make, fake):
    fake.hub_connected = False
    fake.mode = "disconnected"
    st = await make().status()
    assert st["via"] is None and st["running_activity"] is None and st["capabilities"] == []
    assert st["server"] is not None and st["server"]["hub_connected"] is False
    assert ("GET", f"/hubs/{HUB}/info") not in fake.requests


async def test_unreachable_server_is_a_limitation_not_a_crash(make, fake):
    fake.unreachable = True
    st = await make().status()
    assert st["server"] is not None and st["server"]["reachable"] is False
    assert any("Can't reach sofabaton-x-server" in lim for lim in st["limitations"])


# --- activities --------------------------------------------------------------------------
async def test_start_waits_for_the_macro(make, fake):
    """S-REST-START: confirmed by GET /activity, not by `accepted`."""
    fake.settle_reads = 3  # the server still reports the old state for three reads
    c = make()
    outcome, _ = await c.start_activity("watch shield")
    assert outcome.changed and outcome.via == "server" and fake.running == WATCH_SHIELD


async def test_start_of_the_running_activity_sends_nothing(make, fake):
    fake.running = WATCH_SHIELD
    outcome, _ = await make().start_activity("Watch Shield")
    assert not outcome.changed and fake.posts == []


async def test_accepted_but_never_running_is_an_error(make, fake):
    # "accepted" means the hub took the frame, not that the activity came up.
    fake.settle_reads = None
    with pytest.raises(SofabatonError, match="didn't report it finished"):
        await make().start_activity("Listen to Music")


async def test_power_off(make, fake):
    c = make()
    outcome, name = await c.power_off()
    assert (outcome.changed, name) == (False, None) and fake.posts == []
    fake.running = MUSIC
    outcome, name = await c.power_off()
    assert outcome.changed and name == "Listen to Music"
    assert fake.posts == [(f"/hubs/{HUB}/activities/{MUSIC}/stop", None)]


async def test_observe_mode_on_an_x2_without_mqtt_suggests_mqtt(make, fake):
    fake.mode = "observe"
    st = await make().status()
    # Reads still work; nothing that sends does.
    assert st["capabilities"] == ["catalog", "presses", "hub_info"]
    assert any("commands are refused until it's closed" in lim for lim in st["limitations"])
    with pytest.raises(
        SofabatonError, match="close the app.*setting up MQTT .SOFABATON_MQTT_URL. would keep it controllable"
    ):
        await make().start_activity("Watch Shield")


# --- targets and commands --------------------------------------------------------------------
async def test_commands_for_an_activity_are_macros_and_favorites(make):
    source, opts = await make().commands("Watch Shield")
    assert source == "activity Watch Shield"
    # The unlabeled macro is skipped; the favorite goes to its own device.
    assert [(o.label, o.kind, o.entity_id, o.key_id, o.via) for o in opts] == [
        ("Movie Mode", "macro", WATCH_SHIELD, 40, "activity"),
        ("Netflix", "favorite", SHIELD, 9, "Shield"),
    ]


async def test_send_command_and_press_over_rest(make, fake):
    fake.running = WATCH_SHIELD
    c = make()
    outcome, label, where = await c.send_command("netflix", None)
    assert (label, where, outcome.via) == ("Netflix", "activity Watch Shield", "server")
    await c.press("VOL_UP", None, repeat=2)
    await c.send_command("Input HDMI 2", "onkyo receiver")
    assert fake.sends() == [(SHIELD, 9), (WATCH_SHIELD, 182), (WATCH_SHIELD, 182), (ONKYO, 7)]


async def test_unknown_command_and_button_send_nothing(make, fake):
    c = make()
    with pytest.raises(SofabatonError, match="has no command 'Erase'"):
        await c.send_command("Erase", "Onkyo Receiver")
    with pytest.raises(SofabatonError, match="not allowed"):
        await c.press("POWER_OFF", "Onkyo Receiver")
    assert fake.sends() == []


async def test_find_remote_over_rest(make, fake):
    outcome = await make().find_remote()
    assert outcome.changed and fake.state.executed == [Executed("rest", "find_remote", 0)]
