package com.muninn

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Process-wide bridge between the [Mesh] (owned for the app by `AppGraph`,
 * fed sockets by `MuninnService`) and the Compose UI.
 *
 * Turns mesh events into what the screens show: a list of conversations —
 * one per known peer plus one per group — each with its messages, unread
 * count and delivery ticks. Pure JVM (no `android.*`), so the notification
 * and read-receipt rules are unit-tested in `spec/kotlin-conformance`.
 *
 * Conversation ids: `dm:<wire id>` and `group:<group id hex>`, as the
 * desktop GUI names them.
 */
object ChatRepository {

    /** Delivery state of an outgoing message — mirrors the desktop client. */
    enum class Ack { SENT, ACKED, READ }

    data class Message(
        /** Sender's wire id (ours, for an outgoing message). */
        val peer: String,
        val text: String,
        val outgoing: Boolean,
        /** Milliseconds. From the wire for incoming messages. */
        val timestamp: Long = System.currentTimeMillis(),
        val msgId: ByteArray? = null,
        val ack: Ack = Ack.SENT,
        val conv: String = "",
        /** Who an outgoing message is for, and which of them have it / read it. */
        val recipients: List<String> = emptyList(),
        val acked: Set<String> = emptySet(),
        val read: Set<String> = emptySet(),
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
                    conv == other.conv &&
                    recipients == other.recipients &&
                    acked == other.acked &&
                    read == other.read &&
                    (msgId?.contentEquals(other.msgId ?: ByteArray(0)) ?: (other.msgId == null))
                )

        override fun hashCode(): Int {
            var h = peer.hashCode()
            h = 31 * h + text.hashCode()
            h = 31 * h + outgoing.hashCode()
            h = 31 * h + timestamp.hashCode()
            h = 31 * h + ack.hashCode()
            h = 31 * h + conv.hashCode()
            return 31 * h + (msgId?.contentHashCode() ?: 0)
        }

