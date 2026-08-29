"""Conformance against spec/wire-vectors.json.

The same fixture is decoded by the Kotlin client's WireVectorsTest. If a
change makes one side fail, the two clients no longer speak the same
protocol — which is exactly the failure these vectors exist to catch.
"""

import json
import pathlib

import pytest
from nacl.public import Box, PrivateKey, PublicKey

from muninn import crypto, protocol

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "spec" / "wire-vectors.json"
)


@pytest.fixture(scope="module")
def vectors():
    assert VECTORS_PATH.exists(), (
        f"{VECTORS_PATH} is missing — run python3 spec/generate_vectors.py"
    )
    return json.loads(VECTORS_PATH.read_text())


def frame(vectors, key: str) -> bytes:
    return bytes.fromhex(vectors["frames"][key]["frame"])


def payload(vectors, key: str) -> bytes:
    return frame(vectors, key)[3:]


# --- Constants ---


def test_frame_type_numbers_match(vectors):
    assert vectors["frame_types"] == {
        "handshake": protocol.TYPE_HANDSHAKE,
        "message": protocol.TYPE_MESSAGE,
        "ack": protocol.TYPE_ACK,
        "group_setup": protocol.TYPE_GROUP_SETUP,
        "read": protocol.TYPE_READ,
        "profile": protocol.TYPE_PROFILE,
        "peer_annc": protocol.TYPE_PEER_ANNC,
    }


def test_every_vector_frame_has_a_well_formed_header(vectors):
    import struct

    for name, spec in vectors["frames"].items():
        raw = bytes.fromhex(spec["frame"])
        declared = struct.unpack("!H", raw[1:3])[0]
        assert declared == len(raw) - 3, f"{name}: header length disagrees with payload"
        assert 1 <= raw[0] <= 7, f"{name}: unknown frame type {raw[0]}"


# --- Crypto interop ---


def test_the_sealed_vector_opens_with_the_documented_keys(vectors):
    c = vectors["crypto"]
    alice = PrivateKey(bytes.fromhex(c["alice_secret"]))
    bob = PrivateKey(bytes.fromhex(c["bob_secret"]))
    assert bytes(alice.public_key) == bytes.fromhex(c["alice_public"])
    assert bytes(bob.public_key) == bytes.fromhex(c["bob_public"])

    box = crypto.derive_box(bob, bytes(alice.public_key))
    opened = crypto.decrypt(box, bytes.fromhex(c["sealed"]))
    assert opened.decode("utf-8") == c["plaintext_utf8"]


def test_sealing_with_the_documented_nonce_reproduces_the_vector(vectors):
    c = vectors["crypto"]
    alice = PrivateKey(bytes.fromhex(c["alice_secret"]))
    box = Box(alice, PublicKey(bytes.fromhex(c["bob_public"])))
    sealed = bytes(
        box.encrypt(c["plaintext_utf8"].encode("utf-8"), bytes.fromhex(c["nonce"]))
    )
    assert sealed.hex() == c["sealed"]


def test_the_sealed_blob_is_nonce_then_ciphertext(vectors):
    c = vectors["crypto"]
    assert bytes.fromhex(c["sealed"])[:24] == bytes.fromhex(c["nonce"])


# --- Frames ---


def test_handshake_vector(vectors):
    spec = vectors["frames"]["handshake"]
    pubkey, wire_id = protocol.decode_handshake(payload(vectors, "handshake"))
    assert pubkey.hex() == spec["pubkey"]
    assert protocol.bytes_to_mac(wire_id) == spec["wire_id"]
    assert protocol.encode_handshake(
        pubkey, protocol.mac_to_bytes(spec["wire_id"])
    ) == frame(vectors, "handshake")


def test_legacy_handshake_vector(vectors):
    pubkey, wire_id = protocol.decode_handshake(payload(vectors, "handshake_legacy"))
    assert pubkey.hex() == vectors["frames"]["handshake_legacy"]["pubkey"]
    assert wire_id is None


@pytest.mark.parametrize("key", ["message", "message_dm"])
def test_message_vector(vectors, key):
    spec = vectors["frames"][key]
    gid, mid, sender, dest, ts, enc = protocol.decode_message(payload(vectors, key))
    assert gid.hex() == spec["group_id"]
    assert mid.hex() == spec["msg_id"]
    assert protocol.bytes_to_mac(sender) == spec["sender"]
    assert protocol.bytes_to_mac(dest) == spec["dest"]
    assert ts == spec["timestamp"]
    assert enc.hex() == spec["encrypted"]
    assert protocol.encode_message(
        gid, mid, sender, dest, enc, timestamp=ts
    ) == frame(vectors, key)


def test_a_dm_uses_the_zero_group_id(vectors):
    gid, *_ = protocol.decode_message(payload(vectors, "message_dm"))
    assert gid == protocol.GROUP_ZERO_ID


@pytest.mark.parametrize(
    "key,decode,encode",
    [
        ("ack", protocol.decode_ack, protocol.encode_ack),
        ("read", protocol.decode_read, protocol.encode_read),
    ],
)
def test_ack_and_read_vectors(vectors, key, decode, encode):
    spec = vectors["frames"][key]
    msg_id, from_mac = decode(payload(vectors, key))
    assert msg_id.hex() == spec["msg_id"]
    assert protocol.bytes_to_mac(from_mac) == spec["from"]
    assert encode(msg_id, from_mac) == frame(vectors, key)


@pytest.mark.parametrize("key", ["profile", "profile_unicode", "profile_empty"])
def test_profile_vectors(vectors, key):
    spec = vectors["frames"][key]
    assert protocol.decode_profile(payload(vectors, key)) == spec["name"]
    assert protocol.encode_profile(spec["name"]) == frame(vectors, key)


@pytest.mark.parametrize("key", ["group_setup", "group_setup_empty"])
def test_group_setup_vectors(vectors, key):
    spec = vectors["frames"][key]
    gid, members, name = protocol.decode_group_setup(payload(vectors, key))
    assert gid.hex() == spec["group_id"]
    assert name == spec["name"]
    assert [
        {"mac": protocol.bytes_to_mac(m), "pubkey": pk.hex()} for m, pk in members
    ] == spec["members"]
    assert protocol.encode_group_setup(gid, members, name) == frame(vectors, key)


@pytest.mark.parametrize("key", ["peer_annc", "peer_annc_empty"])
def test_peer_annc_vectors(vectors, key):
    spec = vectors["frames"][key]
    peers = protocol.decode_peer_annc(payload(vectors, key))
    assert [
        {"mac": protocol.bytes_to_mac(m), "pubkey": pk.hex(), "name": n}
        for m, pk, n in peers
    ] == spec["peers"]
    assert protocol.encode_peer_annc(peers) == frame(vectors, key)


def test_the_checked_in_vectors_are_current(vectors, tmp_path, monkeypatch):
    """Guards against hand-editing wire-vectors.json out of sync with the code."""
    import subprocess
    import sys

    script = VECTORS_PATH.parent / "generate_vectors.py"
    before = VECTORS_PATH.read_text()
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert VECTORS_PATH.read_text() == before, (
        "spec/wire-vectors.json is stale — re-run python3 spec/generate_vectors.py"
    )
