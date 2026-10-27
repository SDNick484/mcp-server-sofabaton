"""serve --http's /healthz: wired to the hub's real reachability, not just "the process is up".

remote.py (shared) answers /healthz before authentication: 200 with whatever the check returns, 503 if it
raises. These tests put SofabatonClient.health_check behind remote.build_app and ask over ASGI, so they
cover the whole chain except uvicorn.
"""

from __future__ import annotations

import httpx
import pytest

from sofabaton_mcp import cli, remote, server
from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.config import MqttSettings, Settings
from sofabaton_mcp.mqtt import MqttHub
from sofabaton_mcp.server import mcp
from sofabaton_mcp.sim.fake_server import FakeServer

from .conftest import eventually, in_package

pytestmark = pytest.mark.anyio


async def healthz(check) -> httpx.Response:
    app = remote.build_app(mcp, remote.HttpConfig(), health_check=check)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as http:
        return await http.get(remote.HEALTH_PATH)


@pytest.fixture
async def rest_client(fake: FakeServer):
    c = SofabatonClient(
        Settings(url="http://sbx.test:8480", hub=None), ServerAPI("http://sbx.test:8480", transport=fake.transport)
    )
    yield c
    await c.stop()


async def test_healthz_is_200_when_the_server_has_the_hub(rest_client, fake):
    resp = await healthz(rest_client.health_check)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "hub": rest_client.hub_name, "via": "server"}


@pytest.mark.parametrize("break_it", ["unreachable", "hub_connected"])
async def test_healthz_is_503_when_the_hub_cant_be_reached(rest_client, fake, break_it):
    if break_it == "unreachable":
        fake.unreachable = True  # sofabaton-x-server itself is down
    else:
        fake.hub_connected = False  # the server is up but has no session with the hub
    resp = await healthz(rest_client.health_check)
    assert (resp.status_code, resp.json()) == (503, {"status": "degraded", "error": "Hub unreachable"})


async def test_healthz_stays_200_in_observe_mode(rest_client, fake):
    """Observe mode (the app holds the proxy) refuses commands but the hub is connected: healthy, via the server."""
    fake.mode = "observe"
    assert (await healthz(rest_client.health_check)).status_code == 200


async def test_healthz_over_mqtt_follows_the_broker_connection(broker, x2, state, monkeypatch):
    b = in_package(broker)
    monkeypatch.setattr(MqttHub, "retry_delay", 30.0)  # stays down after the kick
    m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)
    c = SofabatonClient(Settings(url=None, hub=None, mqtt=m), None)
    await c.start()
    try:
        await eventually(lambda: c.mqtt is not None and c.mqtt.connected)
        resp = await healthz(c.health_check)
        assert resp.status_code == 200 and resp.json() == {"status": "ok", "hub": f"X2 {state.mac[-4:]}", "via": "mqtt"}
        await b.kick()
        await eventually(lambda: c.mqtt is not None and not c.mqtt.connected, timeout=2)
        assert (await healthz(c.health_check)).status_code == 503
    finally:
        await c.stop()


async def test_health_check_doesnt_use_up_the_first_calls_mqtt_grace(broker, state):
    """A monitor polling /healthz at startup mustn't take the wait the first tool call is owed."""
    m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)
    c = SofabatonClient(Settings(url=None, hub=None, mqtt=m), None)
    await c.start()
    try:
        with pytest.raises(SofabatonError):
            await c.health_check()
        assert c._mqtt_grace_used is False
    finally:
        await c.stop()


async def test_the_failure_says_nothing_an_unauthenticated_caller_shouldnt_see(rest_client, fake):
    fake.unreachable = True
    body = (await healthz(rest_client.health_check)).text
    assert "sbx.test" not in body and "8480" not in body


async def test_server_health_check_before_the_lifespan_is_503(monkeypatch):
    monkeypatch.setattr(server, "_client", None)
    resp = await healthz(server.health_check)
    assert (resp.status_code, resp.json()) == (503, {"status": "degraded", "error": "Server not started"})


async def test_server_health_check_delegates_to_the_running_client(rest_client, monkeypatch):
    monkeypatch.setattr(server, "_client", rest_client)
    assert (await server.health_check())["via"] == "server"


def test_serve_http_passes_the_health_check(monkeypatch):
    seen: dict[str, object] = {}

    def fake_serve_http(app, cfg, *, health_check=None):
        seen["app"], seen["health_check"] = app, health_check

    monkeypatch.setattr(remote, "serve_http", fake_serve_http)
    monkeypatch.setattr(cli, "_setup_logging", lambda *a, **k: None)
    cli.main(["serve", "--http"])
    assert seen["app"] is mcp and seen["health_check"] is server.health_check
