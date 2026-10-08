"""Settings, and the button allow-list.

This server doesn't talk to the hub itself. It talks to sofabaton-x-server, the
REST server that owns the hub connection (see README "Why go through
sofabaton-x-server"). So the settings are where that server is and, if it
manages more than one hub, which one to use.

There is deliberately no setting for an API token. sofabaton-x-server lets
anyone on the LAN read and *control* (send, start/stop activities, find the
remote) without one, and requires a token only for *writes*: editing,
deleting, erasing and restoring the hub. Never holding a token means a
confused or prompt-injected model can't reach those routes through us, no
matter what tool code someday tries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, get_args

DEFAULT_URL = "http://localhost:8480"

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
class Settings:
    url: str
    hub: str | None  # hub id or name; None = the only hub the server has


def load_settings() -> Settings:
    url = (os.environ.get("SOFABATON_URL") or DEFAULT_URL).rstrip("/")
    return Settings(url=url, hub=os.environ.get("SOFABATON_HUB") or None)
