package com.muninn.node

import com.goterl.lazysodium.LazySodiumJava
import com.goterl.lazysodium.SodiumJava
import com.muninn.MeshLink
import com.muninn.NONCE_BYTES
import com.muninn.Sealer
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.security.SecureRandom

/**
 * The JVM stand-ins for what the app gets from Android: libsodium through
 * lazysodium-java instead of lazysodium-android (the same C library and the
 * same crypto_box construction), and a TCP socket instead of RFCOMM.
 */
object Sodium {
    val lib = LazySodiumJava(SodiumJava())
    private val rng = SecureRandom()

    /** A fresh X25519 keypair as (secret, public). */
    fun keypair(): Pair<ByteArray, ByteArray> {
        val secret = ByteArray(32).also(rng::nextBytes)
        val public = ByteArray(32)
        check(lib.cryptoScalarMultBase(public, secret)) { "crypto_scalarmult_base failed" }
        return secret to public
    }

    fun nonce(): ByteArray = ByteArray(NONCE_BYTES).also(rng::nextBytes)
}

class SodiumSealer(private val secret: ByteArray) : Sealer {
    override fun seal(plaintext: ByteArray, theirPub: ByteArray): ByteArray {
        val nonce = Sodium.nonce()
        val ct = ByteArray(plaintext.size + 16)
        check(Sodium.lib.cryptoBoxEasy(ct, plaintext, plaintext.size.toLong(), nonce, theirPub, secret)) {
            "crypto_box_easy failed"
        }
        return nonce + ct
    }

    override fun open(sealed: ByteArray, theirPub: ByteArray): ByteArray {
        if (sealed.size < NONCE_BYTES + 16) throw SecurityException("sealed body too short")
        val nonce = sealed.copyOfRange(0, NONCE_BYTES)
        val ct = sealed.copyOfRange(NONCE_BYTES, sealed.size)
        val pt = ByteArray(ct.size - 16)
        if (!Sodium.lib.cryptoBoxOpenEasy(pt, ct, ct.size.toLong(), nonce, theirPub, secret)) {
            throw SecurityException("crypto_box_open_easy failed")
        }
        return pt
    }
}

class TcpLink(val socket: Socket) : MeshLink {
    override val input: InputStream = socket.getInputStream()
    override val output: OutputStream = socket.getOutputStream()

    override fun close() {
        runCatching { socket.shutdownInput() }
        runCatching { socket.shutdownOutput() }
        runCatching { socket.close() }
    }
}
