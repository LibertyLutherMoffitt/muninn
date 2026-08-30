package com.muninn

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertContains
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Mirrors python/tests/test_dialer.py case for case.
 *
 * The situation this code exists for — a full cabin, forty devices in range,
 * one of them the peer you want — cannot practically be reproduced with
 * hardware, and the two clients must behave identically in it. A divergence
 * here means one device gives up on a peer the other keeps trying.
 */
class DialSchedulerTest {

    private val local = "AA:AA:AA:AA:AA:AA"
    private val peer = "BB:BB:BB:BB:BB:BB"
    private val peer2 = "BB:BB:BB:BB:BB:CC"
    private val noise = "CC:CC:CC:CC:CC:01"
    private val neverConnected: (String) -> Boolean = { false }

    private fun sched(policy: ScanPolicy = ScanPolicy.AGGRESSIVE) = DialScheduler(policy, local)

    /** One Muninn peer plus a lot of other people's audio gear. */
    private fun cabin(s: DialScheduler, now: Long, headsets: Int = 40) {
        s.saw(peer, now, isPeer = true)
        repeat(headsets) { s.saw("CC:CC:CC:CC:%02X:%02X".format(it / 256, it % 256), now) }
    }

    // --- Priority ---

    @Test
    fun `a known peer is dialled before any unknown device`() {
        val s = sched()
        cabin(s, 1000)
        val plan = s.plan(1000, neverConnected)
        assertEquals(peer, plan.targets.first())
        assertEquals(listOf(peer), plan.peers)
    }

    @Test
    fun `probes are rationed so a crowd cannot starve real peers`() {
        val s = sched()
        cabin(s, 1000, headsets = 200)
        val plan = s.plan(1000, neverConnected)
        assertEquals(ScanPolicy.AGGRESSIVE.probeBudget, plan.probes.size)
        assertEquals(listOf(peer), plan.peers, "the peer must still be served")
    }

    @Test
    fun `a connected peer is not redialled`() {
        val s = sched()
        cabin(s, 1000)
        assertFalse(peer in s.plan(1000, { it == peer }).targets)
    }

    @Test
    fun `the freshest sighting is dialled first`() {
        val s = sched()
        s.saw(peer, 1000, isPeer = true)
        s.saw(peer2, 1100, isPeer = true)
        assertEquals(peer2, s.plan(1200, neverConnected).peers.first())
    }

    // --- Backoff curves ---

    @Test
    fun `a known peer is retried soon and never abandoned`() {
        val s = sched()
        s.saw(peer, 0, isPeer = true)
        var now = 0L
        repeat(30) {
            s.failed(peer, now)
            now += ScanPolicy.AGGRESSIVE.peerBackoffMaxMs
        }
        assertEquals(listOf(peer), s.plan(now, neverConnected).peers)
    }

    @Test
    fun `a peer backoff never exceeds its cap`() {
        val s = sched()
        s.markPeer(peer)
        repeat(20) { s.failed(peer, 0) }
        assertEquals(ScanPolicy.AGGRESSIVE.peerBackoffMaxMs, s.backoffOf(peer))
    }

    @Test
    fun `an unknown device backs off much harder than a peer`() {
        val s = sched()
        s.saw(noise, 0)
        s.failed(noise, 0)
        s.markPeer(peer)
        s.failed(peer, 0)
        assertTrue(s.backoffOf(noise) > s.backoffOf(peer))
    }

    @Test
    fun `a failed probe is not retried immediately`() {
        val s = sched()
        s.saw(noise, 0)
        s.failed(noise, 0, "connect refused")
        assertFalse(noise in s.plan(1, neverConnected).targets)
        val later = ScanPolicy.AGGRESSIVE.probeBackoffBaseMs + 1
        s.saw(noise, later) // still in the cabin
        assertContains(s.plan(later, neverConnected).targets, noise)
    }

    @Test
    fun `repeated probe failures grow the wait`() {
        val s = sched()
        s.saw(noise, 0)
        val waits = (1..4).map { s.failed(noise, 0); s.backoffOf(noise) }
        assertEquals(waits.sorted(), waits)
        assertTrue(waits.last() > waits.first())
    }

    @Test
    fun `backoff is capped for unknown devices too`() {
        val s = sched()
        s.saw(noise, 0)
        repeat(40) { s.failed(noise, 0) }
        assertEquals(ScanPolicy.AGGRESSIVE.probeBackoffMaxMs, s.backoffOf(noise))
    }

    // --- Promotion ---

    @Test
    fun `a successful dial makes a device a peer forever`() {
        val s = sched()
        s.saw(noise, 0)
        s.succeeded(noise)
        // A later sighting without the UUID must not demote it.
        s.saw(noise, 100, isPeer = false)
        s.failed(noise, 100)
        assertTrue(s.backoffOf(noise) <= ScanPolicy.AGGRESSIVE.peerBackoffMaxMs)
        assertContains(s.plan(200_000, neverConnected).peers, noise)
    }

