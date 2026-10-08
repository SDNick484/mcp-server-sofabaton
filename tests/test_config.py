"""Settings, and the button allow-list checked against the library itself."""

from __future__ import annotations

import sofabaton

from sofabaton_mcp.config import ALLOWED_BUTTONS, BUTTON_CODES, load_settings


def test_button_codes_match_the_library():
    # BUTTON_CODES is a copy (so runtime needs only HTTP); this keeps it honest.
    for name, code in BUTTON_CODES.items():
        assert getattr(sofabaton.ButtonName, name) == code, name


def test_allow_list_and_codes_agree():
    assert set(BUTTON_CODES) == ALLOWED_BUTTONS


def test_power_buttons_are_not_pressable():
    # Starting/stopping activities goes through the confirming tools instead.
    assert {"POWER_ON", "POWER_OFF"}.isdisjoint(ALLOWED_BUTTONS)
    library = {k for k in dir(sofabaton.ButtonName) if k.isupper()}
    assert library - ALLOWED_BUTTONS == {"POWER_ON", "POWER_OFF"}


def test_settings(monkeypatch):
    monkeypatch.delenv("SOFABATON_URL", raising=False)
    monkeypatch.delenv("SOFABATON_HUB", raising=False)
    s = load_settings()
    assert (s.url, s.hub) == ("http://localhost:8480", None)
    monkeypatch.setenv("SOFABATON_URL", "http://pve-sbx:8480/")
    monkeypatch.setenv("SOFABATON_HUB", "Living Room")
    s = load_settings()
    assert (s.url, s.hub) == ("http://pve-sbx:8480", "Living Room")
