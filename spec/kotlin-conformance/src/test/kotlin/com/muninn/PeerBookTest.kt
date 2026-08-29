package com.muninn

import com.muninn.PeerBook.State
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * PeerBook holds the rules the Android client must apply identically to the
 * desktop one — key trust, name precedence, dedup, and presence transitions.
 * The assertions here mirror python/tests/test_presence.py and test_groups.py
 * case for case, so a divergence between the clients shows up as a red test
 * rather than as two devices disagreeing about who is online.
 */
class PeerBookTest {

    private val a = "AA:AA:AA:AA:AA:AA"
    private val b = "BB:BB:BB:BB:BB:BB"
    private fun key(n: Int) = ByteArray(32) { n.toByte() }
    private fun msg(n: Int) = ByteArray(16) { n.toByte() }

    // --- Keys ---

    @Test
    fun `a handshake key overwrites`() {
        val book = PeerBook()
        book.learnDirect(a, key(1))
        book.learnDirect(a, key(2))
        assertContentEquals(key(2), book.pubkey(a))
    }

    @Test
    fun `a relayed key never overwrites a handshake key`() {
        // A plaintext PEER_ANNC must not be able to redirect our encryption.
        val book = PeerBook()
        book.learnDirect(a, key(1))
        book.learnRelayed(a, key(9))
        assertContentEquals(key(1), book.pubkey(a))
    }

    @Test
    fun `a relayed key fills in an unknown peer`() {
        val book = PeerBook()
        book.learnRelayed(b, key(7))
        assertContentEquals(key(7), book.pubkey(b))
    }

    @Test
    fun `keys are stored case-insensitively`() {
        val book = PeerBook()
        book.learnDirect(a.lowercase(), key(1))
        assertContentEquals(key(1), book.pubkey(a))
    }

    // --- Names ---

    @Test
    fun `an unknown peer displays as its wire id`() {
        assertEquals(a, PeerBook().displayName(a))
    }

    @Test
    fun `an override beats the peer's own name`() {
        val book = PeerBook()
        book.setSelfChosenName(a, "Ravn")
        assertEquals("Ravn", book.displayName(a))
        book.setOverride(a, "The Pilot")
        assertEquals("The Pilot", book.displayName(a))
        book.setOverride(a, "")
        assertEquals("Ravn", book.displayName(a))
    }

    @Test
    fun `clearing a name falls back to the wire id`() {
        val book = PeerBook()
        book.setSelfChosenName(a, "Ravn")
        book.setSelfChosenName(a, "")
        assertEquals(a, book.displayName(a))
    }

    @Test
    fun `a peer announcing its own MAC as a name is treated as unset`() {
        // Older clients defaulted display_name to their MAC.
        val book = PeerBook()
        book.setSelfChosenName(a, a)
        assertEquals(a, book.displayName(a))
    }

    @Test
    fun `resolution accepts a MAC or either kind of name`() {
        val book = PeerBook()
        book.learnDirect(a, key(1))
        book.setSelfChosenName(a, "Ravn")
        assertEquals(a, book.resolve(a))
        assertEquals(a, book.resolve(a.lowercase()))
        assertEquals(a, book.resolve("ravn"))
        assertNull(book.resolve("nobody"))
    }

    @Test
    fun `an override wins resolution over another peer's own name`() {
        val book = PeerBook()
        book.learnDirect(a, key(1))
        book.learnDirect(b, key(2))
        book.setSelfChosenName(b, "shared")
        book.setOverride(a, "shared")
        assertEquals(a, book.resolve("shared"))
    }

    // --- Dedup ---

    @Test
    fun `a message id can only be claimed once`() {
        val book = PeerBook()
        assertTrue(book.claimSeen(msg(1)))
        assertFalse(book.claimSeen(msg(1)))
    }

    @Test
    fun `dedup compares contents not identity`() {
        // The bug this guards: a ByteArray in a HashSet hashes by identity, so
        // an equal-but-distinct msg_id would look unseen and deliver twice.
        val book = PeerBook()
        assertTrue(book.claimSeen(msg(3)))
        assertFalse(book.claimSeen(msg(3).copyOf()))
    }

    @Test
    fun `a released claim can be made again`() {
        // Needed so a retransmit still lands after a decrypt failure.
        val book = PeerBook()
        book.claimSeen(msg(2))
        book.releaseSeen(msg(2))
        assertTrue(book.claimSeen(msg(2)))
    }

    // --- Presence ---

    @Test
    fun `an unknown peer is offline`() {
        val status = PeerBook().status(a)
        assertEquals(State.OFFLINE, status.state)
        assertEquals("never seen", status.describe())
    }

    @Test
    fun `a sighting makes a peer nearby`() {
        val book = PeerBook()
        book.recordSighting(a)
        assertEquals(State.NEARBY, book.status(a).state)
    }