    @Test
    fun `being identified as a peer clears an accumulated probe backoff`() {
        val s = sched()
        s.saw(noise, 0)
        repeat(6) { s.failed(noise, 0) }
        assertFalse(noise in s.plan(10, neverConnected).targets)
        s.saw(noise, 10, isPeer = true) // SDP finally resolved
        assertContains(s.plan(10, neverConnected).peers, noise)
    }

    // --- Visibility ---

    @Test
    fun `an unknown device that left is not dialled`() {
        val s = sched()
        s.saw(noise, 0)
        assertFalse(noise in s.plan(10_000_000, neverConnected).targets)
    }

    @Test
    fun `a known peer is dialled even when this inquiry missed it`() {
        val s = sched()
        s.saw(peer, 0, isPeer = true)
        assertEquals(listOf(peer), s.plan(10_000_000, neverConnected).peers)
    }

    // --- Self ---

    @Test
    fun `our own address is never dialled`() {
        val s = sched()
        s.saw(local, 0, isPeer = true)
        s.markPeer(local)
        assertTrue(s.plan(0, neverConnected).isEmpty)
    }

    // --- Policy ---

    @Test
    fun `changing policy takes effect immediately`() {
        val s = sched()
        s.saw(noise, 0)
        s.failed(noise, 0)
        assertFalse(noise in s.plan(1, neverConnected).targets)
        s.policy = (ScanPolicy.CONSERVATIVE)
        assertContains(s.plan(1, neverConnected).targets, noise)
    }

    @Test
    fun `setting the same policy does not reset backoffs`() {
        val s = sched()
        s.saw(noise, 0)
        s.failed(noise, 0)
        s.policy = (ScanPolicy.AGGRESSIVE)
        assertFalse(noise in s.plan(1, neverConnected).targets)
    }

    @Test
    fun `the presets are ordered from eager to quiet`() {
        val order = listOf(ScanPolicy.AGGRESSIVE, ScanPolicy.BALANCED, ScanPolicy.CONSERVATIVE)
        assertEquals(order.map { it.inquiryIntervalMs }.sorted(), order.map { it.inquiryIntervalMs })
        assertEquals(order.map { it.dialIntervalMs }.sorted(), order.map { it.dialIntervalMs })
        assertEquals(
            order.map { it.probeBudget }.sortedDescending(),
            order.map { it.probeBudget },
        )
    }

    @Test
    fun `the default is the eager one`() {
        // Finding peers unattended is the whole point.
        assertEquals(ScanPolicy.AGGRESSIVE, ScanPolicy.DEFAULT)
    }

    @Test
    fun `policy lookup is forgiving and matches the desktop keys`() {
        assertEquals(ScanPolicy.AGGRESSIVE, ScanPolicy.byKey("AGGRESSIVE"))
        assertEquals(ScanPolicy.BALANCED, ScanPolicy.byKey(" balanced "))
        assertEquals(null, ScanPolicy.byKey("nonsense"))
        assertEquals(null, ScanPolicy.byKey(null))
    }

    @Test
    fun `stats summarise what is being tracked`() {
        val s = sched()
        cabin(s, 1000, headsets = 5)
        s.failed(noise, 1000)
        val stats = s.stats()
        assertEquals("aggressive", stats.policy)
        assertEquals(1, stats.peers)
        assertTrue(stats.unknown >= 5)
        assertEquals(stats.tracked, stats.peers + stats.unknown)
    }

    // --- Cross-language agreement ---

    @Test
    fun `the timings match the desktop client`() {
        // Read scanpolicy.py directly: a drift here means the phone and the
        // laptop disagree about how hard to hunt, which is invisible until
        // one of them stops finding peers the other sees.
        val src = File("../../python/src/muninn/scanpolicy.py").readText()
        fun field(preset: String, name: String): Double {
            val block = src.substringAfter("$preset = ScanPolicy(").substringBefore(")")
            val raw = Regex("$name=([0-9.]+)").find(block)?.groupValues?.get(1)
            requireNotNull(raw) { "$name missing from $preset in scanpolicy.py" }
            return raw.toDouble()
        }
        for (policy in ScanPolicy.entries) {
            val preset = policy.name
            assertEquals(policy.inquiryIntervalMs / 1000.0, field(preset, "inquiry_interval"), "$preset inquiry")
            assertEquals(policy.dialIntervalMs / 1000.0, field(preset, "dial_interval"), "$preset dial")
            assertEquals(policy.peerBackoffBaseMs / 1000.0, field(preset, "peer_backoff_base"), "$preset peer base")
            assertEquals(policy.peerBackoffMaxMs / 1000.0, field(preset, "peer_backoff_max"), "$preset peer max")
            assertEquals(policy.probeBackoffBaseMs / 1000.0, field(preset, "probe_backoff_base"), "$preset probe base")
            assertEquals(policy.probeBackoffMaxMs / 1000.0, field(preset, "probe_backoff_max"), "$preset probe max")
            assertEquals(policy.probeBudget.toDouble(), field(preset, "probe_budget"), "$preset budget")
        }
    }
}
