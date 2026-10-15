package com.muninn.core

import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream
import java.nio.ByteBuffer

/** Muninn wire protocol v2. Mirrors python/src/muninn/protocol.py — see PROTOCOL.md. */
object P {
    const val VERSION = 2

    const val T_HELLO: Int = 0x01
    const val T_PING: Int = 0x02
    const val T_PONG: Int = 0x03
    const val T_ROUTES: Int = 0x04
    const val T_ROSTER: Int = 0x05
    const val T_PACKET: Int = 0x10
    const val T_ACK: Int = 0x11

    const val MAX_PAYLOAD = 0xFFFF
    const val DEFAULT_TTL = 8
    const val ROUTE_UNREACHABLE = 0xFF

    const val F_RELIABLE = 0x01
    const val F_BULK = 0x02
    const val F_REALTIME = 0x04

    const val PACKET_HEADER = 16 + 6 + 6 + 1 + 1 + 4 + 32
    const val MAX_BOX = MAX_PAYLOAD - PACKET_HEADER
    const val ACK_SIZE = 16 + 6 + 6 + 1

    class FrameTooLarge(msg: String) : IllegalArgumentException(msg)
    class Malformed(msg: String) : IllegalArgumentException(msg)

    fun frame(type: Int, payload: ByteArray): ByteArray {
        if (payload.size > MAX_PAYLOAD) throw FrameTooLarge("payload ${payload.size} > $MAX_PAYLOAD")
        val out = ByteArray(3 + payload.size)
        out[0] = type.toByte()
        out[1] = (payload.size ushr 8).toByte()
        out[2] = payload.size.toByte()
        System.arraycopy(payload, 0, out, 3, payload.size)
        return out
    }

    class Frame(val type: Int, val payload: ByteArray)

    fun readFrame(input: InputStream): Frame {
        val d = if (input is DataInputStream) input else DataInputStream(input)
        val t = d.read()
        if (t < 0) throw EOFException("closed")
        val len = d.readUnsignedShort()
        val p = ByteArray(len)
        d.readFully(p)
        return Frame(t, p)
    }

    // ---- ids ---------------------------------------------------------------

    fun idToBytes(id: String): ByteArray {
        val parts = id.split(":")
        if (parts.size != 6) throw IllegalArgumentException("bad node id $id")
        return ByteArray(6) { parts[it].toInt(16).toByte() }
    }

    fun bytesToId(b: ByteArray, off: Int = 0): String =
        (0 until 6).joinToString(":") { "%02X".format(b[off + it].toInt() and 0xff) }

    fun randomNodeId(): String {
        val b = Nacl.randomBytes(6)
        b[0] = ((b[0].toInt() and 0xFC) or 0x02).toByte()
        return bytesToId(b)
    }

    // ---- HELLO ---------------------------------------------------------------

    data class Hello(
        val pubkey: ByteArray, val nodeId: String, val version: Int,
        val features: Int, val name: String, val nameTs: Long,
    )

    fun encodeHello(pubkey: ByteArray, nodeId: String, name: String, nameTs: Long, features: Int = 0): ByteArray {
        val nb = utf8Cut(name)
        val bb = ByteBuffer.allocate(32 + 6 + 1 + 4 + 4 + 1 + nb.size)
        bb.put(pubkey).put(idToBytes(nodeId)).put(VERSION.toByte()).putInt(features)
            .putInt(nameTs.toInt()).put(nb.size.toByte()).put(nb)
        return frame(T_HELLO, bb.array())
    }

    fun decodeHello(p: ByteArray): Hello {
        if (p.size < 48) throw Malformed("hello too short")
        val bb = ByteBuffer.wrap(p, 38, 10)
        val version = bb.get().toInt() and 0xff
        val features = bb.int
        val nameTs = bb.int.toLong() and 0xffffffffL
        val nlen = bb.get().toInt() and 0xff
        val name = String(p, 48, minOf(nlen, p.size - 48), Charsets.UTF_8)
        return Hello(p.copyOfRange(0, 32), bytesToId(p, 32), version, features, name, nameTs)
    }

    // ---- ping / pong -----------------------------------------------------------

    fun encodePing(token: ByteArray) = frame(T_PING, token)
    fun encodePong(token: ByteArray) = frame(T_PONG, token)

    // ---- routes ----------------------------------------------------------------

    fun encodeRoutes(entries: List<Pair<String, Int>>): ByteArray {
        val e = entries.take(255)
        val bb = ByteBuffer.allocate(1 + e.size * 7)
        bb.put(e.size.toByte())
        for ((id, hops) in e) bb.put(idToBytes(id)).put(minOf(hops, ROUTE_UNREACHABLE).toByte())
        return frame(T_ROUTES, bb.array())
    }

    fun decodeRoutes(p: ByteArray): List<Pair<String, Int>> {
        if (p.isEmpty()) return emptyList()
        val n = p[0].toInt() and 0xff
        if (p.size < 1 + n * 7) throw Malformed("routes truncated")
        return (0 until n).map { bytesToId(p, 1 + it * 7) to (p[7 + it * 7].toInt() and 0xff) }
    }

    // ---- roster ----------------------------------------------------------------

    data class RosterEntry(val nodeId: String, val pubkey: ByteArray, val name: String, val nameTs: Long)

    fun encodeRoster(entries: List<RosterEntry>): List<ByteArray> =
        entries.chunked(200).map { batch ->
            val parts = batch.map { e ->
                val nb = utf8Cut(e.name)
                ByteBuffer.allocate(6 + 32 + 4 + 1 + nb.size)
                    .put(idToBytes(e.nodeId)).put(e.pubkey).putInt(e.nameTs.toInt())
                    .put(nb.size.toByte()).put(nb).array()
            }
            val bb = ByteBuffer.allocate(1 + parts.sumOf { it.size })
            bb.put(batch.size.toByte())
            parts.forEach { bb.put(it) }
            frame(T_ROSTER, bb.array())
        }