    @Test
    fun `connecting makes a peer connected and reachable`() {
        val book = PeerBook()
        book.recordConnected(a)
        val status = book.status(a)
        assertEquals(State.CONNECTED, status.state)
        assertTrue(status.isReachable)
        assertEquals("connected", status.describe())
    }

    @Test
    fun `disconnecting drops to nearby not offline`() {
        val book = PeerBook()
        book.recordConnected(a)
        book.recordDisconnected(a)
        assertEquals(State.NEARBY, book.status(a).state)
    }

    @Test
    fun `a relay is reachable but not connected`() {
        val book = PeerBook()
        book.recordRelay(a, b)
        val status = book.status(a)
        assertEquals(State.RELAY, status.state)
        assertTrue(status.isReachable)
        assertEquals(b, status.via)
    }

    @Test
    fun `a relay never downgrades a live session`() {
        val book = PeerBook()
        book.recordConnected(a)
        book.recordRelay(a, b)
        assertEquals(State.CONNECTED, book.status(a).state)
    }

    @Test
    fun `a sighting does not clobber a live session`() {
        val book = PeerBook()
        book.recordConnected(a)
        book.recordSighting(a)
        assertEquals(State.CONNECTED, book.status(a).state)
    }

    @Test
    fun `one dial failure is not yet unreachable`() {
        val book = PeerBook()
        book.recordSighting(a)
        book.recordDialFailure(a, "br-connection-refused")
        assertFalse(book.status(a).unreachableNearby)
    }

    @Test
    fun `repeated dial failures mark a visible peer unreachable`() {
        val book = PeerBook()
        book.recordSighting(a)
        repeat(PeerBook.UNREACHABLE_AFTER) { book.recordDialFailure(a, "key-missing") }
        val status = book.status(a)
        assertTrue(status.unreachableNearby)
        assertEquals("key-missing", status.lastError)
        assertTrue(status.describe().startsWith("nearby, can't connect"))
        assertEquals(listOf(a), book.nearbyUnreachable())
    }

    @Test
    fun `a successful connection clears the failure count`() {
        val book = PeerBook()
        repeat(5) { book.recordDialFailure(a) }
        book.recordConnected(a)
        assertEquals(0, book.status(a).failedDials)
        assertNull(book.status(a).lastError)
        assertEquals(emptyList(), book.nearbyUnreachable())
    }

    @Test
    fun `a stale sighting ages out to offline`() {
        val book = PeerBook()
        val t0 = 1_000_000L
        book.recordSighting(a, now = t0)
        assertEquals(State.NEARBY, book.status(a, now = t0 + PeerBook.NEARBY_WINDOW_MS - 1).state)
        assertEquals(State.OFFLINE, book.status(a, now = t0 + PeerBook.NEARBY_WINDOW_MS + 1).state)
    }

    @Test
    fun `ageing does not mutate the stored record`() {
        val book = PeerBook()
        val t0 = 1_000_000L
        book.recordSighting(a, now = t0)
        val far = t0 + PeerBook.NEARBY_WINDOW_MS + 1
        assertEquals(State.OFFLINE, book.status(a, now = far).state)
        // A fresh scan brings it straight back — nothing was overwritten.
        book.recordSighting(a, now = far)
        assertEquals(State.NEARBY, book.status(a, now = far).state)
    }

    @Test
    fun `a connected peer never ages out`() {
        val book = PeerBook()
        book.recordConnected(a, now = 1_000L)
        assertEquals(State.CONNECTED, book.status(a, now = 1_000L + 10 * PeerBook.NEARBY_WINDOW_MS).state)
    }

    @Test
    fun `connected lists only live sessions`() {
        val book = PeerBook()
        book.recordConnected(a)
        book.recordRelay(b, a)
        assertEquals(listOf(a), book.connected())
    }

    // --- Formatting: must read the same as presence.format_ago ---

    @Test
    fun `relative time formatting matches the desktop client`() {
        assertEquals("never", PeerBook.formatAgo(null))
        assertEquals("just now", PeerBook.formatAgo(0))
        assertEquals("just now", PeerBook.formatAgo(44_000))
        assertEquals("0m ago", PeerBook.formatAgo(45_000))
        assertEquals("1m ago", PeerBook.formatAgo(60_000))
        assertEquals("59m ago", PeerBook.formatAgo(3_599_000))
        assertEquals("1h ago", PeerBook.formatAgo(3_600_000))
        assertEquals("23h ago", PeerBook.formatAgo(86_399_000))
        assertEquals("1d ago", PeerBook.formatAgo(86_400_000))
        assertEquals("9d ago", PeerBook.formatAgo(9 * 86_400_000L))
    }
}
