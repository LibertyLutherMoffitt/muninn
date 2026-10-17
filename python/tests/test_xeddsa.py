"""XEdDSA: signatures with the X25519 identity key (linked-device records)."""

import json
from pathlib import Path

from nacl.public import PrivateKey

from muninn import xeddsa

VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "spec/xeddsa-vectors.json").read_text()
)


def test_vectors_are_stable():
    """Same bytes as the Kotlin core (XEdDSATest.kt reads the same file)."""
    for v in VECTORS:
        priv, msg = bytes.fromhex(v["priv"]), bytes.fromhex(v["msg"])
        assert bytes(PrivateKey(priv).public_key).hex() == v["pub"]
        assert xeddsa.ed_public(bytes.fromhex(v["pub"])).hex() == v["ed"]
        assert xeddsa.sign(priv, msg, bytes.fromhex(v["z"])).hex() == v["sig"]
        assert xeddsa.verify(bytes.fromhex(v["pub"]), msg, bytes.fromhex(v["sig"]))


def test_rejects_forgeries():
    k = PrivateKey.generate()
    pub = bytes(k.public_key)
    sig = xeddsa.sign(bytes(k), b"record")
    assert xeddsa.verify(pub, b"record", sig)
    assert not xeddsa.verify(pub, b"recorD", sig)
    assert not xeddsa.verify(bytes(PrivateKey.generate().public_key), b"record", sig)
    for i in (0, 31, 32, 63):
        bad = bytearray(sig)
        bad[i] ^= 1
        assert not xeddsa.verify(pub, b"record", bytes(bad))
    assert not xeddsa.verify(pub, b"record", sig[:63])
