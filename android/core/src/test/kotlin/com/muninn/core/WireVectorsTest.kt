package com.muninn.core

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals

/** Byte-exact conformance with spec/wire-vectors.json (also checked by Python). */
@Suppress("UNCHECKED_CAST")
class WireVectorsTest {
    private val v = Json.obj(File("../../spec/wire-vectors.json").readText())
    private fun list(k: String) = v[k] as List<Map<String, Any?>>

    @Test fun hello() = list("hello").forEach {
        val f = P.encodeHello(it.str("pubkey").unhex(), it.str("node_id"), it.str("name"), it.long("name_ts"), it.int("features"))
        assertEquals(it.str("frame"), f.hex())
        val h = P.decodeHello(it.str("frame").unhex().copyOfRange(3, f.size))
        assertEquals(it.str("decoded_name"), h.name)
        assertEquals(it.long("name_ts"), h.nameTs)
    }

    @Test fun routesRosterPacketAck() {
        list("routes").forEach {
            val e = it.list("entries").map { x -> val l = x as List<Any?>; (l[0] as String) to (l[1] as Number).toInt() }
            assertEquals(it.str("frame"), P.encodeRoutes(e).hex())
            assertEquals(e, P.decodeRoutes(it.str("frame").unhex().let { b -> b.copyOfRange(3, b.size) }))
        }
        list("roster").forEach {
            val es = it.list("entries").map { x -> val l = x as List<Any?>
                P.RosterEntry(l[0] as String, (l[1] as String).unhex(), l[2] as String, (l[3] as Number).toLong()) }
            assertEquals(it.str("frame"), P.encodeRoster(es)[0].hex())
        }
        list("packet").forEach {
            val raw = it.str("frame").unhex()
            val p = P.decodePacket(raw.copyOfRange(3, raw.size))
            assertEquals(it.str("src"), p.src); assertEquals(it.str("dst"), p.dst)
            assertEquals(it.int("flags"), p.flags); assertEquals(it.long("ts"), p.ts)
            assertEquals(it.str("frame"), P.encodePacket(p).hex())
            assertEquals(it.str("ttl3_frame"), P.packetWithTtl(raw.copyOfRange(3, raw.size), 3).hex())
        }
        list("ack").forEach {
            val raw = it.str("frame").unhex()
            val a = P.decodeAck(raw.copyOfRange(3, raw.size))
            assertEquals(it.str("frame"), P.encodeAck(a).hex())
        }
    }

    @Test fun bodies() = list("body").forEach {
        val b = Body.B(it.int("kind"), it["meta"] as Map<String, Any?>, it.str("blob").unhex())
        assertEquals(it.str("bytes"), Body.encode(b).hex())
    }
}
