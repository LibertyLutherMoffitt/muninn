package com.muninn.core

import java.security.SecureRandom

/**
 * NaCl `crypto_box` (X25519 + XSalsa20-Poly1305) in pure Kotlin — a direct
 * port of TweetNaCl. Wire-compatible with PyNaCl's `Box.encrypt()` output:
 * `nonce(24) || mac(16) || ciphertext`.
 *
 * Pure Kotlin so the exact same code runs on Android and on the JVM sim node,
 * with no native libraries.
 */
object Nacl {
    private val rng = SecureRandom()
    private val SIGMA = "expand 32-byte k".toByteArray(Charsets.US_ASCII)
    private val ZERO16 = ByteArray(16)
    private val NINE = ByteArray(32).also { it[0] = 9 }
    private val GF_121665 = longArrayOf(0xDB41, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    const val NONCE = 24
    const val MAC = 16

    fun randomBytes(n: Int): ByteArray = ByteArray(n).also(rng::nextBytes)

    // ---- key handling ---------------------------------------------------

    fun publicKey(secret: ByteArray): ByteArray {
        require(secret.size == 32)
        return scalarMult(secret, NINE)
    }

    fun newSecretKey(): ByteArray = randomBytes(32)

    /** Shared key for (my secret, their public): HSalsa20(X25519(...)). */
    fun beforeNm(theirPublic: ByteArray, mySecret: ByteArray): ByteArray {
        val s = scalarMult(mySecret, theirPublic)
        val k = ByteArray(32)
        core(k, ZERO16, s, SIGMA, true)
        return k
    }

    /** nonce || mac || ciphertext */
    fun seal(plain: ByteArray, sharedKey: ByteArray, nonce: ByteArray = randomBytes(NONCE)): ByteArray {
        val m = ByteArray(32 + plain.size)
        System.arraycopy(plain, 0, m, 32, plain.size)
        val c = ByteArray(m.size)
        streamXsalsa20Xor(c, m, m.size, nonce, sharedKey)
        val tag = ByteArray(16)
        oneTimeAuth(tag, c, 32, c.size - 32, c)
        val out = ByteArray(NONCE + MAC + plain.size)
        System.arraycopy(nonce, 0, out, 0, NONCE)
        System.arraycopy(tag, 0, out, NONCE, MAC)
        System.arraycopy(c, 32, out, NONCE + MAC, plain.size)
        return out
    }

    /** Throws SecurityException on authentication failure. */
    fun open(box: ByteArray, sharedKey: ByteArray): ByteArray {
        if (box.size < NONCE + MAC) throw SecurityException("box too short")
        val nonce = box.copyOfRange(0, NONCE)
        val n = box.size - NONCE - MAC
        val c = ByteArray(32 + n)
        System.arraycopy(box, NONCE, c, 16, MAC)
        System.arraycopy(box, NONCE + MAC, c, 32, n)
        val x = ByteArray(32)
        streamXsalsa20Xor(x, null, 32, nonce, sharedKey)
        val tag = ByteArray(16)
        oneTimeAuth(tag, c, 32, n, x)
        var d = 0
        for (i in 0 until 16) d = d or (tag[i].toInt() xor c[16 + i].toInt())
        if (d != 0) throw SecurityException("bad MAC")
        val m = ByteArray(c.size)
        streamXsalsa20Xor(m, c, c.size, nonce, sharedKey)
        return m.copyOfRange(32, m.size)
    }

    // ---- salsa20 ---------------------------------------------------------

    private fun ld32(x: ByteArray, o: Int): Int =
        (x[o].toInt() and 0xff) or ((x[o + 1].toInt() and 0xff) shl 8) or
            ((x[o + 2].toInt() and 0xff) shl 16) or ((x[o + 3].toInt() and 0xff) shl 24)

    private fun st32(x: ByteArray, o: Int, u: Int) {
        x[o] = u.toByte(); x[o + 1] = (u ushr 8).toByte()
        x[o + 2] = (u ushr 16).toByte(); x[o + 3] = (u ushr 24).toByte()
    }

    private fun rotl(x: Int, c: Int) = (x shl c) or (x ushr (32 - c))

    private fun core(out: ByteArray, inp: ByteArray, k: ByteArray, c: ByteArray, h: Boolean) {
        val w = IntArray(16); val x = IntArray(16); val y = IntArray(16); val t = IntArray(4)
        for (i in 0 until 4) {
            x[5 * i] = ld32(c, 4 * i)
            x[1 + i] = ld32(k, 4 * i)
            x[6 + i] = ld32(inp, 4 * i)
            x[11 + i] = ld32(k, 16 + 4 * i)
        }
        for (i in 0 until 16) y[i] = x[i]
        for (i in 0 until 20) {
            for (j in 0 until 4) {
                for (m in 0 until 4) t[m] = x[(5 * j + 4 * m) % 16]
                t[1] = t[1] xor rotl(t[0] + t[3], 7)
                t[2] = t[2] xor rotl(t[1] + t[0], 9)
                t[3] = t[3] xor rotl(t[2] + t[1], 13)
                t[0] = t[0] xor rotl(t[3] + t[2], 18)
                for (m in 0 until 4) w[4 * j + (j + m) % 4] = t[m]
            }
            for (m in 0 until 16) x[m] = w[m]
        }
        if (h) {
            for (i in 0 until 16) x[i] += y[i]
            for (i in 0 until 4) {
                x[5 * i] -= ld32(c, 4 * i)
                x[6 + i] -= ld32(inp, 4 * i)
            }
            for (i in 0 until 4) {
                st32(out, 4 * i, x[5 * i])
                st32(out, 16 + 4 * i, x[6 + i])
            }
        } else {
            for (i in 0 until 16) st32(out, 4 * i, x[i] + y[i])
        }
    }

    private fun streamSalsa20Xor(c: ByteArray, m: ByteArray?, len: Int, n: ByteArray, nOff: Int, k: ByteArray) {
        val z = ByteArray(16)
        val x = ByteArray(64)
        for (i in 0 until 8) z[i] = n[nOff + i]
        var b = len
        var off = 0
        while (b >= 64) {
            core(x, z, k, SIGMA, false)
            for (i in 0 until 64) c[off + i] = ((m?.get(off + i)?.toInt() ?: 0) xor x[i].toInt()).toByte()
            var u = 1
            for (i in 8 until 16) {
                u += z[i].toInt() and 0xff
                z[i] = u.toByte()
                u = u ushr 8
            }
            b -= 64
            off += 64
        }
        if (b > 0) {
            core(x, z, k, SIGMA, false)
            for (i in 0 until b) c[off + i] = ((m?.get(off + i)?.toInt() ?: 0) xor x[i].toInt()).toByte()
        }
    }

    private fun streamXsalsa20Xor(c: ByteArray, m: ByteArray?, len: Int, n: ByteArray, k: ByteArray) {
        val s = ByteArray(32)
        core(s, n, k, SIGMA, true)
        streamSalsa20Xor(c, m, len, n, 16, s)
    }

    // ---- poly1305 --------------------------------------------------------

    private val MINUSP = longArrayOf(5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 252)

    private fun add1305(h: LongArray, c: LongArray) {
        var u = 0L
        for (j in 0 until 17) {
            u += h[j] + c[j]
            h[j] = u and 255
            u = u ushr 8
        }
    }

    private fun oneTimeAuth(out: ByteArray, m: ByteArray, mOff: Int, len: Int, k: ByteArray) {
        val x = LongArray(17); val r = LongArray(17); val h = LongArray(17)
        val c = LongArray(17); val g = LongArray(17)
        for (j in 0 until 16) r[j] = (k[j].toLong() and 0xff)
        r[3] = r[3] and 15; r[4] = r[4] and 252; r[7] = r[7] and 15; r[8] = r[8] and 252
        r[11] = r[11] and 15; r[12] = r[12] and 252; r[15] = r[15] and 15
        var n = len
        var p = mOff
        while (n > 0) {
            java.util.Arrays.fill(c, 0L)
            var j = 0
            while (j < 16 && j < n) { c[j] = m[p + j].toLong() and 0xff; j++ }
            c[j] = 1
            p += j; n -= j
            add1305(h, c)
            for (i in 0 until 17) {
                x[i] = 0
                for (jj in 0 until 17) x[i] += h[jj] * (if (jj <= i) r[i - jj] else 320 * r[i + 17 - jj])
            }
            for (i in 0 until 17) h[i] = x[i]
            var u = 0L
            for (jj in 0 until 16) { u += h[jj]; h[jj] = u and 255; u = u ushr 8 }
            u += h[16]; h[16] = u and 3
            u = 5 * (u ushr 2)
            for (jj in 0 until 16) { u += h[jj]; h[jj] = u and 255; u = u ushr 8 }
            u += h[16]; h[16] = u
        }
        for (j in 0 until 17) g[j] = h[j]
        add1305(h, MINUSP)
        val s = if ((h[16] ushr 7) and 1L == 1L) -1L else 0L
        for (j in 0 until 17) h[j] = h[j] xor (s and (g[j] xor h[j]))
        for (j in 0 until 16) c[j] = k[j + 16].toLong() and 0xff
        c[16] = 0
        add1305(h, c)
        for (j in 0 until 16) out[j] = h[j].toByte()
    }

    // ---- curve25519 --------------------------------------------------------

    private fun car(o: LongArray) {
        for (i in 0 until 16) {
            o[i] += 1L shl 16
            val c = o[i] shr 16
            if (i < 15) o[i + 1] += c - 1 else o[0] += 38 * (c - 1)
            o[i] -= c shl 16
        }
    }

    private fun sel(p: LongArray, q: LongArray, b: Int) {
        val c = (b - 1).toLong().inv()
        for (i in 0 until 16) {
            val t = c and (p[i] xor q[i])
            p[i] = p[i] xor t
            q[i] = q[i] xor t
        }
    }

    private fun pack(o: ByteArray, n: LongArray) {
        val m = LongArray(16)
        val t = n.copyOf()
        car(t); car(t); car(t)
        for (j in 0 until 2) {
            m[0] = t[0] - 0xffed
            for (i in 1 until 15) {
                m[i] = t[i] - 0xffff - ((m[i - 1] shr 16) and 1)
                m[i - 1] = m[i - 1] and 0xffff
            }
            m[15] = t[15] - 0x7fff - ((m[14] shr 16) and 1)
            val b = ((m[15] shr 16) and 1).toInt()
            m[14] = m[14] and 0xffff
            sel(t, m, 1 - b)
        }
        for (i in 0 until 16) {
            o[2 * i] = (t[i] and 0xff).toByte()
            o[2 * i + 1] = (t[i] shr 8).toByte()
        }
    }

    private fun unpack(o: LongArray, n: ByteArray) {
        for (i in 0 until 16) o[i] = (n[2 * i].toLong() and 0xff) + ((n[2 * i + 1].toLong() and 0xff) shl 8)
        o[15] = o[15] and 0x7fff
    }

    private fun add(o: LongArray, a: LongArray, b: LongArray) { for (i in 0 until 16) o[i] = a[i] + b[i] }
    private fun sub(o: LongArray, a: LongArray, b: LongArray) { for (i in 0 until 16) o[i] = a[i] - b[i] }

    private fun mul(o: LongArray, a: LongArray, b: LongArray) {
        val t = LongArray(31)
        for (i in 0 until 16) for (j in 0 until 16) t[i + j] += a[i] * b[j]
        for (i in 0 until 15) t[i] += 38 * t[i + 16]
        for (i in 0 until 16) o[i] = t[i]
        car(o); car(o)
    }

    private fun sq(o: LongArray, a: LongArray) = mul(o, a, a)

    private fun inv(o: LongArray, i: LongArray) {
        val c = i.copyOf()
        for (a in 253 downTo 0) {
            sq(c, c)
            if (a != 2 && a != 4) mul(c, c, i)
        }
        for (a in 0 until 16) o[a] = c[a]
    }

    fun scalarMult(n: ByteArray, p: ByteArray): ByteArray {
        val z = n.copyOf(32)
        z[31] = ((n[31].toInt() and 127) or 64).toByte()
        z[0] = (z[0].toInt() and 248).toByte()
        val x = LongArray(16)
        unpack(x, p)
        val a = LongArray(16); val b = x.copyOf(); val c = LongArray(16); val d = LongArray(16)
        val e = LongArray(16); val f = LongArray(16)
        a[0] = 1; d[0] = 1
        for (i in 254 downTo 0) {
            val r = ((z[i ushr 3].toInt() and 0xff) ushr (i and 7)) and 1
            sel(a, b, r); sel(c, d, r)
            add(e, a, c); sub(a, a, c); add(c, b, d); sub(b, b, d)
            sq(d, e); sq(f, a); mul(a, c, a); mul(c, b, e)
            add(e, a, c); sub(a, a, c); sq(b, a); sub(c, d, f)
            mul(a, c, GF_121665); add(a, a, d); mul(c, c, a); mul(a, d, f)
            mul(d, b, x); sq(b, e)
            sel(a, b, r); sel(c, d, r)
        }
        inv(c, c)
        mul(a, a, c)
        val q = ByteArray(32)
        pack(q, a)
        return q
    }
}
