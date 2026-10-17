package com.muninn.core

import java.security.MessageDigest

/**
 * XEdDSA (Signal spec) — Ed25519 signatures made with a device's X25519
 * identity key and verified against the X25519 public key peers pinned.
 * Mirror of python/src/muninn/xeddsa.py; Ed25519 group arithmetic ported
 * from TweetNaCl's crypto_sign (field ops shared with [Nacl]).
 */
object XEdDSA {
    private fun gf(vararg v: Long) = LongArray(16).also { v.copyInto(it) }
    private val GF0 = LongArray(16)
    private val GF1 = gf(1)
    private val D = gf(0x78a3, 0x1359, 0x4dca, 0x75eb, 0xd8ab, 0x4141, 0x0a4d, 0x0070, 0xe898, 0x7779, 0x4079, 0x8cc7, 0xfe73, 0x2b6f, 0x6cee, 0x5203)
    private val D2 = gf(0xf159, 0x26b2, 0x9b94, 0xebd6, 0xb156, 0x8283, 0x149a, 0x00e0, 0xd130, 0xeef3, 0x80f2, 0x198e, 0xfce7, 0x56df, 0xd9dc, 0x2406)
    private val X = gf(0xd51a, 0x8f25, 0x2d60, 0xc956, 0xa7b2, 0x9525, 0xc760, 0x692c, 0xdc5c, 0xfdd6, 0xe231, 0xc0a4, 0x53fe, 0xcd6e, 0x36d3, 0x2169)
    private val Y = gf(0x6658, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666)
    private val I = gf(0xa0b0, 0x4a0e, 0x1b27, 0xc4ee, 0xe478, 0xad2f, 0x1806, 0x2f43, 0xd7a7, 0x3dfb, 0x0099, 0x2b4d, 0xdf0b, 0x4fc1, 0x2480, 0x2b83)
    private val L = longArrayOf(0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58, 0xd6, 0x9c, 0xf7, 0xa2, 0xde, 0xf9, 0xde, 0x14,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x10)

