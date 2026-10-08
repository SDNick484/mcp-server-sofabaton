"""The transport layer: the route allow-list and error translation."""

from __future__ import annotations

import pytest

from sofabaton_mcp.api import ServerAPI, SofabatonError

from .conftest import HUB

pytestmark = pytest.mark.anyio


@pytest.fixture
async def api(fake):
    a = ServerAPI("http://sbx.test:8480", transport=fake.transport)
    yield a
    await a.aclose()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", f"/hubs/{HUB}/snapshot/refresh"),
        ("POST", f"/hubs/{HUB}/devices"),
        ("POST", f"/hubs/{HUB}/activities/101/rename"),
        ("POST", f"/hubs/{HUB}/send/../erase"),
    ],
)
async def test_non_control_posts_never_leave_the_process(api, fake, method, path):
    # Writes need a token we don't hold anyway; this is the second wall.
    with pytest.raises(RuntimeError, match="not a control route"):
        await api._request(method, path)
    assert fake.requests == []


async def test_control_posts_are_allowed(api, fake):
    await api.send(HUB, 101, 182)
    await api.start_activity(HUB, 101)
    await api.stop_activity(HUB, 101)
    await api.find_remote(HUB)
    assert [p for p, _ in fake.posts] == [
        f"/hubs/{HUB}/send",
        f"/hubs/{HUB}/activities/101/start",
        f"/hubs/{HUB}/activities/101/stop",
        f"/hubs/{HUB}/find-remote",
    ]


async def test_unreachable_server_says_where_it_looked(api, fake):
    fake.unreachable = True
    with pytest.raises(SofabatonError, match=r"Can't reach sofabaton-x-server at http://sbx.test:8480"):
        await api.hubs()


async def test_observe_mode_tells_you_to_close_the_app(api, fake):
    fake.mode = "observe"
    with pytest.raises(SofabatonError, match="close the app on every phone and tablet"):
        await api.send(HUB, 101, 182)


async def test_no_hub_session_says_so(api, fake):
    fake.hub_connected = False
    fake.mode = "disconnected"
    with pytest.raises(SofabatonError, match="no session with the hub"):
        await api.activities(HUB)


async def test_other_problems_carry_the_servers_detail(api, fake):
    with pytest.raises(SofabatonError, match=r"\(404\): hub_not_found"):
        await api.status("nope")
