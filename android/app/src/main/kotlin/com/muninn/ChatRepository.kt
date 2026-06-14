package com.muninn

import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Process-wide bridge between the [MuninnService]'s PeerSessions and the chat
 * UI in [MainActivity]. Prototype scope: a flat message log plus the set of
 * connected peer wire ids. Sending fans out to every live session — 1:1 today,
 * so there is normally just one.
 *
 * Superseded by a proper ConnectionManager + per-conversation model and
 * on-disk history in milestone 4 (the `peers.py` / `storage.py` port).
 */
object ChatRepository {
    data class Message(
        val peer: String,
        val text: String,
        val outgoing: Boolean,
        val timestamp: Long = System.currentTimeMillis(),
    )

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    private val _peers = MutableStateFlow<Set<String>>(emptySet())
    val peers: StateFlow<Set<String>> = _peers.asStateFlow()

    // peer wire id -> "send this text" (returns false if the socket is dead).
    private val senders = ConcurrentHashMap<String, (String) -> Boolean>()

    fun registerPeer(wireId: String, send: (String) -> Boolean) {
        senders[wireId] = send
        _peers.value = senders.keys.toSet()
    }

    fun unregisterPeer(wireId: String) {
        senders.remove(wireId)
        _peers.value = senders.keys.toSet()
    }

    fun onIncoming(wireId: String, text: String) {
        append(Message(wireId, text, outgoing = false))
    }

    /** Fan out to every connected peer. Returns true if at least one accepted. */
    fun send(text: String): Boolean {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return false
        var sent = false
        senders.forEach { (wire, fn) ->
            if (fn(trimmed)) {
                sent = true
                append(Message(wire, trimmed, outgoing = true))
            }
        }
        return sent
    }

    @Synchronized
    private fun append(msg: Message) {
        _messages.value = _messages.value + msg
    }
}
