"""XEdDSA (Signal spec): Ed25519-compatible signatures made with the X25519
identity key every Muninn device already has, verified against the X25519
public key peers already pinned. Used for linked-device records, so no
second key has to be distributed or trusted.

Signatures are ordinary Ed25519 signatures under the Edwards form of the
X25519 public key (sign bit 0); verification is libsodium's crypto_sign.
Mirrored in Kotlin (android/core/.../Ed25519.kt) and cross-tested.
"""

from __future__ import annotations

import hashlib
import os

from nacl import bindings as b
from nacl.exceptions import BadSignatureError

P = 2**255 - 19


def _clamp(k: bytes) -> bytes:
    a = bytearray(k)
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    return bytes(a)


def _reduce(x: bytes) -> bytes:
    return b.crypto_core_ed25519_scalar_reduce(x)


def ed_public(x_pub: bytes) -> bytes:
    """Edwards public key (sign bit 0) for an X25519 public key u:
    y = (u - 1) / (u + 1) mod p."""
    u = int.from_bytes(x_pub, "little") & ((1 << 255) - 1)
    if u >= P or (u + 1) % P == 0:
        raise ValueError("bad X25519 public key")
    y = (u - 1) * pow(u + 1, P - 2, P) % P
    return y.to_bytes(32, "little")


def sign(x_priv: bytes, msg: bytes, random: bytes | None = None) -> bytes:
    k = _clamp(x_priv)
    e = b.crypto_scalarmult_ed25519_base_noclamp(k)
    a = _reduce(k + bytes(32))
    if e[31] & 0x80:
        a = b.crypto_core_ed25519_scalar_negate(a)
    pub = e[:31] + bytes([e[31] & 0x7F])
    z = random if random is not None else os.urandom(64)
    r = _reduce(hashlib.sha512(b"\xfe" + b"\xff" * 31 + a + msg + z).digest())
    big_r = b.crypto_scalarmult_ed25519_base_noclamp(r)
    h = _reduce(hashlib.sha512(big_r + pub + msg).digest())
    s = b.crypto_core_ed25519_scalar_add(r, b.crypto_core_ed25519_scalar_mul(h, a))
    return big_r + s


def verify(x_pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(sig) != 64 or sig[63] & 0xE0:
        return False
    try:
        b.crypto_sign_open(sig + msg, ed_public(x_pub))
        return True
    except (BadSignatureError, ValueError):
        return False
