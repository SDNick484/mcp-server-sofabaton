"""Settings, and the button allow-list.

Two ways to reach a hub, and you can configure either or both:

  sofabaton-x-server (SOFABATON_URL)  any model: X1, X1S, X2. The REST server
      owns the hub connection through the community sofabaton-x library.
  MQTT (SOFABATON_MQTT_URL)           X2 only. The hub's own MQTT support
      (Sofabaton app: Me -> Connect to Home Assistant) talking to your broker.

Both on an X2 is the fullest setup: REST for everything, MQTT for live
activity state, presses, and control while the Sofabaton app holds the REST
proxy. MQTT alone works with no server at all. With neither set, the default
is a server on localhost:8480 (the original behavior).

  Direct (SOFABATON_HUB_IP)           X2, MQTT-only setups. The one thing MQTT
      can't do, finding the remote, done over the hub's own TCP protocol
      (direct.py). Ignored when a server is configured: the server holds the
      hub's only session, and has its own find-remote route.

There is deliberately no setting for a sofabaton-x-server API token. The server
lets anyone on the LAN read and *control* (send, start/stop, find the remote)
without one, and requires a token only for *writes*: editing, deleting,
erasing and restoring the hub. Never holding a token means a confused or
prompt-injected model can't reach those routes through us.

Loading never fails: problems become sentences in ``Settings.problems``
(logged at startup, shown by `doctor`) and the broken part is left out.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, get_args
from urllib.parse import unquote, urlparse

from .protocol import normalize_mac

DEFAULT_URL = "http://localhost:8480"
DEFAULT_DIRECT_PORT = 8200  # where the hub connects back; the same port sofabaton-x uses

# --- Allow-list ---------------------------------------------------------------
# Hard buttons on the remote, named as the library's ButtonName (tests check
# each against it). press_button sends the button's code to an activity, which
# routes it to whatever device the activity binds that button to.
# Deliberately absent: POWER_ON and POWER_OFF. They start and stop activities,
# which start_activity/stop_activity do with confirmation; leaving them out
# keeps one way to change what's on.
ButtonName = Literal[
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "OK",
    "BACK",
    "HOME",
    "MENU",
    "EXIT",
    "GUIDE",
    "DVR",
    "VOL_UP",
    "VOL_DOWN",
    "MUTE",
    "CH_UP",
    "CH_DOWN",
    "PLAY",
    "PAUSE",
    "REW",
    "FWD",
    "RED",
    "GREEN",
    "YELLOW",
    "BLUE",
    "A",
    "B",
    "C",
    "NUM_0",
    "NUM_1",
    "NUM_2",
    "NUM_3",
    "NUM_4",
    "NUM_5",
    "NUM_6",
    "NUM_7",
    "NUM_8",
    "NUM_9",
    "NUM_DASH",
    "NUM_ENTER",
]
ALLOWED_BUTTONS: frozenset[str] = frozenset(get_args(ButtonName))

# The hub's button codes (the command_id to send). Copied from the library's
# ButtonName so this package needs only HTTP at runtime; test_config.py checks
# every entry against sofabaton-x itself.
BUTTON_CODES: dict[str, int] = {
    "UP": 174,
    "DOWN": 178,
    "LEFT": 175,
    "RIGHT": 177,
    "OK": 176,
    "BACK": 179,
    "HOME": 180,
    "MENU": 181,
    "EXIT": 154,
    "GUIDE": 157,
    "DVR": 155,
    "VOL_UP": 182,
    "VOL_DOWN": 185,
    "MUTE": 184,
    "CH_UP": 183,
    "CH_DOWN": 186,
    "PLAY": 156,
    "PAUSE": 188,
    "REW": 187,
    "FWD": 189,
    "RED": 190,
    "GREEN": 191,
    "YELLOW": 192,
    "BLUE": 193,
    "A": 153,
    "B": 152,
    "C": 151,
    "NUM_0": 159,
    "NUM_1": 169,
    "NUM_2": 168,
    "NUM_3": 167,
    "NUM_4": 166,
    "NUM_5": 165,
    "NUM_6": 164,
    "NUM_7": 163,
    "NUM_8": 162,
    "NUM_9": 161,
    "NUM_DASH": 160,
    "NUM_ENTER": 158,
}


@dataclass(frozen=True)
class MqttSettings:
    host: str
    port: int
    tls: bool
    username: str | None
    password: str | None = field(repr=False)  # never printed
    mac: str | None  # the X2's MAC, uppercase bare hex; None: learn it from sofabaton-x-server


@dataclass(frozen=True)
class Settings:
    url: str | None  # sofabaton-x-server; None: MQTT only
    hub: str | None  # hub id or name on that server; None = its only hub
    mqtt: MqttSettings | None = None
    dry_run: bool = False
    # In MQTT-only mode the hub announces an activity change early in its power
    # macro and never says when the macro is done, so presses are held off this
    # long after a change. ASSUMPTION S-MQTT-SETTLE
    settle_s: float = 6.0
    # The hub's IPv4 address, for find_remote without a server (direct.py); None: not set up.
    hub_ip: str | None = None
    direct_port: int = DEFAULT_DIRECT_PORT  # TCP port we listen on for the hub's connect-back
    problems: tuple[str, ...] = ()


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _mqtt(env: dict[str, str] | os._Environ[str], problems: list[str]) -> MqttSettings | None:
    raw = env.get("SOFABATON_MQTT_URL")
    if not raw:
        return None
    u = urlparse(raw if "://" in raw else f"mqtt://{raw}")
    if u.scheme not in ("mqtt", "mqtts") or not u.hostname:
        problems.append(
            f"SOFABATON_MQTT_URL must look like mqtt://[user:pass@]host[:1883] or mqtts://...; got scheme {u.scheme!r}"
        )
        return None
    try:
        port = u.port or (8883 if u.scheme == "mqtts" else 1883)
    except ValueError:
        problems.append("SOFABATON_MQTT_URL has an invalid port")
        return None
    password = unquote(u.password) if u.password else env.get("SOFABATON_MQTT_PASSWORD")
    pw_file = env.get("SOFABATON_MQTT_PASSWORD_FILE")
    if pw_file:
        try:
            password = Path(pw_file).read_text().strip()
        except OSError as exc:
            problems.append(f"can't read SOFABATON_MQTT_PASSWORD_FILE: {exc.strerror}")
    username = unquote(u.username) if u.username else env.get("SOFABATON_MQTT_USERNAME")
    mac = None
    if raw_mac := env.get("SOFABATON_MQTT_MAC"):
        mac = normalize_mac(raw_mac)
        if mac is None:
            problems.append(f"SOFABATON_MQTT_MAC {raw_mac!r} isn't a MAC address (12 hex digits)")
    try:
        import aiomqtt  # noqa: F401
    except ImportError:
        problems.append("MQTT is configured but aiomqtt isn't installed (it's a dependency): pip install aiomqtt")
        return None
    return MqttSettings(u.hostname, port, u.scheme == "mqtts", username, password, mac)


def _direct(env: dict[str, str] | os._Environ[str], url: str | None, problems: list[str]) -> tuple[str | None, int]:
    port = DEFAULT_DIRECT_PORT
    if raw_port := env.get("SOFABATON_DIRECT_PORT"):
        try:
            port = int(raw_port)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            problems.append(
                f"SOFABATON_DIRECT_PORT {raw_port!r} should be a TCP port, 1-65535; using {DEFAULT_DIRECT_PORT}"
            )
            port = DEFAULT_DIRECT_PORT
    raw = (env.get("SOFABATON_HUB_IP") or "").strip()
    if not raw:
        return None, port
    try:
        ip = ipaddress.IPv4Address(raw)
    except ValueError:
        problems.append(f"SOFABATON_HUB_IP {raw!r} isn't an IPv4 address; finding the remote directly is off")
        return None, port
    if url is not None:
        problems.append(
            "SOFABATON_HUB_IP is ignored while sofabaton-x-server is configured: the server holds the hub's session "
            "and finds the remote itself. Unset it, or set SOFABATON_URL=none for MQTT only."
        )
        return None, port
    return str(ip), port


def load_settings() -> Settings:
    env = os.environ
    problems: list[str] = []
    mqtt = _mqtt(env, problems)
    url_env = env.get("SOFABATON_URL")
    if url_env and url_env.strip().lower() == "none":
        url: str | None = None
    elif url_env:
        url = url_env.rstrip("/")
    else:
        # The original default, unless MQTT is set up on its own.
        url = None if mqtt is not None else DEFAULT_URL
    if url is not None and urlparse(url).scheme not in ("http", "https"):
        problems.append(f"SOFABATON_URL {url!r} should start with http:// or https://; ignoring it")
        url = None
    if mqtt is not None and url is None and mqtt.mac is None:
        problems.append(
            "MQTT without sofabaton-x-server needs the X2's MAC: set SOFABATON_MQTT_MAC (your router's client list "
            "shows it; with a server configured, `doctor` prints it)"
        )
    settle_raw = env.get("SOFABATON_MQTT_SETTLE_S")
    settle = 6.0
    if settle_raw:
        try:
            settle = float(settle_raw)
            if not 0 <= settle <= 120:
                raise ValueError
        except ValueError:
            problems.append(f"SOFABATON_MQTT_SETTLE_S {settle_raw!r} should be seconds, 0-120; using 6")
            settle = 6.0
    hub_ip, direct_port = _direct(env, url, problems)
    return Settings(
        url=url,
        hub=env.get("SOFABATON_HUB") or None,
        mqtt=mqtt,
        dry_run=_flag(env.get("SOFABATON_DRY_RUN")),
        settle_s=settle,
        hub_ip=hub_ip,
        direct_port=direct_port,
        problems=tuple(problems),
    )
