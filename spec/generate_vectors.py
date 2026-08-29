#!/usr/bin/env python3
"""Regenerate spec/wire-vectors.json — the cross-client conformance fixture.

Every Muninn client (Python desktop, Kotlin/Android, anything future) decodes
these frames and must agree byte-for-byte. Run this only when PROTOCOL.md
changes on purpose; a diff here is a wire-compatibility break.

    python3 spec/generate_vectors.py
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "python" / "src"))

from nacl.public import Box, PrivateKey, PublicKey  # noqa: E402

from muninn import protocol  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "wire-vectors.json"

# Fixed test keys — NOT secret, and never used by a real client. Deterministic
# so the encrypted vector below is reproducible across languages.
ALICE_SK = bytes(range(32))
BOB_SK = bytes(range(32, 64))
NONCE = bytes(range(100, 124))  # 24 bytes

MAC_A = "AA:BB:CC:DD:EE:FF"
MAC_B = "02:11:22:33:44:55"  # an Android-style locally-administered wire id
GROUP = bytes.fromhex("0123456789abcdef0123456789abcdef")
MSG = bytes.fromhex("fedcba9876543210fedcba9876543210")
PUB32 = bytes(range(32))


def h(b: bytes) -> str:
    return b.hex()


def main() -> None:
    alice, bob = PrivateKey(ALICE_SK), PrivateKey(BOB_SK)
    box = Box(alice, PublicKey(bytes(bob.public_key)))
    sealed = bytes(box.encrypt(b"hello flight", NONCE))  # nonce || ciphertext

    frames = {
        "handshake": {
            "note": "32-byte X25519 pubkey followed by the 6-byte wire id",
            "pubkey": h(PUB32),
            "wire_id": MAC_B,
            "frame": h(protocol.encode_handshake(PUB32, protocol.mac_to_bytes(MAC_B))),
        },
        "handshake_legacy": {
            "note": "pre-wire-id form; receiver falls back to the transport MAC",
            "pubkey": h(PUB32),
            "frame": h(protocol.encode_handshake(PUB32)),
        },
        "message": {
            "note": "metadata plaintext, body sealed; encrypted = nonce || ciphertext",
            "group_id": h(GROUP),
            "msg_id": h(MSG),
            "sender": MAC_A,
            "dest": MAC_B,
            "timestamp": 1700000000,
            "encrypted": h(sealed),
            "frame": h(
                protocol.encode_message(
                    GROUP,
                    MSG,
                    protocol.mac_to_bytes(MAC_A),
                    protocol.mac_to_bytes(MAC_B),
                    sealed,
                    timestamp=1700000000,
                )
            ),
        },
        "message_dm": {
            "note": "1:1 DM — group_id is 16 zero bytes",
            "group_id": h(protocol.GROUP_ZERO_ID),
            "msg_id": h(MSG),
            "sender": MAC_A,
            "dest": MAC_B,
            "timestamp": 4294967295,
            "encrypted": h(sealed),
            "frame": h(
                protocol.encode_message(
                    protocol.GROUP_ZERO_ID,
                    MSG,
                    protocol.mac_to_bytes(MAC_A),
                    protocol.mac_to_bytes(MAC_B),
                    sealed,
                    timestamp=0xFFFFFFFF,
                )
            ),
        },
        "ack": {
            "msg_id": h(MSG),
            "from": MAC_B,
            "frame": h(protocol.encode_ack(MSG, protocol.mac_to_bytes(MAC_B))),
        },
        "read": {
            "msg_id": h(MSG),
            "from": MAC_B,
            "frame": h(protocol.encode_read(MSG, protocol.mac_to_bytes(MAC_B))),
        },
        "profile": {
            "name": "Ravn",
            "frame": h(protocol.encode_profile("Ravn")),
        },
        "profile_unicode": {
            "note": "UTF-8, no length prefix — the header bounds the payload",
            "name": "Hugin 🐦 ﬁ",
            "frame": h(protocol.encode_profile("Hugin 🐦 ﬁ")),
        },
        "profile_empty": {
            "note": "legal; means 'no self-chosen name, fall back to MAC'",
            "name": "",
            "frame": h(protocol.encode_profile("")),
        },
        "group_setup": {
            "group_id": h(GROUP),
            "name": "Sky Team",
            "members": [
                {"mac": MAC_A, "pubkey": h(PUB32)},
                {"mac": MAC_B, "pubkey": h(bytes(range(32, 64)))},
            ],
            "frame": h(
                protocol.encode_group_setup(
                    GROUP,
                    [
                        (protocol.mac_to_bytes(MAC_A), PUB32),
                        (protocol.mac_to_bytes(MAC_B), bytes(range(32, 64))),
                    ],
                    "Sky Team",
                )
            ),
        },
        "group_setup_empty": {
            "group_id": h(GROUP),
            "name": "",
            "members": [],
            "frame": h(protocol.encode_group_setup(GROUP, [], "")),
        },
        "peer_annc": {
            "peers": [
                {"mac": MAC_A, "pubkey": h(PUB32), "name": "Ravn"},
                {"mac": MAC_B, "pubkey": h(bytes(range(32, 64))), "name": ""},
            ],
            "frame": h(
                protocol.encode_peer_annc(
                    [
                        (protocol.mac_to_bytes(MAC_A), PUB32, "Ravn"),
                        (protocol.mac_to_bytes(MAC_B), bytes(range(32, 64)), ""),
                    ]
                )
            ),
        },
        "peer_annc_empty": {
            "peers": [],
            "frame": h(protocol.encode_peer_annc([])),
        },
    }

    doc = {
        "_comment": (
            "Generated by spec/generate_vectors.py. Canonical wire encodings "
            "shared by every Muninn client. A diff here is a wire-compat break."
        ),
        "service_uuid": "320bcf9c-94fe-46f4-b9bf-83535cafcd55",
        "frame_types": {
            "handshake": 1,
            "message": 2,
            "ack": 3,
            "group_setup": 4,
            "read": 5,
            "profile": 6,
            "peer_annc": 7,
        },
        "crypto": {
            "note": (
                "X25519 + NaCl Box (XSalsa20-Poly1305). `sealed` is "
                "nonce||ciphertext, exactly what goes on the wire after the "
                "48-byte message metadata."
            ),
            "alice_secret": h(ALICE_SK),
            "alice_public": h(bytes(alice.public_key)),
            "bob_secret": h(BOB_SK),
            "bob_public": h(bytes(bob.public_key)),
            "nonce": h(NONCE),
            "plaintext_utf8": "hello flight",
            "sealed": h(sealed),
        },
        "frames": frames,
    }
    OUT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT} ({len(frames)} frame vectors)")


if __name__ == "__main__":
    main()
