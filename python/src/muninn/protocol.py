"""Muninn wire protocol v2 — framing and frame codecs. See PROTOCOL.md.

Every frame: [type u8][length u16 BE][payload]. Two families:

Link frames (hop-by-hop, never forwarded):
    HELLO, PING, PONG, ROUTES, ROSTER

Mesh frames (routed end-to-end by node id):
    PACKET  — encrypted body (see content.py), optionally reliable
    ACK     — plaintext delivery confirmation routed back to the sender
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass

PROTOCOL_VERSION = 2

T_HELLO = 0x01
T_PING = 0x02
T_PONG = 0x03
T_ROUTES = 0x04
T_ROSTER = 0x05
T_PACKET = 0x10
T_ACK = 0x11

MAX_PAYLOAD = 0xFFFF
ID_LEN = 6
PUBKEY_LEN = 32
PACKET_ID_LEN = 16

DEFAULT_TTL = 8
ROUTE_UNREACHABLE = 0xFF

# PACKET flags
F_RELIABLE = 0x01  # receiver must ACK; sender retransmits until ACKed
F_BULK = 0x02  # low priority (file chunks) — relays queue it behind chat
F_REALTIME = 0x04  # latency-sensitive (games, typing) — relays send it first


class FrameTooLarge(ValueError):
    """Payload exceeds the 65535-byte wire limit for a single frame."""


class MalformedFrame(ValueError):
    """Frame payload could not be decoded."""


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise FrameTooLarge(f"payload {len(payload)} bytes exceeds max {MAX_PAYLOAD}")
    return struct.pack("!BH", frame_type, len(payload)) + payload


def read_frame(sock) -> tuple[int, bytes]:
    header = recv_exact(sock, 3)
    frame_type, length = struct.unpack("!BH", header)
    return frame_type, recv_exact(sock, length)


def recv_exact(sock, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


# --- ids -------------------------------------------------------------------


def id_to_bytes(node_id: str) -> bytes:
    b = bytes(int(x, 16) for x in node_id.split(":"))
    if len(b) != ID_LEN:
        raise ValueError(f"bad node id {node_id!r}")
    return b


def bytes_to_id(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in b)


def new_packet_id() -> bytes:
    return os.urandom(PACKET_ID_LEN)


def random_node_id() -> str:
    b = bytearray(os.urandom(ID_LEN))
    b[0] = (b[0] & 0xFC) | 0x02  # locally administered, unicast
    return bytes_to_id(bytes(b))


# --- HELLO -----------------------------------------------------------------


@dataclass
class Hello:
    pubkey: bytes
    node_id: str
    version: int
    features: int
    name: str
    name_ts: int


def encode_hello(
    pubkey: bytes, node_id: str, name: str, name_ts: int, features: int = 0
) -> bytes:
    nb = name.encode("utf-8")[:255]
    payload = (
        pubkey
        + id_to_bytes(node_id)
        + struct.pack("!BIIB", PROTOCOL_VERSION, features, name_ts, len(nb))
        + nb
    )
    return encode_frame(T_HELLO, payload)


def decode_hello(p: bytes) -> Hello:
    if len(p) < 48:
        raise MalformedFrame("hello too short")
    version, features, name_ts, nlen = struct.unpack("!BIIB", p[38:48])
    name = p[48 : 48 + nlen].decode("utf-8", errors="replace")
    return Hello(p[:32], bytes_to_id(p[32:38]), version, features, name, name_ts)


# --- PING / PONG -------------------------------------------------------------


def encode_ping(token: bytes) -> bytes:
    return encode_frame(T_PING, token)


def encode_pong(token: bytes) -> bytes:
    return encode_frame(T_PONG, token)


# --- ROUTES (distance vector) -----------------------------------------------


def encode_routes(entries: list[tuple[str, int]]) -> bytes:
    entries = entries[:255]
    parts = [struct.pack("!B", len(entries))]
    for node_id, hops in entries:
        parts.append(id_to_bytes(node_id))
        parts.append(struct.pack("!B", min(hops, ROUTE_UNREACHABLE)))
    return encode_frame(T_ROUTES, b"".join(parts))


def decode_routes(p: bytes) -> list[tuple[str, int]]:
    if not p:
        return []
    n = p[0]
    if len(p) < 1 + n * 7:
        raise MalformedFrame("routes truncated")
    return [(bytes_to_id(p[1 + i * 7 : 7 + i * 7]), p[7 + i * 7]) for i in range(n)]


# --- ROSTER (peer gossip) ----------------------------------------------------


@dataclass
class RosterEntry:
    node_id: str
    pubkey: bytes
    name: str
    name_ts: int


def encode_roster(entries: list[RosterEntry]) -> list[bytes]:
    """Encode as one or more frames (each <= 255 entries and < 64 KiB)."""
    frames = []
    for i in range(0, len(entries), 200):
        batch = entries[i : i + 200]
        parts = [struct.pack("!B", len(batch))]
        for e in batch:
            nb = e.name.encode("utf-8")[:255]
            parts += [
                id_to_bytes(e.node_id),
                e.pubkey,
                struct.pack("!IB", e.name_ts, len(nb)),
                nb,
            ]
        frames.append(encode_frame(T_ROSTER, b"".join(parts)))
    return frames


def decode_roster(p: bytes) -> list[RosterEntry]:
    if not p:
        return []
    n = p[0]
    off = 1
    out = []
    for _ in range(n):
        if len(p) < off + 43:
            raise MalformedFrame("roster truncated")
        nid = bytes_to_id(p[off : off + 6])
        pk = p[off + 6 : off + 38]
        name_ts, nlen = struct.unpack("!IB", p[off + 38 : off + 43])
        name = p[off + 43 : off + 43 + nlen].decode("utf-8", errors="replace")
        out.append(RosterEntry(nid, pk, name, name_ts))
        off += 43 + nlen
    return out


# --- PACKET ------------------------------------------------------------------

# packet_id(16) src(6) dst(6) ttl(1) flags(1) ts(4) src_pubkey(32) | box
PACKET_HEADER = struct.Struct("!16s6s6sBBI32s")


@dataclass
class Packet:
    packet_id: bytes
    src: str
    dst: str
    ttl: int
    flags: int
    ts: int
    src_pubkey: bytes
    box: bytes  # nonce(24) + ciphertext

    @property
    def reliable(self) -> bool:
        return bool(self.flags & F_RELIABLE)


def encode_packet(pkt: Packet) -> bytes:
    hdr = PACKET_HEADER.pack(
        pkt.packet_id,
        id_to_bytes(pkt.src),
        id_to_bytes(pkt.dst),
        pkt.ttl,
        pkt.flags,
        pkt.ts,
        pkt.src_pubkey,
    )
    return encode_frame(T_PACKET, hdr + pkt.box)


def decode_packet(p: bytes) -> Packet:
    if len(p) < PACKET_HEADER.size + 40:
        raise MalformedFrame("packet too short")
    pid, src, dst, ttl, flags, ts, pub = PACKET_HEADER.unpack_from(p)
    return Packet(
        pid,
        bytes_to_id(src),
        bytes_to_id(dst),
        ttl,
        flags,
        ts,
        pub,
        p[PACKET_HEADER.size :],
    )


def packet_with_ttl(raw_payload: bytes, ttl: int) -> bytes:
    """Re-frame a received PACKET payload with a decremented TTL."""
    return encode_frame(T_PACKET, raw_payload[:28] + bytes([ttl]) + raw_payload[29:])


MAX_BOX = MAX_PAYLOAD - PACKET_HEADER.size


# --- ACK -------------------------------------------------------------------

# packet_id(16) from(6) to(6) ttl(1)
ACK_STRUCT = struct.Struct("!16s6s6sB")


@dataclass
class Ack:
    packet_id: bytes
    src: str  # the acknowledging node (final recipient of the packet)
    dst: str  # the packet's original sender
    ttl: int


def encode_ack(ack: Ack) -> bytes:
    return encode_frame(
        T_ACK,
        ACK_STRUCT.pack(
            ack.packet_id, id_to_bytes(ack.src), id_to_bytes(ack.dst), ack.ttl
        ),
    )


def decode_ack(p: bytes) -> Ack:
    if len(p) < ACK_STRUCT.size:
        raise MalformedFrame("ack too short")
    pid, src, dst, ttl = ACK_STRUCT.unpack_from(p)
    return Ack(pid, bytes_to_id(src), bytes_to_id(dst), ttl)


def now() -> int:
    return int(time.time())
