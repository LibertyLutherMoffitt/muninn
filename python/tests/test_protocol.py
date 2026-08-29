"""Wire-format tests for protocol.py.

These assert exact byte layouts, because PROTOCOL.md is the cross-platform
contract with the Kotlin client. A change that breaks one of these breaks
Android interop even if every Python test still passes.
"""

import struct

import pytest

from muninn import protocol


# --- Frame header ---


def test_frame_header_is_type_then_big_endian_length():
    frame = protocol.encode_frame(0x02, b"hello")
    assert frame[0] == 0x02
    assert frame[1:3] == b"\x00\x05"
    assert frame[3:] == b"hello"


def test_empty_payload_is_a_legal_frame():
    assert protocol.encode_frame(protocol.TYPE_PROFILE, b"") == b"\x06\x00\x00"


def test_frame_at_the_size_limit_encodes():
    payload = b"\x00" * protocol.MAX_PAYLOAD
    frame = protocol.encode_frame(protocol.TYPE_MESSAGE, payload)
    assert frame[1:3] == b"\xff\xff"
    assert len(frame) == 3 + protocol.MAX_PAYLOAD


def test_frame_over_the_size_limit_raises():
    with pytest.raises(protocol.FrameTooLarge):
        protocol.encode_frame(protocol.TYPE_MESSAGE, b"\x00" * (protocol.MAX_PAYLOAD + 1))


def test_frame_type_values_match_the_spec():
    # These constants are the contract; Kotlin hardcodes the same numbers.
    assert (
        protocol.TYPE_HANDSHAKE,
        protocol.TYPE_MESSAGE,
        protocol.TYPE_ACK,
        protocol.TYPE_GROUP_SETUP,
        protocol.TYPE_READ,
        protocol.TYPE_PROFILE,
        protocol.TYPE_PEER_ANNC,
    ) == (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07)


# --- read_frame / recv_exact ---


class FakeSock:
    """Feeds bytes in caller-chosen chunks so recv_exact's loop is exercised."""

    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, n):
        if not self.chunks:
            return b""
        head = self.chunks[0]
        if len(head) <= n:
            return self.chunks.pop(0)
        self.chunks[0] = head[n:]
        return head[:n]


def test_read_frame_reassembles_across_partial_recvs():
    frame = protocol.encode_frame(protocol.TYPE_MESSAGE, b"abcdefghij")
    sock = FakeSock([frame[i : i + 1] for i in range(len(frame))])
    assert protocol.read_frame(sock) == (protocol.TYPE_MESSAGE, b"abcdefghij")


def test_read_frame_raises_on_eof_mid_frame():
    frame = protocol.encode_frame(protocol.TYPE_MESSAGE, b"abcdefghij")
    with pytest.raises(ConnectionError):
        protocol.read_frame(FakeSock([frame[:6]]))


def test_read_frame_leaves_trailing_bytes_for_the_next_call():
    two = protocol.encode_frame(protocol.TYPE_ACK, b"a") + protocol.encode_frame(
        protocol.TYPE_READ, b"b"
    )
    sock = FakeSock([two])
    assert protocol.read_frame(sock) == (protocol.TYPE_ACK, b"a")
    assert protocol.read_frame(sock) == (protocol.TYPE_READ, b"b")


# --- Handshake ---


def test_handshake_carries_pubkey_then_wire_id():
    pub = bytes(range(32))
    wire = b"\xaa\xbb\xcc\xdd\xee\xff"
    frame = protocol.encode_handshake(pub, wire)
    assert frame[0] == protocol.TYPE_HANDSHAKE
    assert struct.unpack("!H", frame[1:3])[0] == 38
    assert protocol.decode_handshake(frame[3:]) == (pub, wire)


def test_legacy_32_byte_handshake_decodes_with_no_wire_id():
    pub = bytes(range(32))
    assert protocol.decode_handshake(pub) == (pub, None)


@pytest.mark.parametrize("size", [0, 31, 33, 37, 39, 64])
def test_handshake_of_any_other_length_is_rejected(size):
    with pytest.raises(ValueError):
        protocol.decode_handshake(b"\x00" * size)


# --- Message ---


def test_message_field_offsets_match_the_spec():
    gid = b"\x11" * 16
    mid = b"\x22" * 16
    src = b"\x33" * 6
    dst = b"\x44" * 6
    enc = b"\x55" * 40  # 24-byte nonce + ciphertext, opaque here
    payload = protocol.encode_message(gid, mid, src, dst, enc)[3:]

    assert payload[0:16] == gid
    assert payload[16:32] == mid
    assert payload[32:38] == src
    assert payload[38:44] == dst
    struct.unpack("!I", payload[44:48])  # timestamp parses as big-endian uint32
    assert payload[48:] == enc
    # 48 metadata bytes + 24 nonce = the 72-byte pre-ciphertext header in the spec.
    assert len(payload) == 48 + len(enc)


def test_message_round_trips():
    gid, mid = b"\x00" * 16, b"\x99" * 16
    src, dst = b"\x01" * 6, b"\x02" * 6
    enc = b"payload"
    decoded = protocol.decode_message(protocol.encode_message(gid, mid, src, dst, enc)[3:])
    assert decoded[0] == gid
    assert decoded[1] == mid
    assert decoded[2] == src
    assert decoded[3] == dst
    assert isinstance(decoded[4], int) and decoded[4] > 1_600_000_000
    assert decoded[5] == enc


def test_message_timestamp_is_unsigned_and_survives_2038():
    # A signed 32-bit read would come back negative for timestamps past 2038.
    payload = b"\x00" * 44 + struct.pack("!I", 0xFFFFFFFF) + b"x"
    assert protocol.decode_message(payload)[4] == 0xFFFFFFFF


