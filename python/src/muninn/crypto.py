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
