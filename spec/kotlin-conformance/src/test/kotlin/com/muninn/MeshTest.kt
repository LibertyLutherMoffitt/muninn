package com.muninn

import com.muninn.node.Sodium
import com.muninn.node.SodiumSealer
import com.muninn.node.TcpLink
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.fail

/**
 * The Android client's mesh in the situations a flight produces. Mirrors
 * python/tests/test_routing.py scenario for scenario: the two clients must
 * route identically, and python/tests/test_interop_kotlin.py then runs them
 * against each other.
 *
 * Every link is a real TCP socket and every body is sealed with libsodium.
 */
class MeshTest {

    private val A = "AA:AA:AA:AA:AA:AA"
    private val B = "BB:BB:BB:BB:BB:BB"
    private val C = "CC:CC:CC:CC:CC:CC"
    private val D = "DD:DD:DD:DD:DD:DD"
    private val X = "EE:EE:EE:EE:EE:EE"

    private val made = ArrayList<Node>()
    private val pairs = HashMap<Set<String>, MutableList<Pair<Socket, Socket>>>()

    @AfterTest
    fun tearDown() {
        made.forEach { it.mesh.close() }
    }

    inner class Node(
        val id: String,
        val store: MemoryMeshStore = MemoryMeshStore(),
        tuning: Mesh.Tuning = Mesh.Tuning(),
        keys: Pair<ByteArray, ByteArray> = Sodium.keypair(),
    ) {
        val secret = keys.first
        val pub = keys.second
        val mesh = Mesh(id, pub, SodiumSealer(secret), store, tuning = tuning)
        val messages = CopyOnWriteArrayList<Triple<ByteArray, String, String>>()
        val acks = CopyOnWriteArrayList<Pair<String, String>>()
        val reads = CopyOnWriteArrayList<Pair<String, String>>()
        val groups = CopyOnWriteArrayList<MeshGroup>()

        init {
            mesh.listener = object : Mesh.Listener {
                override fun onMessage(groupId: ByteArray, sender: String, text: String, msgId: ByteArray, timestamp: Long) {
                    messages += Triple(groupId, sender, text)
                }
                override fun onAck(msgId: ByteArray, from: String) { acks += msgId.toHex() to from }
                override fun onRead(msgId: ByteArray, from: String) { reads += msgId.toHex() to from }
                override fun onGroup(group: MeshGroup) { groups += group }
            }
            made += this
        }

        fun texts(): List<String> = messages.map { it.third }
        fun acked(msgId: ByteArray, from: String) = (msgId.toHex() to from) in acks
    }

    private fun node(id: String, tuning: Mesh.Tuning = Mesh.Tuning()) = Node(id, tuning = tuning)

    /** Connect two nodes over TCP; `aDialled` says which side initiated. */
    private fun link(a: Node, b: Node, aDialled: Boolean? = null): Pair<Socket, Socket> {
        val server = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
        var accepted: Socket? = null
        val acceptor = thread { accepted = server.accept() }
        val dialled = Socket(InetAddress.getLoopbackAddress(), server.localPort)
        acceptor.join(5000)
        server.close()
        val theirs = accepted ?: fail("accept failed")
        var okA = false
        var okB = false
        val ta = thread { okA = a.mesh.attach(TcpLink(dialled), b.id, aDialled) }
        val tb = thread { okB = b.mesh.attach(TcpLink(theirs), a.id, aDialled?.not()) }
        ta.join(5000)
        tb.join(5000)
        assertTrue(okA && okB, "handshake failed")
        pairs.getOrPut(setOf(a.id, b.id)) { ArrayList() } += dialled to theirs
        return dialled to theirs
    }

    private fun chain(vararg ids: String): List<Node> {
        val nodes = ids.map { node(it) }
        nodes.zipWithNext().forEach { (l, r) -> link(l, r) }
        return nodes
    }

