"""Contract tests against sofabaton-x-server's own OpenAPI document (0.2.4, recorded from a running server).

The fake server is hand-written, so tests passing against it prove only that
our client and our fake agree. These tests tie both to the real server's
published contract:

  1. every route ServerAPI calls exists there, with that method;
  2. every POST we're allowed to make is one of the free control routes (no
     token), never an edit, and the allow-list has nothing extra;
  3. every field our TypedDicts read is in the server's schema, and if the
     server can send null for it, our type says so;
  4. every payload the fake serves validates against the server's response
     schema (so the fake can't drift into shapes the real server never sends),
     and so do its problem bodies.

jsonschema comes with the `mcp` SDK; no new dependency. OpenAPI 3.1 schemas
are JSON Schema 2020-12, so they validate as they are.

To re-record after a server upgrade:
  curl -s http://<server>:8480/api/v1/openapi.json > tests/fixtures/sofabaton-x-server-<ver>.openapi.json
"""

from __future__ import annotations

import json
import re
import types
import typing
from pathlib import Path
from typing import Any, NotRequired, get_args, get_type_hints

import httpx
import jsonschema
import pytest

from sofabaton_mcp import api as api_mod
from sofabaton_mcp.api import ServerAPI, SofabatonError
from sofabaton_mcp.sim.fake_server import FakeServer

from .conftest import HUB, SHIELD, WATCH_SHIELD

pytestmark = pytest.mark.anyio

SPEC_FILE = Path(__file__).parent / "fixtures" / "sofabaton-x-server-0.2.4.openapi.json"
SPEC: dict[str, Any] = json.loads(SPEC_FILE.read_text())
SCHEMAS = SPEC["components"]["schemas"]
PREFIX = "/api/v1"

# Our TypedDict -> the server's component schema.
TYPEDDICTS = {
    "RunningActivity": "RunningActivity",
    "HubStatus": "HubStatus",
    "HubStatusView": "HubStatusView",
    "HubConfig": "HubConfig",
    "HubView": "HubView",
    "HubInfo": "HubInfo",
    "Activity": "Activity",
    "Device": "Device",
    "Command": "Command",
    "Macro": "Macro",
    "Favorite": "Favorite",
    "Accepted": "Accepted",
    "Press": "PressView",
    "PressPage": "PressPage",
}

# The control routes the server lets anyone call (its description: "Reads and control calls are free").
FREE_CONTROL = {
    "/api/v1/hubs/{hub_id}/activities/{activity_id}/start",
    "/api/v1/hubs/{hub_id}/activities/{activity_id}/stop",
    "/api/v1/hubs/{hub_id}/send",
    "/api/v1/hubs/{hub_id}/find-remote",
}


def template_for(method: str, path: str) -> str:
    """The spec path template that a concrete request path matches, or fail."""
    full = PREFIX + path
    if (method, full) == ("GET", PREFIX + "/openapi.json"):
        return full  # the document itself (FastAPI serves it but doesn't list it); this file came from it
    for tpl, ops in SPEC["paths"].items():
        if method.lower() in ops and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", tpl), full):
            return tpl
    raise AssertionError(f"{method} {full} is not in sofabaton-x-server {SPEC['info']['version']}'s API")


def validator(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    # Refs are "#/components/schemas/X": embed the components so they resolve.
    return jsonschema.Draft202012Validator({**schema, "components": SPEC["components"]})


def response_schema(method: str, tpl: str, status: str = "200") -> dict[str, Any]:
    return SPEC["paths"][tpl][method.lower()]["responses"][status]["content"]["application/json"]["schema"]


async def exercise_everything(api: ServerAPI) -> None:
    """Call every ServerAPI method once."""
    await api.version()
    await api.auth_claimed()
    await api.hubs()
    await api.status(HUB)
    await api.info(HUB)
    await api.activities(HUB)
    await api.devices(HUB)
    await api.commands(HUB, SHIELD)
    await api.macros(HUB, WATCH_SHIELD)
    await api.favorites(HUB, WATCH_SHIELD)
    await api.running_activity(HUB)
    await api.presses(HUB, None, 10)
    await api.presses(HUB, 0, 10)
    await api.start_activity(HUB, WATCH_SHIELD)
    await api.stop_activity(HUB, WATCH_SHIELD)
    await api.send(HUB, SHIELD, 9)
    await api.find_remote(HUB)


def test_the_recorded_spec_is_the_version_we_built_against():
    assert SPEC["info"]["title"] == "sofabaton-x-server" and SPEC["info"]["version"].startswith("0.2.")


# --- 1 and 2: routes ---------------------------------------------------------------------
async def test_every_route_we_call_exists_with_that_method(fake):
    api = ServerAPI("http://sbx.test:8480", transport=fake.transport)
    await exercise_everything(api)
    await api.aclose()
    called = {(m, template_for(m, p)) for m, p in fake.requests}
    # The ones we exercised, in the spec's own terms. A new ServerAPI method shows up here.
    assert {tpl for m, tpl in called if m == "POST"} == FREE_CONTROL


def _sample(tpl: str) -> str:
    """A concrete path for a template, e.g. .../activities/{activity_id}/start -> .../activities/101/start."""
    path = tpl.removeprefix(PREFIX).replace("{activity_id}", "101").replace("{device_id}", "2")
    return re.sub(r"\{[^}]+\}", HUB, path)


def test_control_allow_list_is_exactly_the_free_control_routes():
    allowed = {
        tpl
        for tpl, ops in SPEC["paths"].items()
        if "post" in ops and any(p.fullmatch(_sample(tpl)) for p in api_mod._CONTROL_POSTS)
    }
    assert allowed == FREE_CONTROL


@pytest.mark.parametrize(
    "path",
    [
        "/hubs/a1b2c3/erase",
        "/hubs/a1b2c3/restore",
        "/hubs/a1b2c3/activities",  # create
        "/hubs/a1b2c3/activities/101/rename",
        "/hubs/a1b2c3/learn",
        "/hubs/a1b2c3/play",  # raw IR
        "/hubs/a1b2c3/resync-remote",
        "/hubs/a1b2c3/send/../erase",
    ],
)
async def test_edit_routes_are_refused_before_any_request(path):
    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"request made: {request.url}")

    api = ServerAPI("http://sbx.test:8480", transport=httpx.MockTransport(boom))
    with pytest.raises(RuntimeError, match="not a control route"):
        await api._request("POST", path)
    await api.aclose()


