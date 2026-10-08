"""Shared fixtures. Tests run against fakes: no hub, no server, no broker installed.

The fakes live in the package (sofabaton_mcp/sim/) because `simulate` runs
the same ones:
  FakeServer  a fake sofabaton-x-server (Starlette app; in tests reached
              through httpx's ASGI transport, so the real ServerAPI code
              builds real requests and parses real responses)
  Broker      a small MQTT 3.1.1 broker
  FakeX2      the X2's MQTT side, an MQTT client answering like the hub
They share one FakeHubState, so REST and MQTT see the same hub.

Set MQTT_TEST_BROKER=mqtt://host:port to run the MQTT tests against a real
broker (mosquitto in CI) instead of the in-package one.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Callable
from urllib.parse import urlparse

import pytest

from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.mqtt import MqttHub
from sofabaton_mcp.sim.broker import Broker
from sofabaton_mcp.sim.fake_server import FakeServer
from sofabaton_mcp.sim.fake_x2 import FakeX2
from sofabaton_mcp.sim.hub_state import FakeHubState, load_state

HUB = "a1b2c3"
WATCH_SHIELD = 101
MUSIC = 102
ONKYO = 1
SHIELD = 2
ASK_CLAUDE = 3
MAC = "02AB34CD56EF"


# Async tests use anyio's plugin (pytest.mark.anyio), not pytest-asyncio: the
# MCP SDK's in-process Client needs fixture setup and teardown in one task.
@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SofabatonClient, "activity_timeout", 0.5)
    monkeypatch.setattr(SofabatonClient, "poll_interval", 0.01)
    monkeypatch.setattr(SofabatonClient, "repeat_gap", 0.0)
    monkeypatch.setattr(MqttHub, "publish_gap", 0.0)
    monkeypatch.setattr(MqttHub, "reply_timeout", 1.0)
    monkeypatch.setattr(MqttHub, "retry_delay", 0.05)


@pytest.fixture
def fake() -> FakeServer:
    return FakeServer(load_state(), hub_id=HUB)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SOFABATON_URL", "http://sbx.test:8480/")
    for var in (
        "SOFABATON_HUB",
        "SOFABATON_MQTT_URL",
        "SOFABATON_MQTT_MAC",
        "SOFABATON_DRY_RUN",
        "SOFABATON_MQTT_SETTLE_S",
    ):
        monkeypatch.delenv(var, raising=False)


def external_broker() -> tuple[str, int] | None:
    """MQTT_TEST_BROKER=mqtt://host:port, or None for the in-package broker."""
    url = os.environ.get("MQTT_TEST_BROKER")
    if not url:
        return None
    u = urlparse(url)
    return u.hostname or "127.0.0.1", u.port or 1883


async def eventually(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.02)


@pytest.fixture
async def broker():
    """(host, port, Broker or None). None when testing against an external broker."""
    ext = external_broker()
    if ext is not None:
        yield (*ext, None)
        return
    b = Broker()
    port = await b.start()
    yield "127.0.0.1", port, b
    await b.stop()


def in_package(broker) -> Broker:
    if broker[2] is None:
        pytest.skip("needs the in-package broker's test hooks")
    return broker[2]


@pytest.fixture
def state() -> FakeHubState:
    # A fresh MAC per test, and MQTT ids that differ from REST ids (ASSUMPTION
    # S-MQTT-IDS): anything that carries an id across transports breaks.
    return load_state(mac=secrets.token_hex(6).upper(), mqtt_id_offset=1000)


@pytest.fixture
async def x2(broker, state):
    fake = FakeX2(state, broker[0], broker[1])
    await fake.start()
    yield fake
    await fake.stop()
