"""Entry point: `mcp-server-sofabaton` (serve) and `mcp-server-sofabaton check`.

`serve` speaks stdio by default; `serve --http` runs it as a long-lived HTTP
service behind Cloudflare Access (see remote.py and the README).

`check` is first contact: it reaches sofabaton-x-server, picks the hub the same
way the server will, and prints the names the model will use.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__, remote
from .api import ServerAPI, SofabatonError
from .client import SofabatonClient
from .config import load_settings


def _setup_logging(level: int = logging.INFO) -> None:
    # stdout belongs to the MCP stdio transport; logs must go to stderr.
    logging.basicConfig(level=level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")


async def _cmd_check() -> int:
    settings = load_settings()
    api = ServerAPI(settings.url)
    c = SofabatonClient(settings, api)
    try:
        st = await c.status()
        hub = f"{st['hub_name'] or 'name unknown'}, {st['model'] or 'model unknown'}"
        print(f"sofabaton-x-server at {settings.url}, hub {st['hub_id']} ({hub})")
        print(f"Mode: {st['mode']}  (commands work only in 'control')")
        running = st["running_activity"]
        print(f"Running: {running['name'] if running else 'nothing'}")
        print("Activities:")
        for a in await c.activities():
            print(f"  {a['name']}")
        print("Devices:")
        for d in await c.devices():
            print(f"  {d['name']}  [{d['brand'] or '?'} {d['device_class'] or ''}]")
    except SofabatonError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        await api.aclose()
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mcp-server-sofabaton", description="MCP server for Sofabaton hubs via sofabaton-x-server"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Run the MCP server (stdio by default, or --http)")
    remote.add_http_arguments(serve, default_port=8714, default_path="/sofabaton/mcp")
    sub.add_parser("check", help="Reach sofabaton-x-server, pick the hub, and list what it has")
    args = parser.parse_args(argv)

    if args.cmd == "check":
        _setup_logging(logging.WARNING)
        sys.exit(asyncio.run(_cmd_check()))

    _setup_logging()
    from .server import mcp  # imported late: `check` doesn't need the server code

    if getattr(args, "http", False):
        try:
            remote.serve_http(mcp, remote.http_config(args))
        except remote.ConfigError as exc:
            parser.exit(2, f"mcp-server-sofabaton: {exc}\n")
    else:
        mcp.run()  # stdio: JSON-RPC over stdin/stdout