# --- ACK / READ ---


@pytest.mark.parametrize(
    "encode,decode,frame_type",
    [
        (protocol.encode_ack, protocol.decode_ack, protocol.TYPE_ACK),
        (protocol.encode_read, protocol.decode_read, protocol.TYPE_READ),
    ],
)
def test_ack_and_read_share_a_22_byte_shape(encode, decode, frame_type):
    mid, mac = b"\xab" * 16, b"\xcd" * 6
    frame = encode(mid, mac)
    assert frame[0] == frame_type
    assert struct.unpack("!H", frame[1:3])[0] == 22
    assert decode(frame[3:]) == (mid, mac)


# --- Group setup ---


def test_group_setup_round_trips():
    gid = b"\x77" * 16
    members = [(b"\x01" * 6, b"\xa1" * 32), (b"\x02" * 6, b"\xa2" * 32)]
    frame = protocol.encode_group_setup(gid, members, "Sky Team")
    got_gid, got_members, got_name = protocol.decode_group_setup(frame[3:])
    assert (got_gid, got_members, got_name) == (gid, members, "Sky Team")


def test_group_setup_with_no_members_round_trips():
    gid = b"\x00" * 16
    frame = protocol.encode_group_setup(gid, [], "")
    assert protocol.decode_group_setup(frame[3:]) == (gid, [], "")


def test_group_setup_name_length_is_uint16_big_endian():
    frame = protocol.encode_group_setup(b"\x00" * 16, [], "ab")
    payload = frame[3:]
    assert payload[16] == 0  # member_count
    assert payload[17:19] == b"\x00\x02"  # name_length


def test_group_setup_six_members_stays_under_the_spec_size():
    members = [(bytes([i]) * 6, bytes([i]) * 32) for i in range(6)]
    frame = protocol.encode_group_setup(b"\x00" * 16, members, "Flight")
    assert len(frame) - 3 <= 260  # spec says ~247 for 6 members


# --- Profile ---


def test_profile_is_a_bare_utf8_payload():
    frame = protocol.encode_profile("Ravn")
    assert frame == b"\x06\x00\x04Ravn"
    assert protocol.decode_profile(frame[3:]) == "Ravn"


def test_profile_handles_multibyte_names():
    frame = protocol.encode_profile("Hugin🐦")
    assert protocol.decode_profile(frame[3:]) == "Hugin🐦"


def test_empty_profile_means_no_self_chosen_name():
    assert protocol.decode_profile(protocol.encode_profile("")[3:]) == ""


# --- Peer announcement ---


def test_peer_annc_round_trips():
    peers = [
        (b"\x01" * 6, b"\xa1" * 32, "alice"),
        (b"\x02" * 6, b"\xa2" * 32, ""),
    ]
    frame = protocol.encode_peer_annc(peers)
    assert protocol.decode_peer_annc(frame[3:]) == peers


def test_peer_annc_entry_layout():
    frame = protocol.encode_peer_annc([(b"\x01" * 6, b"\xa1" * 32, "ab")])
    payload = frame[3:]
    assert payload[0] == 1  # peer_count
    assert payload[1:7] == b"\x01" * 6
    assert payload[7:39] == b"\xa1" * 32
    assert payload[39] == 2  # name_length (uint8)
    assert payload[40:42] == b"ab"
    assert len(payload) == 1 + 39 + 2


def test_empty_peer_annc_is_a_legal_noop():
    assert protocol.decode_peer_annc(protocol.encode_peer_annc([])[3:]) == []


def test_peer_annc_name_is_truncated_on_a_utf8_boundary():
    # name_length is a uint8, so long names must be cut — and the cut must not
    # split a multi-byte codepoint, or the receiver decodes mojibake.
    long_name = "é" * 200  # 400 bytes
    frame = protocol.encode_peer_annc([(b"\x01" * 6, b"\xa1" * 32, long_name)])
    (_mac, _pub, name), = protocol.decode_peer_annc(frame[3:])
    assert "�" not in name, "truncation split a UTF-8 sequence"
    assert long_name.startswith(name)


def test_peer_annc_max_peers_encodes():
    peers = [(bytes([i % 256]) * 6, bytes([i % 256]) * 32, "") for i in range(255)]
    frame = protocol.encode_peer_annc(peers)
    assert frame[3] == 255
    assert protocol.decode_peer_annc(frame[3:]) == peers


# --- MAC helpers ---


def test_mac_round_trips_uppercase():
    assert protocol.bytes_to_mac(protocol.mac_to_bytes("aa:bb:cc:dd:ee:ff")) == (
        "AA:BB:CC:DD:EE:FF"
    )


def test_mac_to_bytes_is_msb_first():
    assert protocol.mac_to_bytes("00:11:22:33:44:55") == b"\x00\x11\x22\x33\x44\x55"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "AA:BB:CC:DD:EE",  # too short
        "AA:BB:CC:DD:EE:FF:00",  # too long
        "AA-BB-CC-DD-EE-FF",  # wrong separator
        "ZZ:BB:CC:DD:EE:FF",  # not hex
        "AA:BB:CC:DD:EE:1FF",  # octet out of range
        "02:00:00:00:00",  # the Android sentinel, mangled
    ],
)
def test_mac_to_bytes_rejects_malformed_input(bad):
    # A silently-wrong-length MAC corrupts every downstream frame offset, so
    # this must raise rather than return a short bytes object.
    with pytest.raises(ValueError):
        protocol.mac_to_bytes(bad)


def test_ids_are_16_bytes_and_unique():
    assert len(protocol.new_msg_id()) == 16
    assert len(protocol.new_group_id()) == 16
    assert len({protocol.new_msg_id() for _ in range(500)}) == 500
