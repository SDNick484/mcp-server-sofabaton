"""The hub's own TCP side, as far as finding the remote goes: answers CALL_ME by connecting back.

Like FakeX2, it writes its frames out by hand instead of using direct.py, so a
mistake there can't be mirrored here and go unnoticed. What it does, and what
the X2 did on 2026-10-27:

  UDP CALL_ME (0x0CC3)       -> connect back over TCP to the address and port in it
  TCP REQ_BANNER (0x0001)    -> a banner (0x1502 on an X2), starting with the MAC
  TCP FIND_REMOTE_X2 (0x0323)-> the remote beeps; no reply at all

`busy=True` plays a hub whose session someone else holds: it ignores CALL_ME.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from .hub_state import Executed, FakeHubState

_BANNER_OPCODE = {"X1": b"\x1a\x02", "X1S": b"\x1d\x02", "X2": b"\x15\x02"}


def _sum8(b: bytes) -> bytes:
    return bytes([sum(b) & 0xFF])


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, hub: FakeDirectHub) -> None:
        self.hub = hub

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        self.hub.call_mes.append(data)
        # A5 5A 0C C3, 6 zero bytes, IPv4, port, checksum: 17 bytes.
        if self.hub.busy or len(data) != 17 or data[:4] != b"\xa5\x5a\x0c\xc3" or _sum8(data[:-1])[0] != data[-1]:
            return
        if self.hub.connected:
            return  # one session at a time, like the hub
        ip, port = socket.inet_ntoa(data[10:14]), int.from_bytes(data[14:16], "big")
        self.hub.connected = True
        self.hub.tasks.append(asyncio.ensure_future(self.hub.serve(ip, port)))


class FakeDirectHub:
    def __init__(self, state: FakeHubState, *, model: str = "X2", busy: bool = False) -> None:
        self.state = state
        self.model = model
        self.busy = busy
        self.connected = False
        self.call_mes: list[bytes] = []
        self.received: list[bytes] = []  # every TCP frame we got, raw
        self.tasks: list[asyncio.Future[None]] = []
        self.port = 0
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> int:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: _Udp(self), local_addr=("127.0.0.1", 0))
        self._transport = transport
        self.port = transport.get_extra_info("sockname")[1]
        return self.port

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def serve(self, ip: str, port: int) -> None:
        reader, writer = await asyncio.open_connection(ip, port)
        buf = b""
        try:
            while data := await reader.read(256):
                buf += data
                while len(buf) >= 5 and len(buf) >= 5 + buf[2]:
                    raw, buf = buf[: 5 + buf[2]], buf[5 + buf[2] :]
                    self.received.append(raw)
                    if raw == b"\xa5\x5a\x00\x01\x00":  # REQ_BANNER
                        mac = bytes.fromhex(self.state.mac)
                        body = b"\xa5\x5a" + _BANNER_OPCODE[self.model]
                        # Opcode-hi is the payload length: 21 bytes on the X2, as recorded.
                        payload = (mac + b"\x00\x03\x20\x22\x11\x20\x09" + b"X2 HUB").ljust(body[2], b"\x00")
                        writer.write(body + payload + _sum8(body + payload))
                        await writer.drain()
                    elif raw == b"\xa5\x5a\x03\x23\x00\x00\x08\x2d":  # FIND_REMOTE_X2: beep, say nothing
                        self.state.executed.append(Executed("direct", "find_remote", 0))
        finally:
            writer.close()
            self.connected = False