# --- 3: our types vs the server's schemas -----------------------------------------------------
def _nullable(hint: Any) -> bool:
    origin = typing.get_origin(hint)
    return (origin is typing.Union or origin is types.UnionType) and type(None) in get_args(hint)


def _spec_nullable(prop: dict[str, Any]) -> bool:
    return any(alt.get("type") == "null" for alt in prop.get("anyOf", [])) or prop.get("type") == "null"


@pytest.mark.parametrize(("ours", "theirs"), sorted(TYPEDDICTS.items()))
def test_typeddict_fields_exist_and_nullability_matches(ours, theirs):
    # include_extras keeps NotRequired[...] (the module uses `from __future__ import annotations`, so
    # __optional_keys__ can't see it); unwrap it for the nullability check.
    raw = get_type_hints(getattr(api_mod, ours), include_extras=True)
    optional = {n for n, h in raw.items() if typing.get_origin(h) is NotRequired}
    hints = {n: (get_args(h)[0] if n in optional else h) for n, h in raw.items()}
    schema = SCHEMAS[theirs]
    props = schema["properties"]
    missing = set(hints) - set(props)
    assert not missing, f"{ours} reads fields {theirs} doesn't have: {missing}"
    for name, hint in hints.items():
        if _spec_nullable(props[name]):
            assert _nullable(hint), f"{ours}.{name}: the server can send null, our type says it can't"
        if name not in schema.get("required", []) and "default" not in props[name]:
            # Not required and no default: the server may leave it out, so our type must be NotRequired
            # (which makes mypy insist on .get()).
            assert name in optional, f"{ours}.{name} may be absent from {theirs}; mark it NotRequired"


# --- 4: the fake's payloads vs the server's response schemas ----------------------------------
class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.seen: list[tuple[str, str, int, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = await self.inner.handle_async_request(request)
        body = await resp.aread()
        self.seen.append((request.method, request.url.path, resp.status_code, json.loads(body) if body else None))
        return httpx.Response(resp.status_code, headers=resp.headers, content=body)


async def test_every_fake_payload_validates_against_the_real_schema(fake):
    fake.running = WATCH_SHIELD
    fake.presses = [
        {
            "seq": 1,
            "hub_id": HUB,
            "device_id": 3,
            "command_id": 1,
            "slot": 1,
            "label": "Ask Claude",
            "press_type": "short",
            "resolution": "resolved",
            "transport": "mqtt",
            "source": "192.0.2.40",
            "received_at": "2026-10-07T20:00:00Z",
        }
    ]
    rec = Recorder(fake.transport)
    api = ServerAPI("http://sbx.test:8480", transport=rec)
    await exercise_everything(api)
    await api.aclose()
    checked = 0
    for method, path, status, body in rec.seen:
        tpl = template_for(method, path.removeprefix(PREFIX))
        if tpl == "/api/v1/openapi.json":
            continue
        errors = list(validator(response_schema(method, tpl, str(status))).iter_errors(body))
        assert not errors, f"{method} {tpl}: {errors[0].message} at {list(errors[0].absolute_path)}"
        checked += 1
    assert checked >= 15


@pytest.mark.parametrize(
    ("mode", "call", "status", "body_mode"),
    [
        # Mirrors sofabaton-x-server 0.2.4's routes_hub_data.py: send/start/stop read the catalog to check the id
        # first (503 with no hub session; 404 for an unknown id), then the proxy refuses (409) unless it owns the hub.
        ("observe", "send", "409", "observe"),
        ("disconnected", "send", "503", "disconnected"),
        ("disconnected", "start", "503", "disconnected"),
        ("disconnected", "find_remote", "409", "disconnected"),
        ("control", "send_unknown", "404", "control"),
    ],
)
async def test_fake_problem_bodies_validate(fake, mode, call, status, body_mode):
    fake.mode = mode
    fake.hub_connected = mode != "disconnected"
    rec = Recorder(fake.transport)
    api = ServerAPI("http://sbx.test:8480", transport=rec)
    calls = {
        "send": lambda: api.send(HUB, SHIELD, 9),
        "send_unknown": lambda: api.send(HUB, 999, 9),
        "start": lambda: api.start_activity(HUB, WATCH_SHIELD),
        "find_remote": lambda: api.find_remote(HUB),
    }
    with pytest.raises(SofabatonError):
        await calls[call]()
    await api.aclose()
    method, path, got, body = rec.seen[-1]
    assert str(got) == status
    schema = response_schema(method, template_for(method, path.removeprefix(PREFIX)), status)
    assert not list(validator(schema).iter_errors(body))
    assert body["mode"] == body_mode


async def test_fake_server_reports_a_fake_version():
    # So nobody mistakes the simulator for the real thing in doctor output.
    api = ServerAPI("http://sbx.test:8480", transport=FakeServer().transport)
    assert (await api.version()).endswith("-fake")
    await api.aclose()
