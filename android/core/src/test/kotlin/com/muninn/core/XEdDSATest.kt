package com.muninn.core

import java.io.File
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** XEdDSA byte-exact with python/src/muninn/xeddsa.py (spec/xeddsa-vectors.json). */
@Suppress("UNCHECKED_CAST")
class XEdDSATest {
    private val v = Json.parse(File("../../spec/xeddsa-vectors.json").readText()) as List<Map<String, Any?>>

    @Test fun vectors() = v.forEach {
        val priv = it.str("priv").unhex(); val pub = it.str("pub").unhex(); val msg = it.str("msg").unhex()
        assertEquals(it.str("pub"), Nacl.publicKey(priv).hex())
        assertEquals(it.str("ed"), XEdDSA.edPublic(pub).hex())
        assertEquals(it.str("sig"), XEdDSA.sign(priv, msg, it.str("z").unhex()).hex())
        assertTrue(XEdDSA.verify(pub, msg, it.str("sig").unhex()))
        assertFalse(XEdDSA.verify(pub, msg + 1, it.str("sig").unhex()))
    }

    @Test fun randomKeysRoundTripAndRejectForgeries() = repeat(40) { i ->
        val priv = Nacl.newSecretKey(); val pub = Nacl.publicKey(priv)
        val msg = ByteArray(i * 3) { (it * 7).toByte() }
        val sig = XEdDSA.sign(priv, msg)
        assertTrue(XEdDSA.verify(pub, msg, sig), "round trip $i")
        assertFalse(XEdDSA.verify(Nacl.publicKey(Nacl.newSecretKey()), msg, sig))
        val bad = sig.copyOf().also { it[5] = (it[5] + 1).toByte() }
        assertFalse(XEdDSA.verify(pub, msg, bad))
        val badS = sig.copyOf().also { it[40] = (it[40] + 1).toByte() }
        assertFalse(XEdDSA.verify(pub, msg, badS))
        val nonCanonical = sig.copyOf().also { it[63] = (it[63].toInt() or 0xf0).toByte() }
        assertFalse(XEdDSA.verify(pub, msg, nonCanonical))
        assertContentEquals(XEdDSA.edPublic(pub), XEdDSA.edPublic(pub))
    }
}
