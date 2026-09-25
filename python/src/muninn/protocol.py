import re
import struct
import time
import uuid

TYPE_HANDSHAKE = 0x01
TYPE_MESSAGE = 0x02
TYPE_ACK = 0x03
TYPE_GROUP_SETUP = 0x04
TYPE_READ = 0x05
TYPE_PROFILE = 0x06
TYPE_PEER_ANNC = 0x07
TYPE_ROUTES = 0x08

MAX_PAYLOAD = 0xFFFF  # uint16 — see PROTOCOL.md

# group_id used by 1:1 DMs. 16 zero bytes, per PROTOCOL.md.
GROUP_ZERO_ID = b"\x00" * 16


# Routes are advertised out to this many hops from the advertiser, so a
# receiver learns about devices up to MAX_HOPS + 1 hops away. Four covers a
# chain of people strung along an aisle; the cap also bounds how long a stale
# route can bounce around after a device leaves.
MAX_ROUTE_HOPS = 3


class FrameTooLarge(ValueError):
    """Payload exceeds the 65535-byte wire limit for a single frame."""


class MalformedFrame(ValueError):
    """A peer sent a frame whose payload does not match its type's layout.

    Never fatal: the receive loop drops the one frame and keeps the session.
    A peer running a newer build must not be able to disconnect us by sending
    something we do not fully understand.
    """


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise FrameTooLarge(f"payload {len(payload)} bytes exceeds max {MAX_PAYLOAD}")
    return struct.pack("!BH", frame_type, len(payload)) + payload


def read_frame(sock) -> tuple[int, bytes]:
    header = recv_exact(sock, 3)
    frame_type, length = struct.unpack("!BH", header)
    payload = recv_exact(sock, length)
    return frame_type, payload


def recv_exact(sock, n: int) -> bytes:
    # bytearray avoids the O(n^2) copy that `data += chunk` produces when a
    # large payload arrives in many small recv chunks.
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


# --- Handshake ---


def encode_handshake(pubkey: bytes, wire_id: bytes = b"") -> bytes:
    """Handshake payload: 32-byte X25519 pubkey, optionally followed by the
    sender's 6-byte wire id (its addressing identity). Omitting wire_id yields
    the legacy 32-byte form; peers receiving it fall back to the transport MAC.
    """
    return encode_frame(TYPE_HANDSHAKE, pubkey + wire_id)


def decode_handshake(payload: bytes) -> tuple[bytes, bytes | None]:
    """Returns (pubkey, wire_id). wire_id is None for legacy 32-byte payloads."""
    if len(payload) not in (32, 38):
        raise ValueError(
            f"handshake payload must be 32 or 38 bytes, got {len(payload)}"
        )
    return payload[:32], (payload[32:] if len(payload) == 38 else None)


# --- Message ---


def encode_message(
    group_id: bytes,
    msg_id: bytes,
    sender_mac: bytes,
    dest_mac: bytes,
    encrypted: bytes,
    timestamp: int | None = None,
) -> bytes:
    """Encode a message frame. `timestamp` defaults to now; pass it explicitly
    to produce a byte-for-byte reproducible frame (the wire vectors do)."""
    if timestamp is None:
        timestamp = int(time.time())
    ts = struct.pack("!I", timestamp & 0xFFFFFFFF)
    payload = group_id + msg_id + sender_mac + dest_mac + ts + encrypted
    return encode_frame(TYPE_MESSAGE, payload)


MESSAGE_HEADER_BYTES = 16 + 16 + 6 + 6 + 4


def decode_message(payload: bytes):
    if len(payload) < MESSAGE_HEADER_BYTES:
        raise MalformedFrame(f"message payload too short: {len(payload)}")
    group_id = payload[0:16]
    msg_id = payload[16:32]
    sender_id = payload[32:38]
    final_dest = payload[38:44]
    timestamp = struct.unpack("!I", payload[44:48])[0]
    encrypted = payload[48:]
    return group_id, msg_id, sender_id, final_dest, timestamp, encrypted


# --- ACK ---


def encode_ack(msg_id: bytes, from_mac: bytes) -> bytes:
    return encode_frame(TYPE_ACK, msg_id + from_mac)


def _check_receipt(payload: bytes, what: str) -> None:
    if len(payload) < 22:
        raise MalformedFrame(f"{what} payload too short: {len(payload)}")


def decode_ack(payload: bytes):
    _check_receipt(payload, "ack")
    msg_id = payload[0:16]
    from_mac = payload[16:22]
    return msg_id, from_mac


# --- Read receipt ---


def encode_read(msg_id: bytes, from_mac: bytes) -> bytes:
    return encode_frame(TYPE_READ, msg_id + from_mac)


def decode_read(payload: bytes):
    _check_receipt(payload, "read")
    msg_id = payload[0:16]
    from_mac = payload[16:22]
    return msg_id, from_mac


# --- Group Setup ---


def encode_group_setup(
    group_id: bytes,
    members: list[tuple[bytes, bytes]],
    name: str,
) -> bytes:
    """members: list of (mac_bytes, pubkey_bytes)."""
    parts = [group_id, struct.pack("!B", len(members))]
    for mac, pubkey in members:
        parts.append(mac)
        parts.append(pubkey)
    name_bytes = name.encode("utf-8")
    parts.append(struct.pack("!H", len(name_bytes)))
    parts.append(name_bytes)
    return encode_frame(TYPE_GROUP_SETUP, b"".join(parts))


