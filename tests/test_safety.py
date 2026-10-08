"""Guards: dry-run, rate limits, per-call caps, config validation, and keeping secrets out of logs.

These are the things that protect the house from a model in a loop or a typo
in an env var, so each one is pinned by a test rather than trusted.
"""

from __future__ import annotations

import logging

import pytest

from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.client import SofabatonClient
from sofabaton_mcp.config import MqttSettings, Settings, load_settings
from sofabaton_mcp.limits import ACTIVITY_CAPACITY, PRESS_CAPACITY, TokenBucket
from sofabaton_mcp.logsafe import RedactingFormatter, redact

from .conftest import HUB, MUSIC, WATCH_SHIELD

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def make(fake):
    made: list[SofabatonClient] = []

    def build(**settings) -> SofabatonClient:
        c = SofabatonClient(
            Settings(url="http://sbx.test:8480", hub=None, **settings),
            ServerAPI("http://sbx.test:8480", transport=fake.transport),
        )
        made.append(c)
        return c

    yield build
    for c in made:
        await c.stop()


# --- dry-run --------------------------------------------------------------------------------
async def test_dry_run_reads_but_never_posts(make, fake):
    fake.running = WATCH_SHIELD
    c = make(dry_run=True)
    assert (await c.status())["running_activity"] == "Watch Shield"  # reads still work
    outcomes = [
        (await c.start_activity("Listen to Music"))[0],
        (await c.power_off())[0],
        (await c.press("VOL_UP", None, repeat=3))[0],
        (await c.send_command("Netflix", None))[0],
        await c.find_remote(),
    ]
    assert all(o.dry_run and o.via == "server" for o in outcomes)
    assert fake.posts == [] and fake.state.executed == []
    # ...and each says what it would have sent, so the model (and you) can check it.
    assert outcomes[0].sent == [f"POST /api/v1/hubs/{HUB}/activities/{MUSIC}/start"]
    assert outcomes[2].sent == [f'POST /api/v1/hubs/{HUB}/send {{"entity_id": {WATCH_SHIELD}, "command_id": 182}}'] * 3


async def test_dry_run_from_the_environment(env, monkeypatch):
    monkeypatch.setenv("SOFABATON_DRY_RUN", "yes")
    assert load_settings().dry_run is True


# --- rate limits ----------------------------------------------------------------------------
async def test_press_rate_limit_refuses_whole_calls(make, fake):
    fake.running = WATCH_SHIELD
    c = make()
    clock = Clock()
    c.press_bucket = TokenBucket(PRESS_CAPACITY, 4.0, clock)
    await c.press("VOL_UP", None, repeat=10)
    await c.press("VOL_UP", None, repeat=10)
    with pytest.raises(SofabatonError, match=r"Rate limit: refusing to press VOL_UP x5 \(try again in 1s\)"):
        await c.press("VOL_UP", None, repeat=5)
    assert len(fake.sends()) == 20  # nothing of the refused call went out: no half "volume up x5"
    clock.now += 1.25
    await c.press("VOL_UP", None, repeat=5)
    assert len(fake.sends()) == 25


async def test_activity_rate_limit(make, fake):
    c = make()
    clock = Clock()
    c.activity_bucket = TokenBucket(ACTIVITY_CAPACITY, 1 / 15, clock)
    for name in ("Watch Shield", "Listen to Music", "Watch Shield", "Listen to Music"):
        await c.start_activity(name)
    posts = len(fake.posts)
    with pytest.raises(SofabatonError, match="Rate limit: refusing to start Watch Shield"):
        await c.start_activity("Watch Shield")
    assert len(fake.posts) == posts
    clock.now += 15
    await c.start_activity("Watch Shield")


async def test_unchanged_starts_cost_nothing(make, fake):
    # Starting what's already running sends nothing, so it shouldn't spend the budget either.
    fake.running = WATCH_SHIELD
    c = make()
    c.activity_bucket = TokenBucket(1, 1 / 15, Clock())
    for _ in range(3):
        assert not (await c.start_activity("Watch Shield"))[0].changed
    await c.start_activity("Listen to Music")


# --- per-call caps (the client re-checks what the schema already limits) ---------------------
@pytest.mark.parametrize(
    ("repeat", "delay_ms", "match"),
    [
        (11, None, "repeat must be 1-10"),
        (0, None, "repeat"),
        (2, 50, "delay_ms must be 100-2000"),
        (2, 5000, "delay_ms"),
    ],
)
async def test_caps(make, fake, repeat, delay_ms, match):
    fake.running = WATCH_SHIELD
    with pytest.raises(SofabatonError, match=match):
        await make().press("VOL_UP", None, repeat=repeat, delay_ms=delay_ms)
    assert fake.posts == []


