package com.muninn.core

import java.io.File

/** Persistence used by [Mesh]. Mirrors python/src/muninn/storage.py. */

data class Identity(val privkey: ByteArray, val nodeId: String, var displayName: String, var nameTs: Long)

data class PeerRow(
    val nodeId: String, val pubkey: ByteArray, val name: String, val nameTs: Long,
    val override: String?, val lastSeen: Long, val replacedBy: String? = null,
)

data class GroupRow(val groupId: ByteArray, val name: String, val version: Int, val members: List<String>, val left: Boolean)

class OutboxEntry(val packetId: ByteArray, val dst: String, val body: ByteArray, val ts: Long, val msgId: ByteArray?, val attempts: Int)

data class StoredMessage(
    val msgId: ByteArray,
    val conv: String,
    val sender: String,
    val ts: Long,
    val kind: String, // text | file | system
    val text: String,
    val replyTo: ByteArray? = null,
    val meta: Map<String, Any?> = emptyMap(),
    val edited: Boolean = false,
    val deleted: Boolean = false,
    val status: Map<String, String> = emptyMap(), // recipient -> sent|delivered|read
    val reactions: Map<String, List<String>> = emptyMap(), // emoji -> node ids
    val readLocal: Boolean = false,
) {
    val state: String
        get() = when {
            status.isEmpty() -> "sent"
            status.values.any { it == "sent" } -> "sent"
            status.values.any { it == "delivered" } -> "delivered"
            else -> "read"
        }
    val idHex get() = msgId.hex()
}

class FileRecord(
    val fileId: ByteArray, val msgId: ByteArray, val conv: String, val sender: String,
    val name: String, val mime: String, val size: Long, val sha256: String,
    val chunkSize: Int, val chunks: Int, var path: String, var state: String, var have: ByteArray,
) {
    val haveCount: Int get() = have.sumOf { Integer.bitCount(it.toInt() and 0xff) }
    fun has(i: Int) = (have[i shr 3].toInt() shr (i and 7)) and 1 == 1
    fun mark(i: Int) { have[i shr 3] = (have[i shr 3].toInt() or (1 shl (i and 7))).toByte() }
}

class AppEvent(
    val eventId: ByteArray, val session: String, val app: String, val conv: String,
    val sender: String, val seq: Int, val type: String, val body: Any?, val ts: Long,
)

interface Storage {
    val filesDir: File

    fun getSetting(key: String): String?
    fun setSetting(key: String, value: String)

    fun getIdentity(): Identity?
    fun createIdentity(privkey: ByteArray, name: String): Identity
    fun setDisplayName(name: String, nameTs: Long)

    fun upsertPeer(nodeId: String, pubkey: ByteArray, name: String, nameTs: Long)
    fun setPeerOverride(nodeId: String, name: String?)
    fun touchPeer(nodeId: String)
    fun loadPeers(): List<PeerRow>
    /** `old` reinstalled as `new`: move history/receipts/reactions/groups; returns old's outbox (removed). */
    fun migratePeer(old: String, new: String): List<OutboxEntry>

    fun saveTransport(nodeId: String, addr: String)
    fun loadTransports(): List<Triple<String, String, Long>> // node, addr, lastOk (newest first)

    fun saveGroup(groupId: ByteArray, name: String, version: Int, members: List<String>, left: Boolean)
    fun loadGroups(): List<GroupRow>

    fun saveMessage(m: StoredMessage, recipients: List<String> = emptyList()): Boolean
    fun markDelivered(msgId: ByteArray, nodeId: String): Boolean
    fun markRead(msgId: ByteArray, nodeId: String): Boolean
    fun markReadLocal(conv: String): List<Pair<ByteArray, String>>
    fun unreadCounts(): Map<String, Int>
    fun editMessage(msgId: ByteArray, sender: String, text: String): Boolean
    fun deleteMessage(msgId: ByteArray, sender: String): Boolean
    fun setReaction(msgId: ByteArray, nodeId: String, emoji: String, on: Boolean)
    fun getMessage(msgId: ByteArray): StoredMessage?
    fun loadHistory(conv: String, limit: Int = 200): List<StoredMessage>
    fun lastMessages(): Map<String, StoredMessage>
    fun search(query: String, limit: Int = 50): List<StoredMessage>

    fun outboxAdd(e: OutboxEntry)
    fun outboxAck(packetId: ByteArray): Pair<String, ByteArray?>?
    fun outboxLoad(): List<OutboxEntry>

    fun claimSeen(packetId: ByteArray): Boolean

    fun relayPut(packetId: ByteArray, dst: String, frame: ByteArray)
    fun relayTake(dst: String): List<ByteArray>
    fun relayExpire(maxAgeSec: Long, maxRows: Int)

    fun saveFile(f: FileRecord)
    fun loadFiles(): List<FileRecord>

