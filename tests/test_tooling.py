"""The diagnostics you'll run at home: doctor (server and MQTT checks, the MAC-case probe, --listen,
--dump), check, and simulate end to end as real processes.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from sofabaton_mcp.api import ServerAPI
from sofabaton_mcp.config import MqttSettings, Settings
from sofabaton_mcp.doctor import listen, render, run_doctor, to_json
from sofabaton_mcp.sim.fake_server import FakeServer
from sofabaton_mcp.sim.fake_x2 import FakeX2
from sofabaton_mcp.sim.hub_state import load_state

from .conftest import HUB

pytestmark = pytest.mark.anyio


def server_settings(**kw) -> Settings:
    return Settings(url="http://sbx.test:8480", hub=None, **kw)


async def doctor(fake: FakeServer, settings: Settings | None = None, **kw):
    api = ServerAPI("http://sbx.test:8480", transport=fake.transport)
    try:
        return await run_doctor(settings or server_settings(), api=api, timeout=1.0, **kw)
    finally:
        await api.aclose()


def steps(report) -> dict[str, bool]:
    return {c.step: c.ok for c in report.checks}


# --- doctor: server ---------------------------------------------------------------------
async def test_doctor_all_good_over_rest(fake):
    fake.claimed = True
    report = await doctor(fake)
    assert steps(report) == {"server": True, "hub": True, "identity": True, "catalog": True}
    assert report.ok and report.warnings == []
    text = render(report)
    assert "OK   identity  X2 'Living Room X2', firmware 300, MAC xxxxxxxx56EF" in text
    assert "All checks passed." in text
    assert "S-MQTT-CONTROL" in text  # the unverified assumptions are listed every time


async def test_doctor_explains_observe_mode(fake):
    fake.mode = "observe"
    report = await doctor(fake)
    assert steps(report)["hub"] is False and not report.ok
    assert "close it on every phone and tablet" in render(report)


async def test_doctor_warns_when_the_server_is_unclaimed(fake):
    fake.claimed = False
    report = await doctor(fake)
    assert report.ok and any("unclaimed" in w for w in report.warnings)


async def test_doctor_without_a_hub_session(fake):
    fake.hub_connected = False
    fake.mode = "disconnected"
    report = await doctor(fake)
    assert steps(report) == {"server": True, "hub": False}


async def test_doctor_server_down(fake):
    fake.unreachable = True
    report = await doctor(fake)
    assert steps(report) == {"server": False} and "Is sofabaton-x-server running" in render(report)


async def test_doctor_on_an_x1_with_mqtt_configured_says_x2_only(broker):
    fake = FakeServer(load_state(model="X1"), hub_id=HUB)
    fake.claimed = True
    m = MqttSettings(broker[0], broker[1], False, None, None, None)
    report = await doctor(fake, server_settings(mqtt=m))
    assert any("this is an X1: MQTT features need an X2" in w for w in report.warnings)


async def test_doctor_json_and_dump_are_redacted(fake, tmp_path):
    report = await doctor(fake, dump_dir=tmp_path)
    doc = json.loads(to_json(report))
    assert doc["ok"] is True and doc["dumped_to"] == str(tmp_path / "sofabaton.json")
    dumped = (tmp_path / "sofabaton.json").read_text()
    assert "02:ab:34:cd" not in dumped.lower() and "xx:xx:xx:xx:56:ef" in dumped.lower()
    assert json.loads(dumped)["server"]["activities"][0]["name"] == "Watch Shield"


# --- doctor: MQTT ---------------------------------------------------------------------------
async def test_doctor_mqtt_learns_the_mac_from_the_server(broker, state, x2):
    fake = FakeServer(state, hub_id=HUB)
    fake.claimed = True
    m = MqttSettings(broker[0], broker[1], False, None, None, None)  # no MAC: the server knows it
    report = await doctor(fake, server_settings(mqtt=m))
    assert steps(report)["mqtt"] and steps(report)["x2"], render(report)
    assert "answered over MQTT: 2 activities, running: nothing" in render(report)


async def test_doctor_mqtt_only(broker, state, x2):
    m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)
    report = await run_doctor(Settings(url=None, hub=None, mqtt=m), timeout=1.0)
    assert steps(report) == {"mqtt": True, "x2": True}


async def test_doctor_probes_the_lowercase_mac(broker):
    """S-MQTT-MAC-CASE: if the X2 uses lowercase topics, doctor says so instead of 'no reply'."""
    state = load_state(mac="02ab34cd56ef")  # the fake X2 subscribes with the MAC exactly as given
    fake_x2 = FakeX2(state, broker[0], broker[1])
    await fake_x2.start()
    try:
        m = MqttSettings(broker[0], broker[1], False, None, None, "02AB34CD56EF")
        report = await run_doctor(Settings(url=None, hub=None, mqtt=m), timeout=0.5)
    finally:
        await fake_x2.stop()
    assert steps(report) == {"mqtt": True, "x2": False}
    assert "answered on the *lowercase*-MAC topics" in render(report)


async def test_doctor_x2_silent(broker, state):
    m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)  # no fake X2 running
    report = await run_doctor(Settings(url=None, hub=None, mqtt=m), timeout=0.3)
    assert steps(report) == {"mqtt": True, "x2": False}
    assert "doctor --listen 30" in render(report)


async def test_doctor_bad_broker_login(broker):
    if broker[2] is None:
        pytest.skip("needs the in-package broker (auth configured per test)")
    from sofabaton_mcp.sim.broker import Broker

    locked = Broker(users={"hass": "right"})
    port = await locked.start()
    try:
        m = MqttSettings("127.0.0.1", port, False, "hass", "wrong", "02AB34CD56EF")
        report = await run_doctor(Settings(url=None, hub=None, mqtt=m), timeout=1.0)
    finally:
        await locked.stop()
    assert steps(report) == {"mqtt": False}
    assert "login rejected" in render(report) and "wrong" not in render(report)


async def test_listen_prints_what_the_x2_publishes(broker, state, x2):
    m = MqttSettings(broker[0], broker[1], False, None, None, state.mac)
    task = asyncio.create_task(listen(Settings(url=None, hub=None, mqtt=m), 1.0))
    await asyncio.sleep(0.3)  # let it subscribe
    state.press_virtual_key(3, 1)
    state.set_running(101)
    heard = await task
    assert any(line.startswith(f"{state.mac}/up: ") for line in heard), heard
    assert any(f"activity/{state.mac}/activity_control_up" in line for line in heard), heard


# --- the CLI as real processes ------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def exe() -> str:
    path = shutil.which("mcp-server-sofabaton", path=os.path.dirname(sys.executable))
    if path is None:
        pytest.skip("entry point not installed (pip install -e .)")
    return path


@pytest.fixture
def simulator(tmp_path):
    procs: list[subprocess.Popen[str]] = []

    def start(*args: str) -> dict[str, str]:
        env_file = tmp_path / f"sim{len(procs)}.env"
        p = subprocess.Popen(
            [exe(), "simulate", "--no-prompt", "--port", str(free_port()), "--mqtt-port", str(free_port()),
             "--write-env", str(env_file), "--settle-reads", "1", *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        procs.append(p)
        for _ in range(200):
            if env_file.exists() and env_file.read_text().strip():
                break
            if p.poll() is not None:
                raise AssertionError(p.stdout.read() if p.stdout else "simulate exited")
            import time

            time.sleep(0.05)
        else:
            raise AssertionError("simulate never wrote its env file")
        pairs = (line.removeprefix("export ").split("=", 1) for line in env_file.read_text().splitlines())
        return {k: v for k, v in pairs}

    yield start
    for p in procs:
        p.terminate()
        p.wait(10)


def run(cmd: str, sim_env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOFABATON_")} | sim_env
    return subprocess.run([exe(), cmd, *args], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize(
    ("sim_args", "expect"),
    [
        ((), ["OK   server", "OK   mqtt", "OK   x2", "All checks passed."]),  # X2: server + MQTT
        (("--no-server",), ["OK   mqtt", "OK   x2", "All checks passed."]),  # X2: MQTT only
        (("--model", "X1S"), ["OK   identity  X1S", "All checks passed."]),  # X1S: server only
    ],
)
def test_simulate_then_doctor(simulator, sim_args, expect):
    sim_env = simulator(*sim_args)
    done = run("doctor", sim_env)
    assert done.returncode == 0, done.stdout + done.stderr
    for line in expect:
        assert line in done.stdout, done.stdout


def test_simulate_then_check(simulator):
    done = run("check", simulator())
    assert done.returncode == 0, done.stderr
    assert "Hub: Living Room X2 (X2)" in done.stdout and "Commands go via: server" in done.stdout
    assert "live_activity_state" in done.stdout and "  Watch Shield" in done.stdout


def test_x1_simulate_refuses_mqtt_only():
    done = subprocess.run(
        [exe(), "simulate", "--model", "X1", "--no-server", "--no-prompt"], capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 2 and "no MQTT on those models" in done.stderr


def test_doctor_cli_json_exit_code(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SOFABATON_")}
    env["SOFABATON_URL"] = f"http://127.0.0.1:{free_port()}"  # nothing listening
    done = subprocess.run(
        [exe(), "doctor", "--json", "--timeout", "1"], env=env, capture_output=True, text=True, timeout=30
    )
    assert done.returncode == 1
    doc = json.loads(done.stdout)
    assert doc["ok"] is False and doc["checks"][0]["step"] == "server"


def test_package_ships_the_fixture():
    # `simulate` from an installed wheel needs the fixture as package data.
    import sofabaton_mcp.sim

    assert (Path(sofabaton_mcp.sim.__file__).parent / "fixtures" / "x2_living_room.json").is_file()
