import struct
import uuid

TYPE_HANDSHAKE = 0x01
TYPE_MESSAGE = 0x02
TYPE_ACK = 0x03


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    return struct.pack("!BH", frame_type, len(payload)) + payload


def read_frame(sock) -> tuple[int, bytes]:
    header = recv_exact(sock, 3)
    frame_type, length = struct.unpack("!BH", header)
    payload = recv_exact(sock, length)
    return frame_type, payload


def recv_exact(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


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
    import time

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


# --- Helpers ---


def mac_to_bytes(mac_str: str) -> bytes:
    return bytes(int(b, 16) for b in mac_str.split(":"))


def bytes_to_mac(mac_bytes: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac_bytes)


def new_msg_id() -> bytes:
    return uuid.uuid4().bytes


def new_group_id() -> bytes:
    return uuid.uuid4().bytes