    fun saveAppEvent(ev: AppEvent): Boolean
    fun loadAppEvents(session: String): List<AppEvent>
    fun listAppSessions(conv: String?): List<String> // newest first
}

/** In-memory storage: JVM sim nodes and unit tests. */
class MemStorage(override val filesDir: File) : Storage {
    private val lock = Any()
    private var identity: Identity? = null
    private val peers = LinkedHashMap<String, PeerRow>()
    private val transports = LinkedHashMap<Pair<String, String>, Long>()
    private val groups = LinkedHashMap<String, GroupRow>()
    private val messages = LinkedHashMap<String, StoredMessage>()
    private val recipients = HashMap<String, MutableMap<String, String>>()
    private val reactions = HashMap<String, MutableList<Pair<String, String>>>()
    private val outbox = LinkedHashMap<String, OutboxEntry>()
    private val seen = HashSet<String>()
    private val relay = LinkedHashMap<String, Triple<String, ByteArray, Long>>()
    private val files = LinkedHashMap<String, FileRecord>()
    private val appEvents = LinkedHashMap<String, AppEvent>()

    private val settings = HashMap<String, String>()

    init { filesDir.mkdirs() }

    override fun getSetting(key: String) = synchronized(lock) { settings[key] }
    override fun setSetting(key: String, value: String) = synchronized(lock) { settings[key] = value }

    override fun getIdentity() = synchronized(lock) { identity }
    override fun createIdentity(privkey: ByteArray, name: String) = synchronized(lock) {
        Identity(privkey, P.randomNodeId(), name, if (name.isNotEmpty()) P.now() else 0).also { identity = it }
    }
    override fun setDisplayName(name: String, nameTs: Long) = synchronized(lock) {
        identity?.displayName = name; identity?.nameTs = nameTs
    }

    override fun upsertPeer(nodeId: String, pubkey: ByteArray, name: String, nameTs: Long) = synchronized(lock) {
        val old = peers[nodeId]
        peers[nodeId] = PeerRow(nodeId, old?.pubkey ?: pubkey, name, nameTs, old?.override, old?.lastSeen ?: P.now())
    }
    override fun setPeerOverride(nodeId: String, name: String?) = synchronized(lock) {
        peers[nodeId]?.let { peers[nodeId] = it.copy(override = name?.ifEmpty { null }) }; Unit
    }
    override fun touchPeer(nodeId: String) = synchronized(lock) {
        peers[nodeId]?.let { peers[nodeId] = it.copy(lastSeen = P.now()) }; Unit
    }
    override fun loadPeers() = synchronized(lock) { peers.values.toList() }

    override fun migratePeer(old: String, new: String): List<OutboxEntry> = synchronized(lock) {
        for ((k, m) in messages.entries.toList()) {
            var x = m
            if (x.conv == "dm:$old") x = x.copy(conv = "dm:$new")
            if (x.sender == old) x = x.copy(sender = new)
            messages[k] = x
        }
        recipients.values.forEach { r -> r.remove(old)?.let { r.putIfAbsent(new, it) } }
        reactions.values.forEach { l -> l.replaceAll { if (it.first == old) new to it.second else it } }
        for ((k, g) in groups) if (old in g.members) groups[k] = g.copy(members = g.members.map { if (it == old) new else it }.distinct())
        transports.keys.removeIf { it.first == old }
        peers[old]?.let { peers[old] = it.copy(replacedBy = new) }
        val out = outbox.values.filter { it.dst == old }
        outbox.values.removeIf { it.dst == old }
        out
    }

    override fun saveTransport(nodeId: String, addr: String) = synchronized(lock) { transports[nodeId to addr] = P.now() }
    override fun loadTransports() = synchronized(lock) {
        transports.entries.sortedByDescending { it.value }.map { Triple(it.key.first, it.key.second, it.value) }
    }

    override fun saveGroup(groupId: ByteArray, name: String, version: Int, members: List<String>, left: Boolean) =
        synchronized(lock) { groups[groupId.hex()] = GroupRow(groupId, name, version, members, left) }
    override fun loadGroups() = synchronized(lock) { groups.values.toList() }

    private fun hydrate(m: StoredMessage): StoredMessage {
        val st = recipients[m.idHex] ?: emptyMap<String, String>()
        val re = LinkedHashMap<String, MutableList<String>>()
        reactions[m.idHex]?.forEach { (node, e) -> re.getOrPut(e) { ArrayList() }.add(node) }
        return m.copy(status = HashMap(st), reactions = re)
    }

