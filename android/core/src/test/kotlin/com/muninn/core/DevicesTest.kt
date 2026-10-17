package com.muninn.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Device-set validation (mirrors python/tests/test_devices.py::test_forged_sets_are_rejected). */
class DevicesTest {
    private class Dev(val id: String) { val priv = Nacl.newSecretKey(); val pub = Nacl.publicKey(priv) }
    private val lap = Dev("AA:00:00:00:00:01")
    private val phone = Dev("AA:00:00:00:00:02")
    private val mal = Dev("AA:00:00:00:00:03")
    private val pinned = mapOf(lap.id to lap.pub, phone.id to phone.pub, mal.id to mal.pub)
    private fun pin(id: String) = pinned[id]
    private val joined = 1_800_000_000L

    private fun honest(): Devices.DeviceSet {
        val consent = Devices.makeConsent(phone.priv, lap.id, phone.id, joined)
        return Devices.DeviceSet(lap.id, 1, listOf(Devices.Entry(lap.id, lap.pub, 0, ByteArray(0)),
            Devices.Entry(phone.id, phone.pub, joined, consent))).sign(lap.priv)
    }

    @Test fun honestSetVerifiesAndRoundTrips() {
        val ds = honest()
        assertNull(Devices.check(ds, ::pin))
        val back = Devices.DeviceSet.fromJson(ds.toJson())
        assertNull(Devices.check(back, ::pin))
        val idx = Devices.Index(mapOf(ds.account to ds))
        assertEquals(lap.id, idx.owner(phone.id))
        assertEquals(listOf(lap.id, phone.id), idx.devices(lap.id))
        // the phone left (its own signature): it is itself again, for everyone
        val lv = Devices.makeLeave(phone.priv, lap.id, phone.id, joined)
        assertTrue(Devices.checkLeave(lv, ::pin))
        assertTrue(!Devices.checkLeave(Devices.makeLeave(mal.priv, lap.id, phone.id, joined), ::pin)) // forged
        val back2 = Devices.Leave.fromObj(Json.obj(lv.toJson())["leave"] as Map<String, Any?>)
        assertTrue(Devices.checkLeave(back2, ::pin))
        assertEquals(phone.id, Devices.Index(mapOf(ds.account to ds), mapOf((lap.id to phone.id) to joined)).owner(phone.id))
    }

    @Test fun forgeriesAreRejected() {
        // no consent from the phone
        val noConsent = Devices.DeviceSet(mal.id, 1, listOf(Devices.Entry(mal.id, mal.pub, 0, ByteArray(0)),
            Devices.Entry(phone.id, phone.pub, joined, ByteArray(64)))).sign(mal.priv)
        assertTrue(Devices.check(noConsent, ::pin)!!.contains("consent"))
        // the phone's consent to lap reused for mal's account
        val consent = Devices.makeConsent(phone.priv, lap.id, phone.id, joined)
        val reused = Devices.DeviceSet(mal.id, 1, listOf(Devices.Entry(mal.id, mal.pub, 0, ByteArray(0)),
            Devices.Entry(phone.id, phone.pub, joined, consent))).sign(mal.priv)
        assertNotNull(Devices.check(reused, ::pin))
        // lap's account, signed by mal
        val h = honest()
        val forged = Devices.DeviceSet(h.account, 2, h.entries).sign(mal.priv)
        assertEquals("bad primary signature", Devices.check(forged, ::pin))
        // a listed key that isn't the pinned one
        val swapped = Devices.DeviceSet(lap.id, 3, listOf(Devices.Entry(lap.id, mal.pub, 0, ByteArray(0)),
            Devices.Entry(phone.id, phone.pub, joined, consent))).sign(mal.priv)
        assertTrue(Devices.check(swapped, ::pin)!!.startsWith("key mismatch"))
    }
}