    private fun sha512(vararg parts: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-512").apply { parts.forEach { update(it) } }.digest()

    private fun packed(a: LongArray) = ByteArray(32).also { Nacl.pack(it, a) }
    private fun neq(a: LongArray, b: LongArray) = !packed(a).contentEquals(packed(b))
    private fun par(a: LongArray) = packed(a)[0].toInt() and 1

    private fun pow2523(o: LongArray, i: LongArray) {
        val c = i.copyOf()
        for (a in 250 downTo 0) { Nacl.sq(c, c); if (a != 1) Nacl.mul(c, c, i) }
        c.copyInto(o)
    }

    private fun point() = Array(4) { LongArray(16) }

    private fun add(p: Array<LongArray>, q: Array<LongArray>) {
        val a = LongArray(16); val b = LongArray(16); val c = LongArray(16); val d = LongArray(16)
        val t = LongArray(16); val e = LongArray(16); val f = LongArray(16); val g = LongArray(16); val h = LongArray(16)
        Nacl.sub(a, p[1], p[0]); Nacl.sub(t, q[1], q[0]); Nacl.mul(a, a, t)
        Nacl.add(b, p[0], p[1]); Nacl.add(t, q[0], q[1]); Nacl.mul(b, b, t)
        Nacl.mul(c, p[3], q[3]); Nacl.mul(c, c, D2)
        Nacl.mul(d, p[2], q[2]); Nacl.add(d, d, d)
        Nacl.sub(e, b, a); Nacl.sub(f, d, c); Nacl.add(g, d, c); Nacl.add(h, b, a)
        Nacl.mul(p[0], e, f); Nacl.mul(p[1], h, g); Nacl.mul(p[2], g, f); Nacl.mul(p[3], e, h)
    }

    private fun cswap(p: Array<LongArray>, q: Array<LongArray>, b: Int) { for (i in 0 until 4) Nacl.sel(p[i], q[i], b) }

    private fun pack(p: Array<LongArray>): ByteArray {
        val tx = LongArray(16); val ty = LongArray(16); val zi = LongArray(16)
        Nacl.inv(zi, p[2]); Nacl.mul(tx, p[0], zi); Nacl.mul(ty, p[1], zi)
        val r = packed(ty)
        r[31] = (r[31].toInt() xor (par(tx) shl 7)).toByte()
        return r
    }

    private fun scalarmult(p: Array<LongArray>, q: Array<LongArray>, s: ByteArray) {
        GF0.copyInto(p[0]); GF1.copyInto(p[1]); GF1.copyInto(p[2]); GF0.copyInto(p[3])
        for (i in 255 downTo 0) {
            val b = ((s[i / 8].toInt() and 0xff) ushr (i and 7)) and 1
            cswap(p, q, b); add(q, p); add(p, p); cswap(p, q, b)
        }
    }

    private fun scalarbase(s: ByteArray): ByteArray {
        val p = point(); val q = point()
        X.copyInto(q[0]); Y.copyInto(q[1]); GF1.copyInto(q[2]); Nacl.mul(q[3], X, Y)
        scalarmult(p, q, s)
        return pack(p)
    }

    private fun modL(r: ByteArray, x: LongArray) {
        for (i in 63 downTo 32) {
            var carry = 0L
            var j = i - 32
            while (j < i - 12) {
                x[j] += carry - 16 * x[i] * L[j - (i - 32)]
                carry = (x[j] + 128) shr 8
                x[j] -= carry shl 8
                j++
            }
            x[j] += carry
            x[i] = 0
        }
        var carry = 0L
        for (j in 0 until 32) { x[j] += carry - (x[31] shr 4) * L[j]; carry = x[j] shr 8; x[j] = x[j] and 255 }
        for (j in 0 until 32) x[j] -= carry * L[j]
        for (i in 0 until 32) { x[i + 1] += x[i] shr 8; r[i] = (x[i] and 255).toByte() }
    }

    /** 64-byte little-endian number mod L. */
    private fun reduce(h: ByteArray): ByteArray {
        val x = LongArray(64) { h[it].toLong() and 0xff }
        return ByteArray(32).also { modL(it, x) }
    }

    /** (L - a) mod L for a reduced scalar. */
    private fun negate(a: ByteArray): ByteArray {
        if (a.all { it.toInt() == 0 }) return a.copyOf()
        val out = ByteArray(32)
        var borrow = 0L
        for (i in 0 until 32) {
            var v = L[i] - (a[i].toLong() and 0xff) - borrow
            borrow = if (v < 0) 1 else 0
            if (v < 0) v += 256
            out[i] = v.toByte()
        }
        return out
    }

    private fun lessThanL(s: ByteArray): Boolean {
        for (i in 31 downTo 0) {
            val v = s[i].toLong() and 0xff
            if (v < L[i]) return true
            if (v > L[i]) return false
        }
        return false
    }

    /** Edwards public key (sign bit 0) of an X25519 public key: y = (u-1)/(u+1). */
    fun edPublic(xPub: ByteArray): ByteArray {
        val u = LongArray(16); Nacl.unpack(u, xPub)
        val num = LongArray(16); val den = LongArray(16)
        Nacl.sub(num, u, GF1); Nacl.add(den, u, GF1)
        Nacl.inv(den, den); Nacl.mul(num, num, den)
        return packed(num)
    }

    fun sign(xPriv: ByteArray, msg: ByteArray, random: ByteArray = Nacl.randomBytes(64)): ByteArray {
        val k = xPriv.copyOf(32)
        k[0] = (k[0].toInt() and 248).toByte(); k[31] = ((k[31].toInt() and 127) or 64).toByte()
        val e = scalarbase(k)
        var a = reduce(k + ByteArray(32))
        if (e[31].toInt() and 0x80 != 0) a = negate(a)
        val pub = e.copyOf().also { it[31] = (it[31].toInt() and 0x7f).toByte() }
        val r = reduce(sha512(byteArrayOf(0xfe.toByte()) + ByteArray(31) { 0xff.toByte() }, a, msg, random))
        val bigR = scalarbase(r)
        val h = reduce(sha512(bigR, pub, msg))
        val x = LongArray(64)
        for (i in 0 until 32) x[i] = r[i].toLong() and 0xff
        for (i in 0 until 32) for (j in 0 until 32) x[i + j] += (h[i].toLong() and 0xff) * (a[j].toLong() and 0xff)
        val s = ByteArray(32).also { modL(it, x) }
        return bigR + s
    }

    private fun unpackneg(r: Array<LongArray>, p: ByteArray): Boolean {
        val t = LongArray(16); val chk = LongArray(16); val num = LongArray(16); val den = LongArray(16)
        val den2 = LongArray(16); val den4 = LongArray(16); val den6 = LongArray(16)
        GF1.copyInto(r[2]); Nacl.unpack(r[1], p)
        Nacl.sq(num, r[1]); Nacl.mul(den, num, D); Nacl.sub(num, num, r[2]); Nacl.add(den, r[2], den)
        Nacl.sq(den2, den); Nacl.sq(den4, den2); Nacl.mul(den6, den4, den2); Nacl.mul(t, den6, num); Nacl.mul(t, t, den)
        pow2523(t, t); Nacl.mul(t, t, num); Nacl.mul(t, t, den); Nacl.mul(t, t, den); Nacl.mul(r[0], t, den)
        Nacl.sq(chk, r[0]); Nacl.mul(chk, chk, den)
        if (neq(chk, num)) Nacl.mul(r[0], r[0], I)
        Nacl.sq(chk, r[0]); Nacl.mul(chk, chk, den)
        if (neq(chk, num)) return false
        if (par(r[0]) == ((p[31].toInt() and 0xff) ushr 7)) Nacl.sub(r[0], GF0, r[0])
        Nacl.mul(r[3], r[0], r[1])
        return true
    }

    fun verify(xPub: ByteArray, msg: ByteArray, sig: ByteArray): Boolean {
        if (sig.size != 64 || xPub.size != 32) return false
        val s = sig.copyOfRange(32, 64)
        if (!lessThanL(s)) return false
        val pk = edPublic(xPub)
        val q = point()
        if (!unpackneg(q, pk)) return false
        val h = reduce(sha512(sig.copyOfRange(0, 32), pk, msg))
        val p = point()
        scalarmult(p, q, h)
        val base = point()
        X.copyInto(base[0]); Y.copyInto(base[1]); GF1.copyInto(base[2]); Nacl.mul(base[3], X, Y)
        val sb = point()
        scalarmult(sb, base, s)
        add(p, sb)
        return pack(p).contentEquals(sig.copyOfRange(0, 32))
    }
}