def decode_group_setup(
    payload: bytes,
) -> tuple[bytes, list[tuple[bytes, bytes]], str]:
    if len(payload) < 17:
        raise MalformedFrame(f"group setup payload too short: {len(payload)}")
    group_id = payload[0:16]
    member_count = payload[16]
    offset = 17
    if len(payload) < offset + member_count * 38 + 2:
        raise MalformedFrame(f"group setup truncated: {member_count} members won't fit")
    members = []
    for _ in range(member_count):
        mac = payload[offset : offset + 6]
        pubkey = payload[offset + 6 : offset + 38]
        members.append((mac, pubkey))
        offset += 38
    name_length = struct.unpack("!H", payload[offset : offset + 2])[0]
    offset += 2
    if len(payload) < offset + name_length:
        raise MalformedFrame("group name truncated")
    name = payload[offset : offset + name_length].decode("utf-8", errors="replace")
    return group_id, members, name


# --- Profile ---


def encode_profile(name: str) -> bytes:
    """Self-chosen display name. Sent immediately after handshake."""
    return encode_frame(TYPE_PROFILE, name.encode("utf-8"))


def decode_profile(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


# --- Peer announcement ---


def _truncate_utf8(text: str, limit: int) -> bytes:
    """Encode `text`, cut to at most `limit` bytes on a codepoint boundary.

    A naive `text.encode()[:limit]` can split a multi-byte sequence, and the
    decoder (errors="replace") would render the tail as U+FFFD on the peer.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return encoded
    # Back off to the start of the codepoint that straddles the cut. UTF-8
    # continuation bytes are 0b10xxxxxx.
    end = limit
    while end > 0 and (encoded[end] & 0xC0) == 0x80:
        end -= 1
    return encoded[:end]


def encode_peer_annc(peers: list[tuple[bytes, bytes, str]]) -> bytes:
    """peers: list of (mac_6_bytes, pubkey_32_bytes, display_name)."""
    if len(peers) > 255:
        raise ValueError(f"peer_count is a uint8; got {len(peers)} peers")
    parts = [struct.pack("!B", len(peers))]
    for mac, pubkey, name in peers:
        name_bytes = _truncate_utf8(name, 255)
        parts.append(mac)
        parts.append(pubkey)
        parts.append(struct.pack("!B", len(name_bytes)))
        parts.append(name_bytes)
    return encode_frame(TYPE_PEER_ANNC, b"".join(parts))


def decode_peer_annc(payload: bytes) -> list[tuple[bytes, bytes, str]]:
    if not payload:
        raise MalformedFrame("peer annc payload is empty")
    count = payload[0]
    offset = 1
    peers = []
    for _ in range(count):
        if len(payload) < offset + 39:
            raise MalformedFrame(f"peer annc truncated after {len(peers)} entries")
        if len(payload) < offset + 39 + payload[offset + 38]:
            raise MalformedFrame("peer annc name truncated")
        mac = payload[offset : offset + 6]
        pubkey = payload[offset + 6 : offset + 38]
        name_len = payload[offset + 38]
        name = payload[offset + 39 : offset + 39 + name_len].decode(
            "utf-8", errors="replace"
        )
        peers.append((mac, pubkey, name))
        offset += 39 + name_len
    return peers


# --- Routes ---


def encode_routes(routes: list[tuple[bytes, int]]) -> bytes:
    """routes: list of (wire_id_6_bytes, hops). hops=1 means the sender holds a
    live session with that device. A full snapshot: the receiver replaces
    whatever this sender told it before."""
    if len(routes) > 255:
        raise ValueError(f"route count is a uint8; got {len(routes)}")
    parts = [struct.pack("!B", len(routes))]
    for wire_id, hops in routes:
        if len(wire_id) != 6:
            raise ValueError("route wire id must be 6 bytes")
        if not 1 <= hops <= 255:
            raise ValueError(f"hops must be 1..255, got {hops}")
        parts.append(wire_id)
        parts.append(struct.pack("!B", hops))
    return encode_frame(TYPE_ROUTES, b"".join(parts))


def decode_routes(payload: bytes) -> list[tuple[bytes, int]]:
    if not payload:
        raise MalformedFrame("routes payload is empty")
    count = payload[0]
    if len(payload) < 1 + count * 7:
        raise MalformedFrame(f"routes truncated: {count} entries won't fit")
    routes = []
    for i in range(count):
        offset = 1 + i * 7
        hops = payload[offset + 6]
        if hops == 0:
            raise MalformedFrame("route with zero hops")
        routes.append((payload[offset : offset + 6], hops))
    return routes


# --- Helpers ---


_MAC_RE = re.compile(r"\A[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\Z")


def mac_to_bytes(mac_str: str) -> bytes:
    """Parse "AA:BB:CC:DD:EE:FF" into 6 bytes, MSB first.

    Strict on purpose: every wire field that takes a MAC is fixed at 6 bytes,
    so a malformed address must raise here rather than silently shift every
    following field in the frame.
    """
    if not _MAC_RE.match(mac_str):
        raise ValueError(f"not a 6-octet MAC address: {mac_str!r}")
    return bytes.fromhex(mac_str.replace(":", ""))


def bytes_to_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac_bytes)


def new_msg_id() -> bytes:
    return uuid.uuid4().bytes


def new_group_id() -> bytes:
    return uuid.uuid4().bytes
