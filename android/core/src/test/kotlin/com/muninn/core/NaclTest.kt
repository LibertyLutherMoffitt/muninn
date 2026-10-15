package com.muninn.core

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertFailsWith

class NaclTest {
    @Suppress("UNCHECKED_CAST")
    private val vectors = Json.parse(
        javaClass.getResource("/nacl_vectors.json")!!.readText(),
    ) as List<Map<String, Any?>>

    @Test
    fun matchesPyNacl() {
        for (v in vectors) {
            val aSk = v.str("a_sk").unhex()
            val bPk = v.str("b_pk").unhex()
            assertContentEquals(v.str("a_pk").unhex(), Nacl.publicKey(aSk))
            val k = Nacl.beforeNm(bPk, aSk)
            val ct = Nacl.seal(v.str("msg").unhex(), k, v.str("nonce").unhex())
            assertContentEquals(v.str("ct").unhex(), ct)
            // and the receiver can open it with the mirrored key
            val k2 = Nacl.beforeNm(v.str("a_pk").unhex(), v.str("b_sk").unhex())
            assertContentEquals(v.str("msg").unhex(), Nacl.open(ct, k2))
        }
    }

    @Test
    fun rejectsTampering() {
        val a = Nacl.newSecretKey(); val b = Nacl.newSecretKey()
        val ct = Nacl.seal("hi".toByteArray(), Nacl.beforeNm(Nacl.publicKey(b), a))
        ct[ct.size - 1] = (ct[ct.size - 1].toInt() xor 1).toByte()
        assertFailsWith<SecurityException> { Nacl.open(ct, Nacl.beforeNm(Nacl.publicKey(a), b)) }
    }
}