    fun decodeRoster(p: ByteArray): List<RosterEntry> {
        if (p.isEmpty()) return emptyList()
        val n = p[0].toInt() and 0xff
        var off = 1
        val out = ArrayList<RosterEntry>()
        repeat(n) {
            if (p.size < off + 43) throw Malformed("roster truncated")
            val id = bytesToId(p, off)
            val pk = p.copyOfRange(off + 6, off + 38)
            val ts = ByteBuffer.wrap(p, off + 38, 4).int.toLong() and 0xffffffffL
            val nlen = p[off + 42].toInt() and 0xff
            val name = String(p, off + 43, minOf(nlen, p.size - off - 43), Charsets.UTF_8)
            out.add(RosterEntry(id, pk, name, ts))
            off += 43 + nlen
        }
        return out
    }

    // ---- packet ------------------------------------------------------------------

    class Packet(
        val packetId: ByteArray, val src: String, val dst: String, val ttl: Int,
        val flags: Int, val ts: Long, val srcPubkey: ByteArray, val box: ByteArray,
    ) {
        val reliable get() = flags and F_RELIABLE != 0
    }

    fun encodePacket(p: Packet): ByteArray {
        val bb = ByteBuffer.allocate(PACKET_HEADER + p.box.size)
        bb.put(p.packetId).put(idToBytes(p.src)).put(idToBytes(p.dst)).put(p.ttl.toByte())
            .put(p.flags.toByte()).putInt(p.ts.toInt()).put(p.srcPubkey).put(p.box)
        return frame(T_PACKET, bb.array())
    }

    fun decodePacket(p: ByteArray): Packet {
        if (p.size < PACKET_HEADER + 40) throw Malformed("packet too short")
        return Packet(
            p.copyOfRange(0, 16), bytesToId(p, 16), bytesToId(p, 22),
            p[28].toInt() and 0xff, p[29].toInt() and 0xff,
            ByteBuffer.wrap(p, 30, 4).int.toLong() and 0xffffffffL,
            p.copyOfRange(34, 66), p.copyOfRange(PACKET_HEADER, p.size),
        )
    }

    fun packetWithTtl(raw: ByteArray, ttl: Int): ByteArray {
        val c = raw.copyOf()
        c[28] = ttl.toByte()
        return frame(T_PACKET, c)
    }

    // ---- ack -------------------------------------------------------------------------

    class Ack(val packetId: ByteArray, val src: String, val dst: String, val ttl: Int)

    fun encodeAck(a: Ack): ByteArray {
        val bb = ByteBuffer.allocate(ACK_SIZE)
        bb.put(a.packetId).put(idToBytes(a.src)).put(idToBytes(a.dst)).put(a.ttl.toByte())
        return frame(T_ACK, bb.array())
    }

    fun decodeAck(p: ByteArray): Ack {
        if (p.size < ACK_SIZE) throw Malformed("ack too short")
        return Ack(p.copyOfRange(0, 16), bytesToId(p, 16), bytesToId(p, 22), p[28].toInt() and 0xff)
    }

    fun now(): Long = System.currentTimeMillis() / 1000

    /** UTF-8 bytes cut to `limit` without splitting a codepoint. */
    fun utf8Cut(text: String, limit: Int = 255): ByteArray {
        val b = text.toByteArray(Charsets.UTF_8)
        if (b.size <= limit) return b
        var i = limit - 1
        while (i > 0 && (b[i].toInt() and 0xC0) == 0x80) i-- // start of last char
        val lead = b[i].toInt() and 0xff
        val len = when { lead < 0x80 -> 1; lead >= 0xF0 -> 4; lead >= 0xE0 -> 3; else -> 2 }
        return b.copyOf(if (i + len <= limit) i + len else i)
    }
}

/** Encrypted packet bodies. Mirrors python/src/muninn/content.py. */
object Body {
    const val TEXT = 1
    const val REACT = 2
    const val EDIT = 3
    const val DELETE = 4
    const val FILE = 5
    const val CHUNK = 6
    const val FILE_REQ = 7
    const val GROUP = 8
    const val RECEIPT = 9
    const val APP = 10
    const val RT = 11
    const val TYPING = 12
    const val GROUP_REQ = 13
    const val REPLACED = 14

    val RELIABLE = setOf(TEXT, REACT, EDIT, DELETE, FILE, GROUP, RECEIPT, APP, REPLACED)

    class B(val kind: Int, val meta: Map<String, Any?> = emptyMap(), val blob: ByteArray = ByteArray(0)) {
        val reliable get() = kind in RELIABLE
    }

    fun encode(b: B): ByteArray {
        val meta = Json.write(b.meta).toByteArray()
        if (meta.size > 0xFFFF) throw IllegalArgumentException("meta too large")
        val bb = ByteBuffer.allocate(3 + meta.size + b.blob.size)
        bb.put(b.kind.toByte()).putShort(meta.size.toShort()).put(meta).put(b.blob)
        return bb.array()
    }

    fun decode(d: ByteArray): B {
        if (d.size < 3) throw P.Malformed("body too short")
        val kind = d[0].toInt() and 0xff
        val mlen = ((d[1].toInt() and 0xff) shl 8) or (d[2].toInt() and 0xff)
        if (d.size < 3 + mlen) throw P.Malformed("body meta truncated")
        val meta = if (mlen > 0) Json.obj(String(d, 3, mlen, Charsets.UTF_8)) else emptyMap()
        return B(kind, meta, d.copyOfRange(3 + mlen, d.size))
    }
}
