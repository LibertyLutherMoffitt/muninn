"""Crypto tests.

The wire format assertions here (nonce prefix, tag length, key size) are the
contract the Kotlin client's lazysodium calls have to match.
"""

import pytest
from nacl.exceptions import CryptoError

from muninn import crypto


@pytest.fixture
def pair():
    a, b = crypto.generate_keypair(), crypto.generate_keypair()
    return a, b


def test_box_round_trips_between_two_parties(pair):
    a, b = pair
    a_to_b = crypto.derive_box(a, bytes(b.public_key))
    b_from_a = crypto.derive_box(b, bytes(a.public_key))
    ct = crypto.encrypt(a_to_b, b"hello flight")
    assert crypto.decrypt(b_from_a, ct) == b"hello flight"


def test_ciphertext_is_nonce_then_tag_then_body(pair):
    a, b = pair
    box = crypto.derive_box(a, bytes(b.public_key))
    ct = crypto.encrypt(box, b"x" * 10)
    # 24-byte nonce + 16-byte Poly1305 tag + plaintext — this exact layout is
    # what Kotlin's Crypto.encrypt (nonce + crypto_box_easy) produces.
    assert len(ct) == 24 + 16 + 10


def test_each_encryption_uses_a_fresh_nonce(pair):
    a, b = pair
    box = crypto.derive_box(a, bytes(b.public_key))
    nonces = {crypto.encrypt(box, b"same")[:24] for _ in range(200)}
    assert len(nonces) == 200


def test_ecdh_is_symmetric(pair):
    a, b = pair
    assert bytes(crypto.derive_box(a, bytes(b.public_key)).shared_key()) == bytes(
        crypto.derive_box(b, bytes(a.public_key)).shared_key()
    )


def test_static_keys_give_a_stable_shared_secret_across_reconnects(pair):
    # Muninn reuses one static keypair for the device's lifetime, so every
    # handshake must derive the same secret (see CLAUDE.md).
    a, b = pair
    first = bytes(crypto.derive_box(a, bytes(b.public_key)).shared_key())
    reloaded = crypto.privkey_from_bytes(bytes(a))
    second = bytes(crypto.derive_box(reloaded, bytes(b.public_key)).shared_key())
    assert first == second


def test_tampering_with_the_ciphertext_is_detected(pair):
    a, b = pair
    box_a = crypto.derive_box(a, bytes(b.public_key))
    box_b = crypto.derive_box(b, bytes(a.public_key))
    ct = bytearray(crypto.encrypt(box_a, b"transfer 100"))
    ct[-1] ^= 0x01
    with pytest.raises(CryptoError):
        crypto.decrypt(box_b, bytes(ct))


def test_tampering_with_the_nonce_is_detected(pair):
    a, b = pair
    box_a = crypto.derive_box(a, bytes(b.public_key))
    box_b = crypto.derive_box(b, bytes(a.public_key))
    ct = bytearray(crypto.encrypt(box_a, b"transfer 100"))
    ct[0] ^= 0x01
    with pytest.raises(CryptoError):
        crypto.decrypt(box_b, bytes(ct))


def test_a_third_party_key_cannot_decrypt(pair):
    a, b = pair
    eve = crypto.generate_keypair()
    ct = crypto.encrypt(crypto.derive_box(a, bytes(b.public_key)), b"secret")
    with pytest.raises(CryptoError):
        crypto.decrypt(crypto.derive_box(eve, bytes(a.public_key)), ct)


def test_private_key_serialises_to_32_bytes(pair):
    a, _ = pair
    assert len(bytes(a)) == 32
    assert len(bytes(a.public_key)) == 32
    assert bytes(crypto.privkey_from_bytes(bytes(a)).public_key) == bytes(a.public_key)


def test_empty_plaintext_round_trips(pair):
    a, b = pair
    ct = crypto.encrypt(crypto.derive_box(a, bytes(b.public_key)), b"")
    assert crypto.decrypt(crypto.derive_box(b, bytes(a.public_key)), ct) == b""


def test_truncated_ciphertext_is_rejected(pair):
    a, b = pair
    ct = crypto.encrypt(crypto.derive_box(a, bytes(b.public_key)), b"hello")
    with pytest.raises(Exception):
        crypto.decrypt(crypto.derive_box(b, bytes(a.public_key)), ct[:30])
