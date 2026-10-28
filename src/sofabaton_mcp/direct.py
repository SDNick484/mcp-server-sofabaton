"""Find the remote by talking to the hub directly: no sofabaton-x-server, no MQTT.

The X2's MQTT topics have no "find remote", so on an MQTT-only setup the only
way to make the remote beep is the hub's own TCP protocol, the one the
Sofabaton app and sofabaton-x speak. We speak exactly as much of it as that
needs, one short session per call:

  1. Listen on TCP (SOFABATON_DIRECT_PORT, default 8200).
  2. Send CALL_ME to the hub on UDP 8102, carrying our address and that port.
     The hub *connects back to us*: it dials the client, not the other way
     round. ASSUMPTION S-DIRECT-CALL-ME
  3. REQ_BANNER: the hub answers with its banner, whose opcode says the model
     (0x1502 = X2). We refuse anything but an X2. ASSUMPTION S-DIRECT-BANNER
  4. FIND_REMOTE_X2 (0x0323, payload 00 00 08). The remote beeps; the hub
     sends nothing back, so the only confirmation is the beep.
     ASSUMPTION S-DIRECT-FIND
  5. Hold the session a moment so the hub acts on it, then hang up.
     ASSUMPTION S-DIRECT-LINGER

The hub serves one client at a time, so this works only while nothing else
holds its session: the Sofabaton app, sofabaton-x-server or another proxy.
Then it simply doesn't dial back, and we say so. ASSUMPTION S-DIRECT-EXCLUSIVE
That is also why this path is used only when no sofabaton-x-server is
configured (the server holds the session and has its own find-remote route).

Every frame is ``A5 5A <opcode hi> <opcode lo> <payload> <sum8>``. The
opcode's high byte *is* the payload length, and sum8 is the low byte of the
sum of every byte before it. Source: m3tac0de/home-assistant-sofabaton-x1s,
docs/protocol/ (frame-format.md, connection-flow.md, opcodes.md), and the
sofabaton-x library it documents.

Nothing else from that protocol is here on purpose: it also carries edits,
deletes and erase, which this server never sends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from dataclasses import dataclass

from .api import SofabatonError

log = logging.getLogger(__name__)

SYNC = b"\xa5\x5a"
HUB_UDP_PORT = 8102
OP_CALL_ME = 0x0CC3
OP_REQ_BANNER = 0x0001
OP_FIND_REMOTE_X2 = 0x0323
FIND_REMOTE_X2_PAYLOAD = b"\x00\x00\x08"
# The banner is family 0x02; its opcode tells the model. ASSUMPTION S-DIRECT-BANNER
BANNER_FAMILY = 0x02
BANNER_MODELS = {0x1A02: "X1", 0x1D02: "X1S", 0x1502: "X2"}


def frame(opcode: int, payload: bytes = b"") -> bytes:
    """One wire frame. The opcode's high byte must equal the payload length."""
    if opcode >> 8 != len(payload):
        raise ValueError(f"opcode 0x{opcode:04X} implies {opcode >> 8} payload bytes, got {len(payload)}")
    body = SYNC + opcode.to_bytes(2, "big") + payload
    return body + bytes([sum(body) & 0xFF])


def call_me(our_ip: str, our_port: int) -> bytes:
    """CALL_ME: six reserved zero bytes, our IPv4 address, our TCP port (big-endian). ASSUMPTION S-DIRECT-CALL-ME"""
    return frame(OP_CALL_ME, b"\x00" * 6 + socket.inet_aton(our_ip) + our_port.to_bytes(2, "big"))


class Deframer:
    """Split the TCP byte stream into (opcode, payload) frames; frames with a bad checksum are dropped."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        out: list[tuple[int, bytes]] = []
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                del self._buf[:-1]  # a trailing 0xA5 may begin the next sync
                return out
            del self._buf[:start]
            if len(self._buf) < 5 or len(self._buf) < 5 + self._buf[2]:
                return out
            size = 5 + self._buf[2]
            raw = bytes(self._buf[:size])
            del self._buf[:size]
            if sum(raw[:-1]) & 0xFF != raw[-1]:
                log.debug("direct: dropped a frame with a bad checksum")
                continue
            out.append(((raw[2] << 8) | raw[3], raw[4:-1]))


def local_ip_toward(hub_ip: str) -> str:
    """The address this machine uses to reach the hub (what the hub dials back). A UDP connect sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((hub_ip, HUB_UDP_PORT))
        return str(s.getsockname()[0])


@dataclass(frozen=True)
class Session:
    model: str  # from the banner
    connect_s: float  # CALL_ME to the hub's connect-back
    sent: list[str]  # what went on the wire, for the model (no addresses)


