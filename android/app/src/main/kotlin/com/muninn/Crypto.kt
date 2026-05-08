package com.muninn

import com.goterl.lazysodium.LazySodiumAndroid
import com.goterl.lazysodium.SodiumAndroid
import com.goterl.lazysodium.utils.Key
import com.goterl.lazysodium.utils.KeyPair
import java.security.SecureRandom

/**
 * Wire-compatible with PyNaCl `Box` on the desktop client:
 *   X25519 key exchange + XSalsa20-Poly1305 AEAD.
 * `encrypt` returns nonce ‖ ciphertext (24-byte nonce prepended) — matches
 * PyNaCl's `Box.encrypt()` default output and our `protocol.py` expectation.
 */
object Crypto {
    private val sodium = LazySodiumAndroid(SodiumAndroid())
    private val rng = SecureRandom()

    fun generateKeyPair(): KeyPair = sodium.cryptoBoxKeypair()

    fun keyPairFromSecret(secret: ByteArray): KeyPair {
        require(secret.size == 32) { "x25519 private key must be 32 bytes" }
        val pub = ByteArray(32)
        val priv = secret.copyOf()
        if (!sodium.cryptoScalarMultBase(pub, priv)) {
            throw IllegalStateException("crypto_scalarmult_base failed")
        }
        return KeyPair(Key.fromBytes(pub), Key.fromBytes(priv))
    }

    fun encrypt(plaintext: ByteArray, theirPub: ByteArray, mySecret: ByteArray): ByteArray {
        val nonce = ByteArray(NONCE_BYTES).also(rng::nextBytes)
        val ct = ByteArray(plaintext.size + 16) // Poly1305 tag is 16 bytes
        if (!sodium.cryptoBoxEasy(ct, plaintext, plaintext.size.toLong(), nonce, theirPub, mySecret)) {
            throw IllegalStateException("crypto_box_easy failed")
        }
        return nonce + ct
    }

    fun decrypt(noncePlusCt: ByteArray, theirPub: ByteArray, mySecret: ByteArray): ByteArray {
        require(noncePlusCt.size > NONCE_BYTES + 16) { "ciphertext too short" }
        val nonce = noncePlusCt.copyOfRange(0, NONCE_BYTES)
        val ct = noncePlusCt.copyOfRange(NONCE_BYTES, noncePlusCt.size)
        val pt = ByteArray(ct.size - 16)
        if (!sodium.cryptoBoxOpenEasy(pt, ct, ct.size.toLong(), nonce, theirPub, mySecret)) {
            throw SecurityException("crypto_box_open_easy failed (bad nonce, tampered ct, or wrong key)")
        }
        return pt
    }
}
