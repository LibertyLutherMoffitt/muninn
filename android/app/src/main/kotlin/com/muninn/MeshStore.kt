package com.muninn

/**
 * What [Mesh] needs to survive a restart: keys, names, groups, messages and
 * their delivery state. The Kotlin counterpart of the parts of `storage.py`
 * that `peers.py` writes through to.
 *
 * Pure JVM on purpose, like Mesh. The app backs it with SQLite
 * (`SqliteMeshStore`); tests and the headless node use [MemoryMeshStore].
 * Implementations must be thread-safe: every session's receive thread writes.
 */
interface MeshStore {

    // --- Peers ---

    /** Store a key. `direct` = from a handshake, which always wins. */
    fun savePeerKey(wireId: String, pubkey: ByteArray, direct: Boolean)
    fun savePeerName(wireId: String, name: String)
    fun saveOverride(wireId: String, name: String)
    fun saveAlias(transport: String, wireId: String)
    fun loadPeers(): List<StoredPeer>
    fun loadAliases(): Map<String, String>

    // --- Groups ---

    fun saveGroup(group: MeshGroup)
    fun loadGroups(): List<MeshGroup>

    // --- Messages ---

    /** First-wins dedup claim. True only the first time a msg_id is seen. */
    fun claimSeen(msgId: ByteArray): Boolean
    fun releaseSeen(msgId: ByteArray)

    fun saveOutgoing(msg: StoredMessage, recipients: List<String>)
    fun saveIncoming(msg: StoredMessage)
    fun markAcked(msgId: ByteArray, recipient: String)
    fun markRead(msgId: ByteArray, recipient: String)

    /** Our messages with at least one recipient still missing an ACK. */
    fun loadUnacked(): List<PendingMessage>
}

/**
 * What the chat UI needs on top of [MeshStore]: the conversation itself.
 * Implemented by the app's SQLite store and by [MemoryMeshStore].
 */
interface HistoryStore : MeshStore {
    /** Every stored message, oldest first, with its delivery state. */
    fun loadHistory(): List<HistoryRow>

    /** The user has seen these incoming messages; they stop counting as unread. */
    fun markDisplayed(msgIds: List<ByteArray>)
}

class HistoryRow(
    val message: StoredMessage,
    val outgoing: Boolean,
    val recipients: List<String>,
    val acked: Set<String>,
    val read: Set<String>,
    val displayed: Boolean,
)

class StoredPeer(val wireId: String, val pubkey: ByteArray?, val name: String?, val override: String?)

class StoredMessage(
    val msgId: ByteArray,
    val groupId: ByteArray,
    val sender: String,
    val text: String,
    /** Unix seconds, as on the wire. */
    val timestamp: Long,
)

class PendingMessage(val message: StoredMessage, val recipients: List<String>)

class MeshGroup(val id: ByteArray, val name: String, val members: Map<String, ByteArray>) {
    val key: String get() = id.toHex()
}

/** In-memory store: tests, and the headless JVM node. */
class MemoryMeshStore : HistoryStore {
    private val lock = Any()
    private val keys = HashMap<String, ByteArray>()
    private val names = HashMap<String, String>()
    private val overrides = HashMap<String, String>()
    private val aliases = HashMap<String, String>()
    private val groups = LinkedHashMap<String, MeshGroup>()
    private val seen = HashSet<String>()
    private val messages = LinkedHashMap<String, StoredMessage>()
    // msg hex -> recipient -> acked
    private val recipients = HashMap<String, LinkedHashMap<String, Boolean>>()
    val reads = HashMap<String, MutableSet<String>>()
    private val outgoingIds = HashSet<String>()
    private val displayed = HashSet<String>()

    override fun savePeerKey(wireId: String, pubkey: ByteArray, direct: Boolean) {
        synchronized(lock) {
            if (direct) keys[wireId] = pubkey else keys.putIfAbsent(wireId, pubkey)
        }
    }

    override fun savePeerName(wireId: String, name: String) {
        synchronized(lock) { if (name.isEmpty()) names.remove(wireId) else names[wireId] = name }
    }

    override fun saveOverride(wireId: String, name: String) {
        synchronized(lock) { if (name.isEmpty()) overrides.remove(wireId) else overrides[wireId] = name }
    }

    override fun saveAlias(transport: String, wireId: String) {
        synchronized(lock) { aliases[transport] = wireId }
    }

    override fun loadPeers(): List<StoredPeer> = synchronized(lock) {
        (keys.keys + names.keys + overrides.keys).map {
            StoredPeer(it, keys[it], names[it], overrides[it])
        }
    }

    override fun loadAliases(): Map<String, String> = synchronized(lock) { HashMap(aliases) }

    override fun saveGroup(group: MeshGroup) {
        synchronized(lock) { groups[group.key] = group }
    }

    override fun loadGroups(): List<MeshGroup> = synchronized(lock) { groups.values.toList() }

    override fun claimSeen(msgId: ByteArray): Boolean = synchronized(lock) { seen.add(msgId.toHex()) }

    override fun releaseSeen(msgId: ByteArray) {
        synchronized(lock) { seen.remove(msgId.toHex()) }
    }

    override fun saveOutgoing(msg: StoredMessage, recipients: List<String>) {
        synchronized(lock) {
            messages[msg.msgId.toHex()] = msg
            outgoingIds += msg.msgId.toHex()
            this.recipients[msg.msgId.toHex()] =
                LinkedHashMap(recipients.associateWith { false })
        }
    }

    override fun saveIncoming(msg: StoredMessage) {
        synchronized(lock) { messages.putIfAbsent(msg.msgId.toHex(), msg) }
    }

    override fun markAcked(msgId: ByteArray, recipient: String) {
        synchronized(lock) {
            recipients[msgId.toHex()]?.let { if (recipient in it) it[recipient] = true }
        }
    }

    override fun markRead(msgId: ByteArray, recipient: String) {
        synchronized(lock) { reads.getOrPut(msgId.toHex()) { HashSet() }.add(recipient) }
    }

    override fun loadUnacked(): List<PendingMessage> = synchronized(lock) {
        recipients.mapNotNull { (hex, rs) ->
            val waiting = rs.filterValues { !it }.keys.toList()
            val msg = messages[hex]
            if (waiting.isEmpty() || msg == null) null else PendingMessage(msg, waiting)
        }
    }

    override fun loadHistory(): List<HistoryRow> = synchronized(lock) {
        messages.map { (hex, m) ->
            val rs = recipients[hex].orEmpty()
            HistoryRow(
                message = m,
                outgoing = hex in outgoingIds,
                recipients = rs.keys.toList(),
                acked = rs.filterValues { it }.keys,
                read = reads[hex].orEmpty().toSet(),
                displayed = hex in displayed,
            )
        }
    }

    override fun markDisplayed(msgIds: List<ByteArray>) {
        synchronized(lock) { msgIds.forEach { displayed += it.toHex() } }
    }

    fun messages(): List<StoredMessage> = synchronized(lock) { messages.values.toList() }
}
