package com.muninn

import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest

/**
 * The trigger side of system notifications.
 *
 * Getting this wrong is not subtle to a user: notifying twice for one message,
 * or notifying someone about their own reply, is the fastest way to have the
 * app muted. The posting itself needs Android, but what to post — and when not
 * to — is decided here.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ArrivalsTest {

    private val peer = "BB:BB:BB:BB:BB:BB"

    @BeforeTest
    @AfterTest
    fun reset() {
        ChatRepository.uiVisible = false
        ChatRepository.onConversationRead = null
    }

    @Test
    fun `an incoming message is announced exactly once`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        ChatRepository.onIncoming(peer, "wheels up", ByteArray(16) { 1 }, 1_700_000_000)
        advanceUntilIdle()

        assertEquals(1, seen.size)
        assertEquals("wheels up", seen.single().text)
        assertFalse(seen.single().outgoing)
    }

    @Test
    fun `our own messages are never announced`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        ChatRepository.send("this is mine")
        advanceUntilIdle()

        assertTrue(seen.isEmpty(), "sending must not notify the sender")
    }

    @Test
    fun `the arrival carries the timestamp from the wire, not the clock`() = runTest(UnconfinedTestDispatcher()) {
        // The bubble and the notification must agree about when it was sent.
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        ChatRepository.onIncoming(peer, "sent earlier", ByteArray(16) { 2 }, 1_600_000_000)
        advanceUntilIdle()

        assertEquals(1_600_000_000_000L, seen.single().timestamp)
    }

    @Test
    fun `a missing wire timestamp falls back to now rather than 1970`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        val before = System.currentTimeMillis()
        ChatRepository.onIncoming(peer, "no ts", ByteArray(16) { 3 }, 0)
        advanceUntilIdle()

        assertTrue(seen.single().timestamp >= before)
    }

    @Test
    fun `the ui reports whether it is on screen`() {
        // The service checks this before posting; defaulting to visible would
        // silently suppress every notification.
        assertFalse(ChatRepository.uiVisible, "must default to not visible")
        ChatRepository.uiVisible = true
        assertTrue(ChatRepository.uiVisible)
    }

    @Test
    fun `reading a conversation signals that its notification can go`() {
        var cleared = 0
        ChatRepository.onConversationRead = { cleared += 1 }
        ChatRepository.markConversationRead()
        assertEquals(1, cleared)
    }

    @Test
    fun `marking read is safe with no listener attached`() {
        ChatRepository.onConversationRead = null
        ChatRepository.markConversationRead() // must not throw
    }
}
