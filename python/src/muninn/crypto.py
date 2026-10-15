from nacl.public import Box, PrivateKey, PublicKey


def generate_keypair() -> PrivateKey:
    return PrivateKey.generate()


def privkey_from_bytes(data: bytes) -> PrivateKey:
    return PrivateKey(data)


def derive_box(private_key: PrivateKey, peer_pubkey_bytes: bytes) -> Box:
    return Box(private_key, PublicKey(peer_pubkey_bytes))


def encrypt(box: Box, plaintext: bytes) -> bytes:
    # Returns 24-byte nonce + ciphertext
    return bytes(box.encrypt(plaintext))


def decrypt(box: Box, data: bytes) -> bytes:
    # Expects 24-byte nonce prepended to ciphertext
    return box.decrypt(data)


class BoxCache:
    """Memoizes Box objects per peer pubkey (X25519 is not free)."""

    def __init__(self, private_key: PrivateKey):
        self._pk = private_key
        self._boxes: dict[bytes, Box] = {}

    def get(self, pubkey: bytes) -> Box:
        box = self._boxes.get(pubkey)
        if box is None:
            box = derive_box(self._pk, pubkey)
            self._boxes[pubkey] = box
        return box