class DirectHub:
    """One-shot sessions with the hub over its own TCP protocol. One at a time (the hub serves one client)."""

    connect_timeout = 10.0  # CALL_ME is repeated every call_me_interval until the hub dials back
    call_me_interval = 2.0  # what sofabaton-x uses
    banner_timeout = 3.0
    linger = 1.0  # ASSUMPTION S-DIRECT-LINGER

    def __init__(self, hub_ip: str, listen_port: int = 8200, *, hub_udp_port: int = HUB_UDP_PORT) -> None:
        self.hub_ip = hub_ip
        self.listen_port = listen_port  # 0: any free port (tests)
        self.hub_udp_port = hub_udp_port
        self._lock = asyncio.Lock()

    @staticmethod
    def plan(find: bool) -> list[str]:
        """What a session sends, described for the model (and for dry runs)."""
        steps = ["UDP CALL_ME -> hub:8102 (the hub connects back over TCP)", "TCP REQ_BANNER (00 01)"]
        if find:
            steps.append("TCP FIND_REMOTE_X2 (03 23 00 00 08)")
        return steps

    async def find_remote(self) -> Session:
        """Make the remote beep. Raises SofabatonError if the hub doesn't connect back or isn't an X2."""
        return await self._session(find=True)

    async def identify(self) -> Session:
        """Connect and read the banner only (doctor): proves the path works without making a sound."""
        return await self._session(find=False)

    async def _session(self, *, find: bool) -> Session:
        if self._lock.locked():
            raise SofabatonError("A direct session with the hub is already running; try again in a few seconds.")
        async with self._lock:
            conn: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = asyncio.Queue(1)

            async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                # Only the hub may use this session; anything else that finds the port is dropped.
                peer = writer.get_extra_info("peername")
                if not peer or peer[0] != self.hub_ip or conn.full():
                    writer.close()
                    return
                conn.put_nowait((reader, writer))

            try:
                server = await asyncio.start_server(accept, "0.0.0.0", self.listen_port)
            except OSError as exc:
                raise SofabatonError(
                    f"Can't listen on TCP port {self.listen_port} for the hub's connect-back ({exc.strerror}). "
                    "Something else uses it (sofabaton-x-server, or Home Assistant's Sofabaton X1S integration "
                    "use 8200): set SOFABATON_DIRECT_PORT to a free port."
                ) from exc
            try:
                port = server.sockets[0].getsockname()[1]
                hello = call_me(local_ip_toward(self.hub_ip), port)
                t0 = time.monotonic()
                reader, writer = await self._wait_for_hub(conn, hello)
                connect_s = time.monotonic() - t0
            finally:
                server.close()
            try:
                model = await self._banner(reader, writer)
                if model != "X2":
                    raise SofabatonError(
                        f"The hub at SOFABATON_HUB_IP is an {model}: finding the remote directly is only known for "
                        "the X2. Use sofabaton-x-server for this hub."
                    )
                if find:
                    writer.write(frame(OP_FIND_REMOTE_X2, FIND_REMOTE_X2_PAYLOAD))  # ASSUMPTION S-DIRECT-FIND
                    await writer.drain()
                    await asyncio.sleep(self.linger)  # ASSUMPTION S-DIRECT-LINGER
            finally:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
            log.info("direct: %s hub connected back in %.2fs%s", model, connect_s, "; find-remote sent" if find else "")
            return Session(model, connect_s, self.plan(find))

    async def _wait_for_hub(
        self, conn: asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]], hello: bytes
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setblocking(False)
        deadline = time.monotonic() + self.connect_timeout
        try:
            while (left := deadline - time.monotonic()) > 0:
                with contextlib.suppress(OSError):  # a send error is just another miss; the deadline decides
                    udp.sendto(hello, (self.hub_ip, self.hub_udp_port))
                try:
                    return await asyncio.wait_for(conn.get(), min(self.call_me_interval, left))
                except TimeoutError:
                    continue
        finally:
            udp.close()
        # ASSUMPTION S-DIRECT-EXCLUSIVE: silence most likely means someone else holds the session.
        raise SofabatonError(
            f"The hub didn't connect back within {self.connect_timeout:.0f}s. Most likely something else holds its "
            "session: close the Sofabaton app on every phone and tablet (and stop sofabaton-x-server or any other "
            "proxy for this hub), then try again. Otherwise check SOFABATON_HUB_IP, and that the hub can reach this "
            f"machine on TCP {self.listen_port or 'the listen port'} (firewall, a NATed container)."
        )

    async def _banner(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
        writer.write(frame(OP_REQ_BANNER))
        await writer.drain()
        dfr = Deframer()
        deadline = time.monotonic() + self.banner_timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                data = await asyncio.wait_for(reader.read(4096), left)
            except TimeoutError:
                break
            if not data:
                raise SofabatonError("The hub closed the connection before sending its banner. Try again.")
            for opcode, _payload in dfr.feed(data):
                # The payload starts with the hub's MAC: never logged or returned.
                if opcode & 0xFF == BANNER_FAMILY:
                    return BANNER_MODELS.get(opcode, f"unknown model (banner 0x{opcode:04X})")
        raise SofabatonError(
            "The hub connected back but didn't send its banner, so nothing was sent to it. Try again; if it keeps "
            "happening, please report it."
        )
