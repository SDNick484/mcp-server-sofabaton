"""find_remote over the hub's own TCP protocol (direct.py), for an X2 with no sofabaton-x-server.

The hub dials *us*: we send CALL_ME over UDP and it connects back over TCP
(ASSUMPTION S-DIRECT-CALL-ME). The fake does the same on 127.0.0.1, writing its
frames out by hand, so these tests exercise the real sockets and the real
framing. The byte strings marked "recorded" are what went on the wire to the
owner's X2 on 2026-10-27, when the remote beeped.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
from mcp import Client

from sofabaton_mcp import server
from sofabaton_mcp.api import SofabatonError
from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.config import MqttSettings, Settings, load_settings
from sofabaton_mcp.direct import OP_FIND_REMOTE_X2, Deframer, DirectHub, call_me, frame
from sofabaton_mcp.doctor import run_doctor
from sofabaton_mcp.sim.fake_direct import FakeDirectHub
from sofabaton_mcp.sim.hub_state import Executed

from .conftest import direct_to, eventually

pytestmark = pytest.mark.anyio

FIND = bytes.fromhex("a55a0323000008 2d")  # recorded
BANNER_REQ = bytes.fromhex("a55a000100")  # recorded


# --- framing ---------------------------------------------------------------------------------
def test_frames_match_what_the_x2_was_sent():
    assert frame(OP_FIND_REMOTE_X2, b"\x00\x00\x08") == FIND
    assert frame(0x0001) == BANNER_REQ
    # recorded: CALL_ME telling the X2 to dial 192.168.1.174:8200
    assert call_me("192.168.1.174", 8200) == bytes.fromhex("a55a0cc3000000000000c0a801ae20080d")


def test_opcode_high_byte_must_be_the_payload_length():
    with pytest.raises(ValueError, match="implies 3 payload bytes"):
        frame(OP_FIND_REMOTE_X2, b"\x00")


def test_deframer_splits_resyncs_and_drops_bad_checksums():
    d = Deframer()
    ping = frame(0x0140, b"\x00")
    bad = bytearray(frame(0x0103, b"\x00"))
    bad[-1] ^= 0xFF
    stream = b"\x00\x01junk" + ping + bytes(bad) + frame(0x0103, b"\x07")
    out = []
    for i in range(0, len(stream), 3):  # arrives in small pieces
        out += d.feed(stream[i : i + 3])
    assert out == [(0x0140, b"\x00"), (0x0103, b"\x07")]


# --- sessions against the fake hub ----------------------------------------------------------------
async def test_find_remote_beeps_and_waits_for_no_reply(hub_tcp, state):
    session = await direct_to(hub_tcp).find_remote()
    assert session.model == "X2"
    assert state.executed == [Executed("direct", "find_remote", 0)]
    # Exactly the recorded sequence: banner first, then the find frame. Nothing else. ASSUMPTION S-DIRECT-FIND
    assert hub_tcp.received == [BANNER_REQ, FIND]
    assert all(state.mac.lower() not in step.lower() for step in session.sent)  # no MAC, no addresses
    await eventually(lambda: not hub_tcp.connected)  # we hung up (ASSUMPTION S-DIRECT-LINGER)


async def test_identify_reads_the_banner_and_makes_no_sound(hub_tcp, state):
    session = await direct_to(hub_tcp).identify()
    assert session.model == "X2" and session.connect_s < 1
    assert hub_tcp.received == [BANNER_REQ] and state.executed == []


async def test_a_busy_hub_never_connects_back(state):
    """ASSUMPTION S-DIRECT-EXCLUSIVE: with the app holding the session the hub ignores CALL_ME."""
    fake = FakeDirectHub(state, busy=True)
    await fake.start()
    try:
        with pytest.raises(SofabatonError, match="close the Sofabaton app"):
            await direct_to(fake).find_remote()
        assert len(fake.call_mes) >= 2  # CALL_ME is repeated while waiting (S-DIRECT-CALL-ME)
    finally:
        await fake.stop()


@pytest.mark.parametrize("model", ["X1", "X1S"])
async def test_only_an_x2_gets_the_find_frame(state, model):
    """ASSUMPTION S-DIRECT-BANNER: the banner names the model, and nothing is sent to anything but an X2."""
    fake = FakeDirectHub(state, model=model)
    await fake.start()
    try:
        with pytest.raises(SofabatonError, match=f"is an {model}"):
            await direct_to(fake).find_remote()
        assert fake.received == [BANNER_REQ] and state.executed == []
    finally:
        await fake.stop()


async def test_one_session_at_a_time(hub_tcp, state):
    direct = direct_to(hub_tcp)
    results = await asyncio.gather(direct.find_remote(), direct.find_remote(), return_exceptions=True)
    assert sum(isinstance(r, SofabatonError) and "already running" in str(r) for r in results) == 1
    assert state.executed == [Executed("direct", "find_remote", 0)]


async def test_a_taken_port_says_which_setting_fixes_it(hub_tcp):
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        with pytest.raises(SofabatonError, match="SOFABATON_DIRECT_PORT"):
            await DirectHub("127.0.0.1", port, hub_udp_port=hub_tcp.port).find_remote()


# --- the client: MQTT-only X2 with SOFABATON_HUB_IP ---------------------------------------------
@pytest.fixture
async def mqtt_only(broker, state, x2):
    made: list[SofabatonClient] = []

    async def build(*, hub_ip: str | None = "127.0.0.1", **settings) -> SofabatonClient:
        m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)
        c = SofabatonClient(Settings(url=None, hub=None, mqtt=m, hub_ip=hub_ip, **settings), None)
        await c.start()
        made.append(c)
        return c

    yield build
    for c in made:
        await c.stop()


async def test_mqtt_only_with_hub_ip_can_find_the_remote(mqtt_only, hub_tcp, state):
    c = await mqtt_only()
    c.direct = direct_to(hub_tcp)
    st = await c.status()
    assert "find_remote" in st["capabilities"]
    assert any("find_remote goes to the hub directly" in lim for lim in st["limitations"])
    outcome = await c.find_remote()
    assert (outcome.changed, outcome.via) == (True, "direct")
    assert "doesn't confirm" in outcome.note
    assert state.executed == [Executed("direct", "find_remote", 0)]


async def test_mqtt_only_without_hub_ip_says_what_would_enable_it(mqtt_only):
    c = await mqtt_only(hub_ip=None)
    st = await c.status()
    assert "find_remote" not in st["capabilities"]
    assert any("SOFABATON_HUB_IP enables find_remote" in lim for lim in st["limitations"])
    with pytest.raises(SofabatonError, match="Set SOFABATON_HUB_IP"):
        await c.find_remote()


async def test_dry_run_opens_no_session(mqtt_only, hub_tcp, state):
    c = await mqtt_only(dry_run=True)
    c.direct = direct_to(hub_tcp)
    outcome = await c.find_remote()
    assert outcome.dry_run and outcome.via == "direct" and "FIND_REMOTE_X2" in outcome.sent[-1]
    assert hub_tcp.call_mes == [] and state.executed == []


async def test_through_the_mcp_tool(env, monkeypatch, broker, state, x2, hub_tcp):
    monkeypatch.setenv("SOFABATON_URL", "none")
    monkeypatch.setenv("SOFABATON_MQTT_URL", f"mqtt://{broker[0]}:{broker[1]}")
    monkeypatch.setenv("SOFABATON_MQTT_MAC", state.mac)
    monkeypatch.setenv("SOFABATON_HUB_IP", "127.0.0.1")
    async with Client(server.mcp) as c:
        server.client().direct = direct_to(hub_tcp)
        result = await c.call_tool("find_remote", {})
        r = json.loads(result.content[0].text)
        assert (r["outcome"], r["via"]) == ("done", "direct")
        assert "beeping" in r["detail"]
    assert state.executed == [Executed("direct", "find_remote", 0)]


# --- settings ------------------------------------------------------------------------------------
def test_hub_ip_settings(env, monkeypatch):
    monkeypatch.setenv("SOFABATON_URL", "none")
    monkeypatch.setenv("SOFABATON_HUB_IP", " 192.168.0.25 ")
    s = load_settings()
    assert (s.hub_ip, s.direct_port) == ("192.168.0.25", 8200)
    monkeypatch.setenv("SOFABATON_DIRECT_PORT", "8299")
    assert load_settings().direct_port == 8299


@pytest.mark.parametrize(
    ("var", "value", "problem"),
    [
        ("SOFABATON_HUB_IP", "x2.local", "isn't an IPv4 address"),
        ("SOFABATON_DIRECT_PORT", "99999", "should be a TCP port"),
    ],
)
def test_bad_direct_settings_are_reported(env, monkeypatch, var, value, problem):
    monkeypatch.setenv("SOFABATON_URL", "none")
    monkeypatch.setenv("SOFABATON_HUB_IP", "192.168.0.25")
    monkeypatch.setenv(var, value)
    s = load_settings()
    assert any(problem in p for p in s.problems)
    assert s.direct_port == 8200


def test_hub_ip_is_ignored_with_a_server(env, monkeypatch):
    """The server holds the hub's only session (S-DIRECT-EXCLUSIVE): a direct session would only time out."""
    monkeypatch.setenv("SOFABATON_HUB_IP", "192.168.0.25")
    s = load_settings()
    assert s.hub_ip is None and any("SOFABATON_HUB_IP is ignored" in p for p in s.problems)


# --- doctor --------------------------------------------------------------------------------------
async def test_doctor_checks_the_direct_session_quietly(hub_tcp, state):
    settings = Settings(url=None, hub=None, hub_ip="127.0.0.1")
    report = await run_doctor(settings, direct=direct_to(hub_tcp))
    check = next(c for c in report.checks if c.step == "direct")
    assert check.ok and "X2 connected back" in check.detail
    assert state.executed == []  # no beep


async def test_doctor_says_why_the_direct_session_failed(state):
    fake = FakeDirectHub(state, busy=True)
    await fake.start()
    try:
        report = await run_doctor(Settings(url=None, hub=None, hub_ip="127.0.0.1"), direct=direct_to(fake))
    finally:
        await fake.stop()
    check = next(c for c in report.checks if c.step == "direct")
    assert not check.ok and "close the Sofabaton app" in check.hint
