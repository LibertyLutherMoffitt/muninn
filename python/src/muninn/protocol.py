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

MAX_PAYLOAD = 0xFFFF  # uint16 — see PROTOCOL.md


class FrameTooLarge(ValueError):
    """Payload exceeds the 65535-byte wire limit for a single frame."""


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


def encode_handshake(pubkey: bytes) -> bytes:
    return encode_frame(TYPE_HANDSHAKE, pubkey)


# --- Message ---


def encode_message(
    group_id: bytes,
    msg_id: bytes,
    sender_mac: bytes,
    dest_mac: bytes,
    encrypted: bytes,
) -> bytes:
    timestamp = struct.pack("!I", int(time.time()))
    payload = group_id + msg_id + sender_mac + dest_mac + timestamp + encrypted
    return encode_frame(TYPE_MESSAGE, payload)


def decode_message(payload: bytes):
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


def decode_ack(payload: bytes):
    msg_id = payload[0:16]
    from_mac = payload[16:22]
    return msg_id, from_mac


# --- Read receipt ---


def encode_read(msg_id: bytes, from_mac: bytes) -> bytes:
    return encode_frame(TYPE_READ, msg_id + from_mac)


def decode_read(payload: bytes):
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
    group_id = payload[0:16]
    member_count = payload[16]
    offset = 17
    members = []
    for _ in range(member_count):
        mac = payload[offset : offset + 6]
        pubkey = payload[offset + 6 : offset + 38]
        members.append((mac, pubkey))
        offset += 38
    name_length = struct.unpack("!H", payload[offset : offset + 2])[0]
    offset += 2
    name = payload[offset : offset + name_length].decode("utf-8")
    return group_id, members, name


# --- Profile ---


def encode_profile(name: str) -> bytes:
    """Self-chosen display name. Sent immediately after handshake."""
    return encode_frame(TYPE_PROFILE, name.encode("utf-8"))


def decode_profile(payload: bytes) -> str:
    return payload.decode("utf-8")


# --- Peer announcement ---


def encode_peer_annc(peers: list[tuple[bytes, bytes]]) -> bytes:
    """peers: list of (mac_6_bytes, pubkey_32_bytes)."""
    parts = [struct.pack("!B", len(peers))]
    for mac, pubkey in peers:
        parts.append(mac)
        parts.append(pubkey)
    return encode_frame(TYPE_PEER_ANNC, b"".join(parts))


def decode_peer_annc(payload: bytes) -> list[tuple[bytes, bytes]]:
    count = payload[0]
    offset = 1
    peers = []
    for _ in range(count):
        mac = payload[offset : offset + 6]
        pubkey = payload[offset + 6 : offset + 38]
        peers.append((mac, pubkey))
        offset += 38
    return peers


# --- Helpers ---


def mac_to_bytes(mac_str: str) -> bytes:
    return bytes(int(b, 16) for b in mac_str.split(":"))


def bytes_to_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac_bytes)


def new_msg_id() -> bytes:
    return uuid.uuid4().bytes


def new_group_id() -> bytes:
    return uuid.uuid4().bytes