# --- config validation ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("var", "value", "problem"),
    [
        ("SOFABATON_URL", "ftp://hub", "should start with http:// or https://"),
        ("SOFABATON_MQTT_URL", "http://broker", "SOFABATON_MQTT_URL must look like mqtt://"),
        ("SOFABATON_MQTT_URL", "mqtt://broker:99999", "invalid port"),
        ("SOFABATON_MQTT_MAC", "not-a-mac", "isn't a MAC address"),
        ("SOFABATON_MQTT_SETTLE_S", "soon", "should be seconds, 0-120"),
        ("SOFABATON_MQTT_SETTLE_S", "500", "should be seconds, 0-120"),
        ("SOFABATON_MQTT_PASSWORD_FILE", "/nonexistent/pw", "can't read SOFABATON_MQTT_PASSWORD_FILE"),
    ],
)
def test_config_problems_are_named(env, monkeypatch, var, value, problem):
    if var != "SOFABATON_MQTT_URL":
        monkeypatch.setenv("SOFABATON_MQTT_URL", "mqtt://broker")
    monkeypatch.setenv(var, value)
    problems = load_settings().problems
    assert any(problem in p for p in problems), problems


def test_mqtt_only_needs_a_mac(env, monkeypatch):
    monkeypatch.setenv("SOFABATON_URL", "none")
    monkeypatch.setenv("SOFABATON_MQTT_URL", "mqtt://broker")
    s = load_settings()
    assert s.url is None and any("needs the X2's MAC" in p for p in s.problems)


@pytest.mark.parametrize(
    ("url", "host", "port", "tls", "user", "password"),
    [
        ("mqtt://broker", "broker", 1883, False, None, None),
        ("mqtts://u:p%40ss@broker", "broker", 8883, True, "u", "p@ss"),
        ("broker:1884", "broker", 1884, False, None, None),
    ],
)
def test_mqtt_url_forms(env, monkeypatch, url, host, port, tls, user, password):
    monkeypatch.setenv("SOFABATON_MQTT_URL", url)
    m = load_settings().mqtt
    assert m is not None and (m.host, m.port, m.tls, m.username, m.password) == (host, port, tls, user, password)


def test_password_file_wins_and_never_shows_in_repr(env, monkeypatch, tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("s3cret\n")
    monkeypatch.setenv("SOFABATON_MQTT_URL", "mqtt://hass@broker")
    monkeypatch.setenv("SOFABATON_MQTT_PASSWORD", "env-secret")
    monkeypatch.setenv("SOFABATON_MQTT_PASSWORD_FILE", str(pw))
    s = load_settings()
    assert s.mqtt is not None and s.mqtt.password == "s3cret"
    assert "s3cret" not in repr(s) and "s3cret" not in repr(s.mqtt)


def test_missing_aiomqtt_is_a_problem_not_a_crash(env, monkeypatch):
    import builtins

    real = builtins.__import__

    def no_aiomqtt(name, *a, **kw):
        if name == "aiomqtt":
            raise ImportError(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_aiomqtt)
    monkeypatch.delenv("SOFABATON_URL")
    monkeypatch.setenv("SOFABATON_MQTT_URL", "mqtt://broker")
    s = load_settings()
    assert s.mqtt is None and any("aiomqtt isn't installed" in p for p in s.problems)
    assert s.url == "http://localhost:8480"  # falls back to the server, as before MQTT existed


# --- logs ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("hub at 192.168.1.40", "hub at x.x.x.40"),
        ("loopback 127.0.0.1 stays", "loopback 127.0.0.1 stays"),
        ("mac 02:ab:34:cd:56:ef", "mac xx:xx:xx:xx:56:ef"),
        ("topic activity/02AB34CD56EF/list", "topic activity/xxxxxxxx56EF/list"),
        ("mqtt://hass:hunter2@broker:1883", "mqtt://hass:***@broker:1883"),
        ("version 0.2.4 and id 101", "version 0.2.4 and id 101"),
    ],
)
def test_redact(raw, safe):
    assert redact(raw) == safe


def test_log_records_are_redacted():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "connect %s as %s", ("10.0.0.7", "02AB34CD56EF"), None)
    assert RedactingFormatter("%(message)s").format(record) == "connect x.x.x.7 as xxxxxxxx56EF"


def test_mqtt_settings_repr_hides_the_password():
    m = MqttSettings("broker", 1883, False, "u", "hunter2", "02AB34CD56EF")
    assert "hunter2" not in repr(m)
