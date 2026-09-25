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
 * The trigger side of system notifications, and the conversation model the
 * phone shows.
 *
 * Getting this wrong is not subtle to a user: notifying twice for one message,
 * or notifying someone about their own reply, is the fastest way to have the
 * app muted. The posting itself needs Android, but what to post — and when not
 * to — is decided here.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ArrivalsTest {

    private val me = "AA:AA:AA:AA:AA:AA"
    private val peer = "BB:BB:BB:BB:BB:BB"
    private lateinit var store: MemoryMeshStore
    private lateinit var mesh: Mesh

    /** Seals nothing: these tests never put a byte on a wire. */
    private object Plain : Sealer {
        override fun seal(plaintext: ByteArray, theirPub: ByteArray) = plaintext
        override fun open(sealed: ByteArray, theirPub: ByteArray) = sealed
    }

    @BeforeTest
    fun setUp() {
        ChatRepository.uiVisible = false
        ChatRepository.openConversation = null
        ChatRepository.onConversationRead = null
        store = MemoryMeshStore()
        mesh = Mesh(me, ByteArray(32), Plain, store)
        ChatRepository.attach(mesh, store)
    }

    @AfterTest
    fun tearDown() {
        mesh.close()
        ChatRepository.uiVisible = false
        ChatRepository.openConversation = null
        ChatRepository.onConversationRead = null
    }

    /** What Mesh does with a decrypted arrival: persist, then tell the UI. */
    private fun arrive(text: String, id: Int, ts: Long = 1_700_000_000, from: String = peer) {
        val msgId = ByteArray(16) { id.toByte() }
        store.saveIncoming(StoredMessage(msgId, ZERO_GROUP_ID, from, text, ts))
        mesh.listener.onMessage(ZERO_GROUP_ID, from, text, msgId, ts)
    }

    @Test
    fun `an incoming message is announced exactly once`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        arrive("wheels up", 1)
        advanceUntilIdle()

        assertEquals(1, seen.size)
        assertEquals("wheels up", seen.single().text)
        assertEquals("dm:$peer", seen.single().conv)
        assertFalse(seen.single().outgoing)
    }

    @Test
    fun `our own messages are never announced`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        assertTrue(ChatRepository.send("dm:$peer", "this is mine"))
        advanceUntilIdle()

        assertTrue(seen.isEmpty(), "sending must not notify the sender")
    }

    @Test
    fun `the arrival carries the timestamp from the wire, not the clock`() = runTest(UnconfinedTestDispatcher()) {
        // The bubble and the notification must agree about when it was sent.
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        arrive("sent earlier", 2, ts = 1_600_000_000)
        advanceUntilIdle()

        assertEquals(1_600_000_000_000L, seen.single().timestamp)
    }

    @Test
    fun `a missing wire timestamp falls back to now rather than 1970`() = runTest(UnconfinedTestDispatcher()) {
        val seen = mutableListOf<ChatRepository.Message>()
        backgroundScope.launch { ChatRepository.arrivals.collect { seen.add(it) } }
        advanceUntilIdle()

        val before = System.currentTimeMillis()
        arrive("no ts", 3, ts = 0)
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
        val cleared = mutableListOf<String>()
        ChatRepository.onConversationRead = { cleared += it }
        ChatRepository.markConversationRead("dm:$peer")
        assertEquals(listOf("dm:$peer"), cleared)
    }

    @Test
    fun `marking read is safe with no listener attached`() {
        ChatRepository.onConversationRead = null
        ChatRepository.markConversationRead("dm:$peer") // must not throw
    }

    // --- Conversations ---

    private fun conversation(id: String) = ChatRepository.conversations.value.first { it.id == id }

    @Test
    fun `unread counts until the conversation is actually opened`() {
        arrive("one", 4)
        arrive("two", 5)
        assertEquals(2, conversation("dm:$peer").unread)

        // On screen, but a different conversation: still unread.
        ChatRepository.uiVisible = true
        ChatRepository.openConversation = "dm:CC:CC:CC:CC:CC:CC"
        arrive("three", 6)
        assertEquals(3, conversation("dm:$peer").unread)

        ChatRepository.markConversationRead("dm:$peer")
        assertEquals(0, conversation("dm:$peer").unread)
        assertTrue(store.loadHistory().none { !it.outgoing && !it.displayed })
    }

    @Test
    fun `a message arriving in the open conversation is read at once`() {
        ChatRepository.uiVisible = true
        ChatRepository.openConversation = "dm:$peer"
        arrive("you're looking at this", 7)
        assertEquals(0, conversation("dm:$peer").unread)
    }

    @Test
    fun `sending works with nobody in range and is kept for later`() {
        assertTrue(ChatRepository.send("dm:$peer", "whenever you're back"))
        val msg = ChatRepository.messages.value.last()
        assertTrue(msg.outgoing)
        assertEquals(ChatRepository.Ack.SENT, msg.ack)
        assertEquals(listOf(peer), msg.recipients)
        assertEquals(1, store.loadUnacked().size, "must be waiting in the outbox")
    }

    @Test
    fun `ticks never walk backwards`() {
        ChatRepository.send("dm:$peer", "tick")
        val id = ChatRepository.messages.value.last().msgId!!
        mesh.listener.onRead(id, peer)
        mesh.listener.onAck(id, peer) // late ACK after the READ
        assertEquals(ChatRepository.Ack.READ, ChatRepository.messages.value.last().ack)
    }

    @Test
    fun `history survives a restart, unread and all`() {
        arrive("before the reboot", 8)
        ChatRepository.send("dm:$peer", "mine, before the reboot")

        // A new process: fresh mesh over the same store.
        val revived = Mesh(me, ByteArray(32), Plain, store)
        ChatRepository.attach(revived, store)
        val texts = ChatRepository.messages.value.map { it.text }
        assertEquals(listOf("before the reboot", "mine, before the reboot"), texts)
        assertEquals(1, conversation("dm:$peer").unread)
        revived.close()
    }

    @Test
    fun `every known peer gets a conversation, even before anyone has written`() {
        mesh.book.learnRelayed("CC:CC:CC:CC:CC:CC", ByteArray(32) { 3 })
        ChatRepository.refresh()
        assertTrue(ChatRepository.conversations.value.any { it.id == "dm:CC:CC:CC:CC:CC:CC" })
        assertTrue(ChatRepository.conversations.value.none { it.peer == me }, "never a chat with yourself")
    }
}
