"""Shared fixtures. Tests run against a fake sofabaton-x-server: no hub, no network.

The fake plugs in below httpx (httpx.MockTransport), so the real ServerAPI
code builds real requests and parses real responses. Its payloads carry the
*full* field sets from the server's OpenAPI schemas, extra fields included, so
a model that only reads a subset is tested against what actually arrives.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from sofabaton_mcp.client import SofabatonClient

HUB = "a1b2c3"
WATCH_SHIELD = 101
MUSIC = 102
ONKYO = 1
SHIELD = 2


# Async tests use anyio's plugin (pytest.mark.anyio), not pytest-asyncio: the
# MCP SDK's in-process Client needs fixture setup and teardown in one task.
@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _activity(aid: int, name: str, active: bool) -> dict[str, Any]:
    return {"activity_id": aid, "name": name, "active": active, "needs_confirm": False, "sort": aid - 100}


def _device(did: int, name: str, brand: str, cls: str) -> dict[str, Any]:
    return {
        "device_id": did,
        "name": name,
        "brand": brand,
        "device_class": cls,
        "device_class_code": 3,
        "power_state": 0,
        "idle_behavior": 0,
        "sort": did,
    }


class FakeServer:
    """Answers the routes ServerAPI uses, like sofabaton-x-server 0.2.x does."""

    def __init__(self) -> None:
        self.hubs = [
            {
                "hub_id": HUB,
                "enabled": True,
                "config": {"host": "192.0.2.40", "port": 8102, "name": "Living Room", "proxy_enabled": True},
                "added_at": "2026-10-01T00:00:00Z",
                "last_seen": "2026-10-07T00:00:00Z",
                "status": None,
                "hub_name": "Living Room X2",
            }
        ]
        self.mode = "control"
        self.hub_connected = True
        self.running: int | None = None
        # How many /activity reads a start or stop takes to show up, like a
        # power-on macro that is still running. None = never (a stuck macro).
        self.settle_reads: int | None = 1
        self._pending: tuple[int | None, int] | None = None
        self.activity_names = {WATCH_SHIELD: "Watch Shield", MUSIC: "Listen to Music"}
        self.devices = [
            _device(ONKYO, "Onkyo Receiver", "Onkyo", "AV Receiver"),
            _device(SHIELD, "Shield", "NVIDIA", "Media Player"),
        ]
        self.commands = {
            ONKYO: [{"command_id": 7, "label": "Input HDMI 2"}],
            SHIELD: [{"command_id": 3, "label": "Home"}],
        }
        self.macros = {WATCH_SHIELD: [{"command_id": 40, "label": "Movie Mode"}, {"command_id": 41, "label": None}]}
        self.favorites = {WATCH_SHIELD: [{"device_id": SHIELD, "command_id": 9, "label": "Netflix"}]}
        self.presses: list[dict[str, Any]] = []
        self.posts: list[tuple[str, Any]] = []  # every POST received: (path, json body)
        self.requests: list[tuple[str, str]] = []
        self.unreachable = False

    # --- plumbing ------------------------------------------------------------
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def problem(self, status: int, title: str, detail: str | None = None, mode: str | None = None) -> httpx.Response:
        body = {"type": title, "title": title, "status": status, "detail": detail, "hub_id": HUB, "mode": mode}
        return httpx.Response(status, json=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path.removeprefix("/api/v1")
        self.requests.append((request.method, path))
        if request.method == "POST":
            body = json.loads(request.content) if request.content else None
            self.posts.append((path, body))
            return self.post(path, body)
        return self.get(path, dict(request.url.params))

    def _running_view(self) -> dict[str, Any] | None:
        if self._pending is not None:
            target, reads = self._pending
            if reads <= 0:
                self.running, self._pending = target, None
            else:
                self._pending = (target, reads - 1)
        if self.running is None:
            return None
        return {"activity_id": self.running, "name": self.activity_names[self.running]}

    def get(self, path: str, params: dict[str, str]) -> httpx.Response:
        if path == "/hubs":
            return httpx.Response(200, json=self.hubs)
        if not path.startswith(f"/hubs/{HUB}"):
            return self.problem(404, "hub_not_found")
        sub = path.removeprefix(f"/hubs/{HUB}")
        if sub == "/status":
            ra = {"activity_id": self.running, "name": self.activity_names[self.running]} if self.running else None
            status = {
                "hub_connected": self.hub_connected,
                "app_connected": self.mode == "observe",
                "controllable": self.mode == "control",
                "mode": self.mode,
                "hub_version": "X2",
                "proxy_enabled": True,
                "running_activity": ra,
                "activities_cached": 2,
                "devices_cached": 2,
                "catalog_ready": True,
                "firmware_version": 300,
                "firmware_min_supported": 200,
                "firmware_unsupported": False,
                "firmware_outdated": False,
            }
            return httpx.Response(200, json={"hub_id": HUB, "enabled": True, "status": status})
        if not self.hub_connected:
            # Seen from the real server (0.2.4) with the hub unreachable: catalog
            # and identity reads answer 503 with the mode in the problem body.
            return self.problem(503, "hub_not_connected", "cannot fetch: the hub is not connected yet", "disconnected")
        if sub == "/info":
            return httpx.Response(
                200,
                json={
                    "known": True,
                    "model": "X2",
                    "name": "Living Room X2",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "firmware_version": 300,
                    "production_batch": "B1",
                    "firmware_min_supported": 200,
                    "firmware_min_recommended": 250,
                    "firmware_unsupported": False,
                    "firmware_outdated": False,
                },
            )
        if sub == "/activities":
            return httpx.Response(
                200, json=[_activity(a, n, a == self.running) for a, n in self.activity_names.items()]
            )
        if sub == "/devices":
            return httpx.Response(200, json=self.devices)
        if sub == "/activity":
            return httpx.Response(200, json=self._running_view())
        if sub == "/presses":
            rows = [p for p in self.presses if "after" not in params or p["seq"] > int(params["after"])]
            return httpx.Response(
                200,
                json={"instance_id": "inst1", "last_seq": len(self.presses), "expired": False, "presses": rows},
            )
        if m := re.fullmatch(r"/devices/(\d+)/commands", sub):
            return httpx.Response(200, json=self.commands.get(int(m[1]), []))
        if m := re.fullmatch(r"/activities/(\d+)/macros", sub):
            return httpx.Response(200, json=self.macros.get(int(m[1]), []))
        if m := re.fullmatch(r"/activities/(\d+)/favorites", sub):
            return httpx.Response(200, json=self.favorites.get(int(m[1]), []))
        return self.problem(404, "not_found")

    def post(self, path: str, body: Any) -> httpx.Response:
        sub = path.removeprefix(f"/hubs/{HUB}")
        if self.mode != "control":
            # What the real server does when the proxy doesn't own the hub.
            return self.problem(409, "send_refused", "the proxy does not own the hub right now", self.mode)
        if m := re.fullmatch(r"/activities/(\d+)/(start|stop)", sub):
            target = int(m[1]) if m[2] == "start" else None
            if self.settle_reads is not None:
                self._pending = (target, self.settle_reads)
        elif sub not in ("/send", "/find-remote"):
            return self.problem(404, "not_found")
        return httpx.Response(200, json={"accepted": True, "mode": self.mode})

    def sends(self) -> list[tuple[int, int]]:
        return [(b["entity_id"], b["command_id"]) for p, b in self.posts if p.endswith("/send")]


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SofabatonClient, "activity_timeout", 0.3)
    monkeypatch.setattr(SofabatonClient, "poll_interval", 0.01)
    monkeypatch.setattr(SofabatonClient, "repeat_gap", 0.0)


@pytest.fixture
def fake() -> FakeServer:
    return FakeServer()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SOFABATON_URL", "http://sbx.test:8480/")
    monkeypatch.delenv("SOFABATON_HUB", raising=False)
