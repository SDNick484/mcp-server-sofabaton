"""Name resolution and confirmation, against the fake server."""

from __future__ import annotations

import pytest

from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.client import SofabatonClient, Target
from sofabaton_mcp.config import Settings

from .conftest import HUB, MUSIC, ONKYO, SHIELD, WATCH_SHIELD

pytestmark = pytest.mark.anyio


@pytest.fixture
async def make(fake):
    apis: list[ServerAPI] = []

    def build(hub: str | None = None) -> SofabatonClient:
        api = ServerAPI("http://sbx.test:8480", transport=fake.transport)
        apis.append(api)
        return SofabatonClient(Settings(url="http://sbx.test:8480", hub=hub), api)

    yield build
    for a in apis:
        await a.aclose()


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


async def test_hub_lookup_is_cached(make, fake):
    c = make()
    await c.hub_id()
    await c.hub_id()
    assert fake.requests.count(("GET", "/hubs")) == 1


# --- status ----------------------------------------------------------------------
async def test_status(make, fake):
    fake.running = WATCH_SHIELD
    st = await make().status()
    assert st["mode"] == "control" and st["controllable"] and st["model"] == "X2"
    assert st["running_activity"] == {"id": WATCH_SHIELD, "name": "Watch Shield"}


async def test_status_without_a_hub_session_skips_identity(make, fake):
    fake.hub_connected = False
    fake.mode = "disconnected"
    st = await make().status()
    assert (st["hub_connected"], st["hub_name"]) == (False, None)
    assert ("GET", f"/hubs/{HUB}/info") not in fake.requests


# --- activities ----------------------------------------------------------------
async def test_start_waits_for_the_macro(make, fake):
    fake.settle_reads = 3  # still "nothing running" for three looks
    c = make()
    assert await c.start_activity(await c.find_activity("watch shield")) is True
    assert fake.running == WATCH_SHIELD


async def test_start_of_the_running_activity_sends_nothing(make, fake):
    fake.running = WATCH_SHIELD
    c = make()
    assert await c.start_activity(await c.find_activity("Watch Shield")) is False
    assert fake.posts == []


async def test_accepted_but_never_running_is_an_error(make, fake):
    # "accepted" means the hub took the frame, not that the activity came up.
    fake.settle_reads = None
    c = make()
    with pytest.raises(SofabatonError, match="didn't report it finished"):
        await c.start_activity(await c.find_activity("Listen to Music"))


async def test_stop(make, fake):
    c = make()
    assert await c.stop() is None and fake.posts == []
    fake.running = MUSIC
    assert await c.stop() == "Listen to Music"
    assert fake.posts == [(f"/hubs/{HUB}/activities/{MUSIC}/stop", None)]


# --- targets and commands --------------------------------------------------------
async def test_target_defaults_to_the_running_activity(make, fake):
    c = make()
    with pytest.raises(SofabatonError, match="No activity is running"):
        await c.target(None)
    fake.running = WATCH_SHIELD
    assert await c.target(None) == Target("activity", WATCH_SHIELD, "Watch Shield")
    assert await c.target("onkyo receiver") == Target("device", ONKYO, "Onkyo Receiver")


async def test_activity_commands_are_macros_and_favorites(make):
    c = make()
    cmds = await c.commands(Target("activity", WATCH_SHIELD, "Watch Shield"))
    # The unlabeled macro is skipped; the favorite goes to its own device.
    assert cmds == [("Movie Mode", WATCH_SHIELD, 40), ("Netflix", SHIELD, 9)]


async def test_send_command_and_press(make, fake):
    c = make()
    act = Target("activity", WATCH_SHIELD, "Watch Shield")
    assert await c.send_command("netflix", act) == "Netflix"
    await c.press("VOL_UP", act, repeat=2)
    assert fake.sends() == [(SHIELD, 9), (WATCH_SHIELD, 182), (WATCH_SHIELD, 182)]


async def test_unknown_command_and_button_send_nothing(make, fake):
    c = make()
    dev = Target("device", ONKYO, "Onkyo Receiver")
    with pytest.raises(SofabatonError, match="has no command 'Erase'"):
        await c.send_command("Erase", dev)
    with pytest.raises(SofabatonError, match="not allowed"):
        await c.press("POWER_OFF", dev)
    assert fake.sends() == []