    override fun saveMessage(m: StoredMessage, recipients: List<String>) = synchronized(lock) {
        if (messages.containsKey(m.idHex)) return@synchronized false
        messages[m.idHex] = m.copy(readLocal = recipients.isNotEmpty())
        if (recipients.isNotEmpty()) this.recipients[m.idHex] = recipients.associateWith { "sent" }.toMutableMap()
        true
    }
    override fun markDelivered(msgId: ByteArray, nodeId: String) = synchronized(lock) {
        val r = recipients[msgId.hex()] ?: return@synchronized false
        if (r[nodeId] == "sent") { r[nodeId] = "delivered"; true } else false
    }
    override fun markRead(msgId: ByteArray, nodeId: String) = synchronized(lock) {
        val r = recipients[msgId.hex()] ?: return@synchronized false
        if (r.containsKey(nodeId) && r[nodeId] != "read") { r[nodeId] = "read"; true } else false
    }
    override fun markReadLocal(conv: String) = synchronized(lock) {
        val out = ArrayList<Pair<ByteArray, String>>()
        for ((k, m) in messages) {
            if (m.conv == conv && !m.readLocal) {
                if (m.kind == "text" || m.kind == "file") out.add(m.msgId to m.sender)
                messages[k] = m.copy(readLocal = true)
            }
        }
        out
    }
    override fun unreadCounts() = synchronized(lock) {
        messages.values.filter { !it.readLocal && (it.kind == "text" || it.kind == "file") }
            .groupingBy { it.conv }.eachCount()
    }
    override fun editMessage(msgId: ByteArray, sender: String, text: String) = synchronized(lock) {
        val m = messages[msgId.hex()] ?: return@synchronized false
        if (m.sender != sender || m.deleted) return@synchronized false
        messages[msgId.hex()] = m.copy(text = text, edited = true); true
    }
    override fun deleteMessage(msgId: ByteArray, sender: String) = synchronized(lock) {
        val m = messages[msgId.hex()] ?: return@synchronized false
        if (m.sender != sender) return@synchronized false
        messages[msgId.hex()] = m.copy(text = "", deleted = true); true
    }
    override fun setReaction(msgId: ByteArray, nodeId: String, emoji: String, on: Boolean) = synchronized(lock) {
        val l = reactions.getOrPut(msgId.hex()) { ArrayList() }
        val e = nodeId to emoji
        if (on) { if (e !in l) l.add(e) } else l.remove(e); Unit
    }
    override fun getMessage(msgId: ByteArray) = synchronized(lock) { messages[msgId.hex()]?.let(::hydrate) }
    override fun loadHistory(conv: String, limit: Int) = synchronized(lock) {
        messages.values.filter { it.conv == conv }.takeLast(limit).sortedBy { it.ts }.map(::hydrate)
    }
    override fun search(query: String, limit: Int) = synchronized(lock) {
        messages.values.filter { !it.deleted && (it.kind == "text" || it.kind == "file") &&
            (it.text.contains(query, true) || (it.meta["n"] as? String)?.contains(query, true) == true) }
            .sortedByDescending { it.ts }.take(limit).map(::hydrate)
    }

    override fun lastMessages() = synchronized(lock) {
        val out = LinkedHashMap<String, StoredMessage>()
        for (m in messages.values) out[m.conv] = m
        out.mapValues { hydrate(it.value) }
    }

    override fun outboxAdd(e: OutboxEntry) = synchronized(lock) { outbox.putIfAbsent(e.packetId.hex(), e); Unit }
    override fun outboxAck(packetId: ByteArray) = synchronized(lock) {
        outbox.remove(packetId.hex())?.let { it.dst to it.msgId }
    }
    override fun outboxLoad() = synchronized(lock) { outbox.values.toList() }

    override fun claimSeen(packetId: ByteArray) = synchronized(lock) { seen.add(packetId.hex()) }

    override fun relayPut(packetId: ByteArray, dst: String, frame: ByteArray) = synchronized(lock) {
        relay.putIfAbsent(packetId.hex(), Triple(dst, frame, P.now())); Unit
    }
    override fun relayTake(dst: String) = synchronized(lock) {
        val keys = relay.filter { it.value.first == dst }.keys.toList()
        keys.map { relay.remove(it)!!.second }
    }
    override fun relayExpire(maxAgeSec: Long, maxRows: Int) = synchronized(lock) {
        val cutoff = P.now() - maxAgeSec
        relay.entries.removeIf { it.value.third < cutoff }
        while (relay.size > maxRows) relay.remove(relay.keys.first())
    }

    override fun saveFile(f: FileRecord) = synchronized(lock) { files[f.fileId.hex()] = f }
    override fun loadFiles() = synchronized(lock) { files.values.toList() }

    override fun saveAppEvent(ev: AppEvent) = synchronized(lock) {
        if (appEvents.containsKey(ev.eventId.hex())) false else { appEvents[ev.eventId.hex()] = ev; true }
    }
    override fun loadAppEvents(session: String) = synchronized(lock) {
        appEvents.values.filter { it.session == session }.sortedWith(compareBy({ it.seq }, { it.ts }))
    }
    override fun listAppSessions(conv: String?) = synchronized(lock) {
        appEvents.values.filter { conv == null || it.conv == conv }
            .groupBy { it.session }.entries.sortedByDescending { e -> e.value.maxOf { it.ts } }.map { it.key }
    }
}