    private fun waitFor(timeoutMs: Long = 3000, cond: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (cond()) return true
            Thread.sleep(10)
        }
        return cond()
    }

    private fun drop(sock: Socket) {
        runCatching { sock.shutdownInput() }
        runCatching { sock.shutdownOutput() }
        runCatching { sock.close() }
    }

    /** Cut every link between two nodes, from both ends, and wait for both to notice. */
    private fun sever(a: Node, b: Node) {
        val key = setOf(a.id, b.id)
        pairs.remove(key)?.forEach { (x, y) -> drop(x); drop(y) }
        assertTrue(waitFor { b.id !in a.mesh.connectedIds() && a.id !in b.mesh.connectedIds() })
    }

    private fun dm(from: Node, to: Node, text: String) = from.mesh.sendMessage(ZERO_GROUP_ID, text, listOf(to.id))

    // --- Basics ---

    @Test
    fun `a message and its ack cross a direct link`() {
        val (a, b) = chain(A, B)
        val sent = dm(a, b, "hello")
        assertEquals(listOf(B), sent.sent)
        assertTrue(waitFor { b.texts() == listOf("hello") })
        assertEquals(A, b.messages.single().second)
        assertTrue(waitFor { a.acked(sent.msgId, B) })
        assertEquals(0, a.mesh.pendingDeliveries())
    }

    // --- Routes ---

    @Test
    fun `a device two hops away is reachable through the middle`() {
        val (a, _, _) = chain(A, B, C)
        assertTrue(waitFor { a.mesh.routeVia(C) == B })
        val status = a.mesh.book.status(C)
        assertEquals(PeerBook.State.RELAY, status.state)
        assertEquals(B, status.via)
        assertEquals(2, status.hops)
    }

    @Test
    fun `routes reach down a chain of four`() {
        val (a, _, _, d) = chain(A, B, C, D)
        assertTrue(waitFor { a.mesh.routeVia(D) == B })
        assertEquals(3, a.mesh.book.status(D).hops)
        assertTrue(waitFor { d.mesh.routeVia(A) == C })
    }

    @Test
    fun `the shortest route wins`() {
        val a = node(A); val b = node(B); val c = node(C); val d = node(D); val x = node(X)
        link(a, x); link(x, d); link(d, c)
        assertTrue(waitFor { a.mesh.routeVia(C) == X })
        link(a, b); link(b, c)
        assertTrue(waitFor { a.mesh.routeVia(C) == B })
        assertEquals(2, a.mesh.book.status(C).hops)
    }

    @Test
    fun `losing the relay drops the routes it carried`() {
        val a = node(A); val b = node(B); val c = node(C)
        link(a, b); link(b, c)
        assertTrue(waitFor { a.mesh.routeVia(C) == B })
        sever(a, b)
        assertTrue(waitFor { a.mesh.routeVia(C) == null })
        assertTrue(a.mesh.book.pubkey(C) != null, "A still knows C well enough to write")
    }

    // --- Relay delivery ---

    @Test
    fun `a message crosses two relays and its ack comes back`() {
        val (a, b, c, d) = chain(A, B, C, D)
        assertTrue(waitFor { a.mesh.routeVia(D) != null && a.mesh.book.pubkey(D) != null })
        val sent = dm(a, d, "four rows back")
        assertEquals(listOf(D), sent.sent)
        assertTrue(waitFor { d.texts() == listOf("four rows back") })
        assertEquals(A, d.messages.single().second)
        assertTrue(waitFor { a.acked(sent.msgId, D) })
        assertTrue(b.messages.isEmpty() && c.messages.isEmpty(), "relays must not read it")
    }

    @Test
    fun `a relayed message goes to the neighbour that can reach it`() {
        val a = node(A); val b = node(B); val c = node(C); val x = node(X)
        link(a, x); link(a, b); link(b, c)
        assertTrue(waitFor { a.mesh.routeVia(C) == B })
        dm(a, c, "hi")
        assertTrue(waitFor { c.texts() == listOf("hi") })
        Thread.sleep(100)
        assertEquals(0, x.mesh.heldFor(C), "X was never asked to carry it")
    }

    @Test
    fun `a message rides along with whoever meets the recipient`() {
        val a = node(A); val b = node(B); val c = node(C)
        a.mesh.book.learnDirect(C, c.pub)
        link(a, b)
        dm(a, c, "see you at the gate")
        assertTrue(waitFor { b.mesh.heldFor(C) == 1 }, "B is not carrying it")
        sever(a, b) // A leaves
        link(b, c)
        assertTrue(waitFor { c.texts() == listOf("see you at the gate") })
        assertTrue(waitFor { b.mesh.heldFor(C) == 0 }, "delivered, so B stops carrying it")
    }

    @Test
    fun `a message is resent the moment a relay path appears`() {
        val a = node(A); val b = node(B); val c = node(C)
        a.mesh.book.learnDirect(C, c.pub)
        val sent = dm(a, c, "when you can")
        link(a, b); link(b, c)
        assertTrue(waitFor { c.texts() == listOf("when you can") })
        assertTrue(waitFor { a.acked(sent.msgId, C) })
    }

    @Test
    fun `the ack for a resent message reaches a sender who missed the first`() {
        val a = node(A, Mesh.Tuning(retryIntervalMs = 0)); val b = node(B); val c = node(C)
        a.mesh.book.learnDirect(C, c.pub)
        link(a, b)
        val sent = dm(a, c, "ping")
        assertTrue(waitFor { b.mesh.heldFor(C) == 1 })
        sever(a, b)
        link(b, c)
        assertTrue(waitFor { c.texts() == listOf("ping") })
        link(a, b)
        assertTrue(waitFor { a.acked(sent.msgId, C) }, "ACK was swallowed")
        assertEquals(1, c.messages.size, "the resend must not show twice")
    }

    @Test
    fun `maintain retries down a relay path`() {
        val fast = Mesh.Tuning(retryIntervalMs = 200)
        val a = node(A, fast); val b = node(B); val c = node(C)
        link(a, b)
        // B's link to C silently loses the first message frame written to it.
        val server = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
        var accepted: Socket? = null
        val t = thread { accepted = server.accept() }
        val bSide = Socket(InetAddress.getLoopbackAddress(), server.localPort)
        t.join(); server.close()
        val lossy = SwallowFirstMessage(TcpLink(bSide))
        val t1 = thread { b.mesh.attach(lossy, C, true) }
        val t2 = thread { c.mesh.attach(TcpLink(accepted!!), B, false) }
        t1.join(); t2.join()
        assertTrue(waitFor { a.mesh.routeVia(C) == B && a.mesh.book.pubkey(C) != null })

        dm(a, c, "lost once")
        assertFalse(waitFor(300) { c.messages.isNotEmpty() })
        Thread.sleep(250)
        a.mesh.maintain()
        assertTrue(waitFor { c.texts() == listOf("lost once") })
    }

    @Test
    fun `a flood around a loop terminates and every relay holds one copy`() {
        val a = node(A); val b = node(B); val c = node(C); val d = node(D)
        link(a, b); link(b, c); link(c, a)
        a.mesh.book.learnDirect(D, d.pub)
        val sent = dm(a, d, "anyone?")
        assertTrue(waitFor { b.mesh.heldFor(D) == 1 && c.mesh.heldFor(D) == 1 })
        Thread.sleep(100)
        assertEquals(1, b.mesh.heldFor(D))
        assertEquals(0, a.mesh.heldFor(D), "the sender keeps it in its outbox instead")
        link(c, d)
        assertTrue(waitFor { d.texts() == listOf("anyone?") })
        assertTrue(waitFor { a.acked(sent.msgId, D) })
        Thread.sleep(100)
        assertEquals(1, d.messages.size)
    }

    @Test
    fun `read receipts wait for a path to the sender`() {
        val a = node(A); val b = node(B); val c = node(C)
        link(a, b); link(b, c)
        assertTrue(waitFor { a.mesh.routeVia(C) == B && a.mesh.book.pubkey(C) != null })
        val sent = dm(a, c, "read me")
        assertTrue(waitFor { a.acked(sent.msgId, C) })
        sever(a, b)
        assertTrue(waitFor { !c.mesh.isReachable(A) })
        c.mesh.sendRead(sent.msgId)
        link(a, b)
        assertTrue(waitFor { (sent.msgId.toHex() to C) in a.reads }, "the READ was lost")
    }

    // --- Groups ---

    @Test
    fun `a relay that is not in the group passes it on without joining`() {
        val (a, b, c) = chain(A, B, C)
        assertTrue(waitFor { a.mesh.book.pubkey(C) != null })
        val group = a.mesh.createGroup("Row 12 and 14", listOf(C))
        assertTrue(waitFor { c.mesh.group(group.id) != null })
        assertNull(b.mesh.group(group.id))
        assertTrue(b.groups.isEmpty())
        a.mesh.sendMessage(group.id, "window or aisle?", listOf(C))
        assertTrue(waitFor { c.messages.isNotEmpty() })
        assertTrue(c.messages.single().first.contentEquals(group.id))
    }

    @Test
    fun `a member offline at creation gets the group before its messages`() {
        val a = node(A); val b = node(B); val c = node(C)
        link(a, b)
        a.mesh.book.learnDirect(C, c.pub)
        val group = a.mesh.createGroup("Trip", listOf(B, C))
        a.mesh.sendMessage(group.id, "first", listOf(B, C))
        assertTrue(waitFor { b.texts() == listOf("first") })
        link(b, c)
        assertTrue(waitFor { c.mesh.group(group.id) != null })
        assertTrue(waitFor { c.texts() == listOf("first") })
        assertEquals("Trip", c.groups.first().name)
    }

    // --- Crossed dials, reinstalls ---

    @Test
    fun `a crossed dial settles on one session at both ends, either order`() {
        for (lowFirst in listOf(true, false)) {
            val low = node(A); val high = node(B)
            fun byLow() = link(low, high, aDialled = true)
            fun byHigh() = link(high, low, aDialled = true)
            val first = if (lowFirst) byLow() else byHigh()
            val second = if (lowFirst) byHigh() else byLow()
            val lowOpened = if (lowFirst) first else second
            assertTrue(waitFor { B in low.mesh.connectedIds() && A in high.mesh.connectedIds() })
            Thread.sleep(200)
            // The session low dialled is the survivor: its sockets are open.
            assertFalse(lowOpened.first.isClosed, "low must keep the session it opened")
            val sent = dm(low, high, "still there?")
            assertTrue(waitFor { high.texts() == listOf("still there?") })
            assertTrue(waitFor { low.acked(sent.msgId, B) })
            low.mesh.close(); high.mesh.close()
        }
    }

    @Test
    fun `a peer that reinstalled still gets what was waiting for it`() {
        val a = node(A)
        val old = node(B)
        link(a, old)
        sever(a, old)
        dm(a, old, "you there?")
        val reinstalled = node(B)
        link(a, reinstalled)
        assertTrue(waitFor { reinstalled.texts() == listOf("you there?") }, "sealed to the old key")
    }

    @Test
    fun `unacked messages survive a restart`() {
        val store = MemoryMeshStore()
        val keys = Sodium.keypair()
        val a = Node(A, store = store, keys = keys)
        val b = node(B)
        link(a, b)
        sever(a, b)
        dm(a, b, "after the reboot")
        a.mesh.close()
        val revived = Node(A, store = store, keys = keys)
        assertEquals(1, revived.mesh.pendingDeliveries())
        link(revived, b)
        assertTrue(waitFor { b.texts() == listOf("after the reboot") })
    }

    // --- Robustness ---

    @Test
    fun `a malformed or unknown frame costs one frame, not the session`() {
        val a = node(A); val b = node(B)
        val (aSock, _) = link(a, b)
        synchronized(aSock) {
            aSock.getOutputStream().write(frameOf(TYPE_GROUP_SETUP, ByteArray(20) { 1 }))
            aSock.getOutputStream().write(frameOf(TYPE_ACK, byteArrayOf(1)))
            aSock.getOutputStream().write(frameOf(0x7E, "from the future".toByteArray()))
        }
        dm(a, b, "after the junk")
        assertTrue(waitFor { b.texts() == listOf("after the junk") })
        assertTrue(A in b.mesh.connectedIds())
    }

    @Test
    fun `nothing is lost or reordered on a link that keeps dropping`() {
        val a = node(A); val b = node(B)
        val sent = ArrayList<String>()
        link(a, b)
        repeat(3) { round ->
            sever(a, b)
            repeat(3) { i ->
                val line = "round $round line $i"
                dm(a, b, line)
                sent += line
            }
            link(a, b)
        }
        assertTrue(waitFor { b.messages.size == sent.size }, "${b.texts()}")
        assertEquals(sent, b.texts())
        assertTrue(waitFor { a.mesh.pendingDeliveries() == 0 })
    }

    @Test
    fun `a message too large for a frame is refused before it is saved`() {
        val (a, b) = chain(A, B)
        try {
            dm(a, b, "x".repeat(70_000))
            fail("expected FrameTooLarge")
        } catch (e: FrameTooLarge) {
            // expected
        }
        assertEquals(0, a.mesh.pendingDeliveries())
        assertTrue(a.store.messages().isEmpty())
    }

    @Test
    fun `names travel outward along the route`() {
        val (a, _, c, d) = chain(A, B, C, D)
        d.mesh.setDisplayName("Dave")
        assertTrue(waitFor { a.mesh.book.displayName(D) == "Dave" })
        d.mesh.setDisplayName("Dave in 31F")
        assertTrue(waitFor { a.mesh.book.displayName(D) == "Dave in 31F" })
        assertNotNull(c.mesh.book.pubkey(D))
    }
}

/** A link that silently loses the first MESSAGE frame written to it. */
private class SwallowFirstMessage(private val inner: MeshLink) : MeshLink {
    @Volatile private var armed = true
    override val input: InputStream get() = inner.input
    override val output: OutputStream = object : OutputStream() {
        override fun write(b: Int) = inner.output.write(b)
        override fun write(b: ByteArray, off: Int, len: Int) {
            if (armed && len > 0 && b[off] == TYPE_MESSAGE) {
                armed = false
                return
            }
            inner.output.write(b, off, len)
        }
        override fun flush() = inner.output.flush()
    }
    override fun close() = inner.close()
}
