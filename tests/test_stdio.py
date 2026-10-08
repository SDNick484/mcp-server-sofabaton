"""End to end: the installed entry point over stdio.

No sofabaton-x-server is running, so list_tools works and get_status answers
with a "can't reach" limitation (not an error: it's the tool the model calls
first, so it has to explain the problem rather than fail).
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from mcp import Client, StdioServerParameters

from .test_tools import TOOL_NAMES

pytestmark = pytest.mark.anyio


async def test_serve_over_stdio():
    exe = shutil.which("mcp-server-sofabaton", path=os.path.dirname(sys.executable))
    if exe is None:
        pytest.skip("entry point not installed (pip install -e .)")
    # Port 9 (discard) on loopback: refused at once, nothing to wait for.
    env = {**os.environ, "SOFABATON_URL": "http://127.0.0.1:9"}
    env.pop("SOFABATON_HUB", None)
    async with Client(StdioServerParameters(command=exe, args=[], env=env)) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOL_NAMES
        result = await c.call_tool("get_status", {})
        assert not result.is_error
        st = result.structured_content
        assert st["via"] is None and st["server"]["reachable"] is False
        # Logs and tool output both redact addresses; the loopback's last octet survives.
        assert any("Can't reach sofabaton-x-server" in lim for lim in st["limitations"])