        /** For a group message: "2/3" delivered. Null for a DM. */
        val deliveredOf: Pair<Int, Int>?
            get() = if (recipients.size > 1) (acked + read).count { it in recipients } to recipients.size else null
    }

    data class Conversation(
        val id: String,
        val title: String,
        val isGroup: Boolean,
        /** The other person, for a DM. */
        val peer: String?,
        val members: List<String>,
        val last: Message?,
        val unread: Int,
    )

    fun dmId(peer: String) = "dm:${peer.uppercase()}"
    fun groupConvId(groupId: ByteArray) = "group:${groupId.toHex()}"

    // --- Wiring ---

    @Volatile private var mesh: Mesh? = null
    @Volatile private var store: HistoryStore? = null
    private val detachedBook = PeerBook()

    /** Shared peer state: the mesh's once attached. */
    val book: PeerBook get() = mesh?.book ?: detachedBook

    val localId: String get() = mesh?.localId ?: ""

    /** Connect to the process's mesh. Loads history; call once per process. */
    @Synchronized
    fun attach(mesh: Mesh, store: HistoryStore) {
        this.mesh = mesh
        this.store = store
        unread.clear()
        val loaded = store.loadHistory().map { row ->
            val m = row.message
            val conv = when {
                !m.groupId.contentEquals(ZERO_GROUP_ID) -> groupConvId(m.groupId)
                row.outgoing -> dmId(row.recipients.firstOrNull() ?: "")
                else -> dmId(m.sender)
            }
            if (!row.outgoing && !row.displayed) unread.getOrPut(conv) { ArrayList() } += m.msgId
            Message(
                peer = m.sender,
                text = m.text,
                outgoing = row.outgoing,
                timestamp = if (m.timestamp > 0) m.timestamp * 1000 else System.currentTimeMillis(),
                msgId = m.msgId,
                ack = ackFor(row.recipients, row.acked, row.read),
                conv = conv,
                recipients = row.recipients,
                acked = row.acked,
                read = row.read,
            )
        }
        _messages.value = loaded.sortedBy { it.timestamp }
        mesh.listener = listener
        refresh()
    }

    // --- State for the UI ---

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    /** Every message, oldest first. Screens filter by [Message.conv]. */
    val messages: StateFlow<List<Message>> = _messages.asStateFlow()

    private val _conversations = MutableStateFlow<List<Conversation>>(emptyList())
    val conversations: StateFlow<List<Conversation>> = _conversations.asStateFlow()

    /** Every Muninn peer we know of, with how reachable it is right now. */
    private val _presence = MutableStateFlow<List<PeerBook.PeerStatus>>(emptyList())
    val presence: StateFlow<List<PeerBook.PeerStatus>> = _presence.asStateFlow()

    /** Wire ids with a live session. */
    private val _connected = MutableStateFlow<Set<String>>(emptySet())
    val connected: StateFlow<Set<String>> = _connected.asStateFlow()

    /**
     * Each arriving message, once. A StateFlow of the whole list would re-emit
     * on every change and notify twice for the same message.
     */
    private val _arrivals = MutableSharedFlow<Message>(extraBufferCapacity = 64)
    val arrivals: SharedFlow<Message> = _arrivals.asSharedFlow()

    /**
     * True while the app is on screen. Notifying someone about a message they
     * are watching arrive is the fastest way to get an app muted.
     */
    @Volatile var uiVisible: Boolean = false

    /** The conversation on screen, if any. */
    @Volatile var openConversation: String? = null

    /** Set by the service so reading a thread also clears its notification. */
    @Volatile var onConversationRead: ((String) -> Unit)? = null

    // Incoming messages not yet shown, by conversation.
    private val unread = HashMap<String, MutableList<ByteArray>>()

    // --- Names ---

    fun displayName(wireId: String): String =
        if (wireId.equals(localId, ignoreCase = true)) "You" else book.displayName(wireId)

    fun title(conv: String): String = when {
        conv.startsWith("dm:") -> displayName(conv.removePrefix("dm:"))
        conv.startsWith("group:") -> groupFor(conv)?.name?.ifEmpty { "Group" } ?: "Group"
        else -> conv
    }

    fun groupFor(conv: String): MeshGroup? {
        if (!conv.startsWith("group:")) return null
        val hex = conv.removePrefix("group:")
        return mesh?.groups()?.firstOrNull { it.key == hex }
    }

    /** Everyone a message in `conv` goes to (never ourselves). */
    fun recipients(conv: String): List<String> = when {
        conv.startsWith("dm:") -> listOf(conv.removePrefix("dm:"))
        else -> groupFor(conv)?.members?.keys?.filter { it != localId }.orEmpty()
    }

    fun isReachable(wireId: String): Boolean = mesh?.isReachable(wireId) == true

    // --- Actions ---

    /**
     * Send `text` into `conv`. Works whether or not anyone is in range: the
     * mesh keeps it and delivers it when a path appears. False only for an
     * empty message or a conversation with nobody in it.
     */
    fun send(conv: String, text: String): Boolean {
        val trimmed = text.trim()
        val m = mesh ?: return false
        if (trimmed.isEmpty()) return false
        val to = recipients(conv)
        if (to.isEmpty()) return false
        val groupId = groupFor(conv)?.id ?: ZERO_GROUP_ID
        val result = try {
            m.sendMessage(groupId, trimmed, to)
        } catch (e: FrameTooLarge) {
            return false
        }
        append(
            Message(
                peer = m.localId,
                text = trimmed,
                outgoing = true,
                timestamp = System.currentTimeMillis(),
                msgId = result.msgId,
                conv = conv,
                recipients = to,
            ),
        )
        return true
    }

    /** Create a group with `members`; returns its conversation id. */
    fun createGroup(name: String, members: List<String>): String? {
        val m = mesh ?: return null
        val group = try {
            m.createGroup(name.trim(), members)
        } catch (e: IllegalArgumentException) {
            return null
        }
        refresh()
        return groupConvId(group.id)
    }

    fun rename(wireId: String, name: String) {
        mesh?.setOverride(wireId, name.trim())
        refresh()
    }

    /**
     * The user is looking at `conv`: send read receipts for everything in it
     * not yet shown, and clear its notification. That is what separates a READ
     * from the ACK the mesh already sent on arrival.
     */
    fun markConversationRead(conv: String) {
        onConversationRead?.invoke(conv)
        val pending = synchronized(this) { unread.remove(conv).orEmpty() }
        if (pending.isEmpty()) return
        pending.forEach { mesh?.sendRead(it) }
        store?.markDisplayed(pending)
        refreshConversations()
    }

    // --- Mesh events ---

    val listener = object : Mesh.Listener {
        override fun onMessage(groupId: ByteArray, sender: String, text: String, msgId: ByteArray, timestamp: Long) {
            val conv = if (groupId.contentEquals(ZERO_GROUP_ID)) dmId(sender) else groupConvId(groupId)
            val message = Message(
                peer = sender,
                text = text,
                outgoing = false,
                timestamp = if (timestamp > 0) timestamp * 1000L else System.currentTimeMillis(),
                msgId = msgId,
                conv = conv,
            )
            synchronized(this@ChatRepository) { unread.getOrPut(conv) { ArrayList() } += msgId }
            append(message)
            _arrivals.tryEmit(message)
            if (uiVisible && openConversation == conv) markConversationRead(conv)
        }

        override fun onAck(msgId: ByteArray, from: String) = advance(msgId) { it.copy(acked = it.acked + from) }

        override fun onRead(msgId: ByteArray, from: String) = advance(msgId) { it.copy(read = it.read + from) }

        override fun onGroup(group: MeshGroup) = refresh()

        override fun onPeerChange(wireId: String, connected: Boolean) = refresh()

        override fun onProfile(wireId: String, name: String) {
            // Re-emit so bubbles labelled with the old name pick up the new one.
            _messages.value = _messages.value.toList()
            refresh()
        }

        override fun onPresence() = refresh()
    }

    /** Recompute presence and the conversation list. Cheap; call freely. */
    fun refresh() {
        val m = mesh
        _connected.value = m?.connectedIds().orEmpty()
        _presence.value = book.muninnStatuses().values
            .filter { it.wireId != localId }
            .sortedWith(compareBy({ it.state.ordinal }, { displayName(it.wireId).lowercase() }))
        refreshConversations()
    }

    private fun refreshConversations() {
        val m = mesh ?: return
        val all = _messages.value
        val lastByConv = HashMap<String, Message>()
        for (msg in all) lastByConv[msg.conv] = msg
        val unreadCounts = synchronized(this) { unread.mapValues { it.value.size } }
        val statuses = book.muninnStatuses()

        val peers = (book.knownPeers() + statuses.keys + lastByConv.keys
            .filter { it.startsWith("dm:") }
            .map { it.removePrefix("dm:") })
            .map { it.uppercase() }
            .filter { it.isNotEmpty() && it != m.localId }
            .toSet()
        val dms = peers.map { p ->
            val id = dmId(p)
            Conversation(id, displayName(p), false, p, listOf(p), lastByConv[id], unreadCounts[id] ?: 0)
        }
        val groups = m.groups().map { g ->
            val id = groupConvId(g.id)
            Conversation(id, g.name.ifEmpty { "Group" }, true, null, g.members.keys.toList(), lastByConv[id], unreadCounts[id] ?: 0)
        }
        _conversations.value = (dms + groups).sortedWith(
            compareByDescending<Conversation> { it.last?.timestamp ?: 0L }
                .thenBy { c -> c.peer?.let { statuses[it]?.state?.ordinal } ?: 0 }
                .thenBy { it.title.lowercase() },
        )
    }

    @Synchronized
    private fun advance(msgId: ByteArray, change: (Message) -> Message) {
        var changed = false
        val next = _messages.value.map { msg ->
            if (msg.outgoing && msg.msgId?.contentEquals(msgId) == true) {
                changed = true
                val updated = change(msg)
                // Never walk a tick backwards: a late ACK must not undo a READ.
                val ack = ackFor(updated.recipients, updated.acked, updated.read)
                updated.copy(ack = if (ack.ordinal > msg.ack.ordinal) ack else msg.ack)
            } else {
                msg
            }
        }
        if (changed) _messages.value = next
    }

    @Synchronized
    private fun append(msg: Message) {
        _messages.value = _messages.value + msg
        refreshConversations()
    }

    /** All recipients read it → READ; all have it → ACKED; else SENT. */
    internal fun ackFor(recipients: List<String>, acked: Set<String>, read: Set<String>): Ack = when {
        recipients.isEmpty() -> Ack.SENT
        read.containsAll(recipients) -> Ack.READ
        (acked + read).containsAll(recipients) -> Ack.ACKED
        else -> Ack.SENT
    }
}
