package com.muninn

import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Process-wide bridge between [MuninnService]'s PeerSessions and the chat UI.
 *
 * Holds the shared [PeerBook] — the one place keys, names, dedup and presence
 * live — so the service's sessions and the UI never disagree about who is
 * online or what to call them.
 *
 * Prototype scope: a flat message log. Sending fans out to every live session,
 * which is 1:1 today. Superseded by a per-conversation model and on-disk
 * history when `storage.py` is ported.
 */
object ChatRepository {

    /** Delivery state of an outgoing message — mirrors the desktop client. */
    enum class Ack { SENT, ACKED, READ }

    data class Message(
        val peer: String,
        val text: String,
        val outgoing: Boolean,
        val timestamp: Long = System.currentTimeMillis(),
        val msgId: ByteArray? = null,
        val ack: Ack = Ack.SENT,
    ) {
        // ByteArray equality is identity-based, which would make the generated
        // data-class equals wrong for any message carrying an id.
        override fun equals(other: Any?): Boolean =
            this === other || (
                other is Message &&
                    peer == other.peer &&
                    text == other.text &&
                    outgoing == other.outgoing &&
                    timestamp == other.timestamp &&
                    ack == other.ack &&
                    (msgId?.contentEquals(other.msgId ?: ByteArray(0)) ?: (other.msgId == null))
                )

        override fun hashCode(): Int {
            var h = peer.hashCode()
            h = 31 * h + text.hashCode()
            h = 31 * h + outgoing.hashCode()
            h = 31 * h + timestamp.hashCode()
            h = 31 * h + ack.hashCode()
            return 31 * h + (msgId?.contentHashCode() ?: 0)
        }
    }

    /** Shared peer state. The service seeds it; the UI reads it. */
    val book = PeerBook()

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    /** Wire ids with a live session. Kept for the connection banner. */
    private val _peers = MutableStateFlow<Set<String>>(emptySet())
    val peers: StateFlow<Set<String>> = _peers.asStateFlow()

    /** Every peer we know of, with how reachable it is right now. */
    private val _presence = MutableStateFlow<List<PeerBook.PeerStatus>>(emptyList())
    val presence: StateFlow<List<PeerBook.PeerStatus>> = _presence.asStateFlow()

    private class Session(
        val send: (String) -> Boolean,
        val sendRead: (ByteArray) -> Boolean,
    )

    private val sessions = ConcurrentHashMap<String, Session>()
    // Incoming messages whose read receipt is still owed, by sender wire id.
    private val unread = ConcurrentHashMap<String, MutableList<ByteArray>>()

    fun registerPeer(
        wireId: String,
        send: (String) -> Boolean,
        sendRead: (ByteArray) -> Boolean,
    ) {
        sessions[wireId] = Session(send, sendRead)
        _peers.value = sessions.keys.toSet()
        refreshPresence()
    }

    fun unregisterPeer(wireId: String) {
        sessions.remove(wireId)
        _peers.value = sessions.keys.toSet()
        refreshPresence()
    }

    /** Recompute the presence list. Cheap; call after anything that changes it. */
    fun refreshPresence() {
        _presence.value = book.statuses().values.sortedWith(
            compareBy({ it.state.ordinal }, { book.displayName(it.wireId) })
        )
    }

    fun displayName(wireId: String): String = book.displayName(wireId)

    fun onPeerRenamed(wireId: String, name: String) {
        // Re-emit the message list so bubbles labelled with the old name (or a
        // raw MAC) pick up the new one.
        _messages.value = _messages.value.toList()
        refreshPresence()
    }

    fun onIncoming(wireId: String, text: String, msgId: ByteArray? = null, tsSeconds: Long = 0) {
        val ts = if (tsSeconds > 0) tsSeconds * 1000L else System.currentTimeMillis()
        append(Message(wireId, text, outgoing = false, timestamp = ts, msgId = msgId))
        if (msgId != null) {
            unread.getOrPut(wireId) { mutableListOf() }.add(msgId)
        }
    }

    fun onAck(fromWireId: String, msgId: ByteArray) = advance(msgId, Ack.ACKED)

    fun onRead(fromWireId: String, msgId: ByteArray) = advance(msgId, Ack.READ)

    /**
     * Send read receipts for everything received but not yet acknowledged as
     * displayed. Call when the conversation is actually on screen — that is
     * what separates a READ from the ACK the recv loop already sent.
     */
    fun markConversationRead() {
        for ((wireId, ids) in unread) {
            val session = sessions[wireId] ?: continue
            val pending = synchronized(ids) { ids.toList().also { ids.clear() } }
            for (id in pending) {
                if (!session.sendRead(id)) {
                    // Socket died mid-flush; keep it owed for the reconnect.
                    synchronized(ids) { ids.add(id) }
                }
            }
        }
    }

    /** Fan out to every connected peer. Returns true if at least one accepted. */
    fun send(text: String): Boolean {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return false
        var sent = false
        sessions.forEach { (wire, session) ->
            if (session.send(trimmed)) {
                sent = true
                append(Message(wire, trimmed, outgoing = true))
            }
        }
        return sent
    }

    @Synchronized
    private fun advance(msgId: ByteArray, state: Ack) {
        var changed = false
        val next = _messages.value.map { msg ->
            // Never walk a receipt backwards: a late ACK must not undo a READ.
            if (msg.outgoing && msg.msgId?.contentEquals(msgId) == true &&
                state.ordinal > msg.ack.ordinal
            ) {
                changed = true
                msg.copy(ack = state)
            } else {
                msg
            }
        }
        if (changed) _messages.value = next
    }

    @Synchronized
    private fun append(msg: Message) {
        _messages.value = _messages.value + msg
    }
}
