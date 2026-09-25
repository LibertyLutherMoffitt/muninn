package com.muninn

import java.io.DataInputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.Timer
import kotlin.concurrent.schedule

/**
 * Every live session, and how frames get between them — the Kotlin twin of
 * `peers.py:ConnectionManager`, rule for rule.
 *
 * Like Protocol.kt and PeerBook.kt this file imports nothing from `android.*`,
 * so `spec/kotlin-conformance` compiles it on a plain JVM, unit-tests it, and
 * runs it as a headless node in meshes with the Python client. Keep it that
 * way: relaying is where two implementations drift most easily, and the only
 * defence is running them against each other.
 *
 * **Routing** (PROTOCOL.md, "Relay & Routing"): a live session if we have
 * one; otherwise the neighbour advertising the fewest hops (ROUTES);
 * otherwise every neighbour, keeping a copy for the destination in case it
 * turns up here first. **Delivery**: the sender keeps each message until its
 * ACK arrives and resends on reconnect, when a relay path appears, and every
 * [Tuning.retryIntervalMs] while one exists.
 *
 * Threading: each session has its own receive thread. All routing state is
 * guarded by one lock, which is never held while writing to a socket; each
 * session serializes its own writes.
 */
class Mesh(
    localId: String,
    private val pubkey: ByteArray,
    private val sealer: Sealer,
    private val store: MeshStore,
    val book: PeerBook = PeerBook(),
    private val tuning: Tuning = Tuning(),
    private val clock: () -> Long = System::currentTimeMillis,
    private val log: (String) -> Unit = {},
) {
    /** Timings, in milliseconds. The defaults match `peers.py`. */
    data class Tuning(
        val relayDedupMs: Long = 10_000,
        val receiptDedupMs: Long = 5_000,
        val retryIntervalMs: Long = 30_000,
        val duplicateSessionMs: Long = 10_000,
        val handshakeTimeoutMs: Long = 15_000,
        val relayQueuePerDest: Int = 200,
        val relayQueueTtlMs: Long = 12 * 3_600_000L,
    )

    /** Events for the UI. Called on session threads; never while holding a lock. */
    interface Listener {
        fun onMessage(groupId: ByteArray, sender: String, text: String, msgId: ByteArray, timestamp: Long) {}
        fun onAck(msgId: ByteArray, from: String) {}
        fun onRead(msgId: ByteArray, from: String) {}
        fun onGroup(group: MeshGroup) {}
        fun onPeerChange(wireId: String, connected: Boolean) {}
        fun onProfile(wireId: String, name: String) {}
        /** Anything a presence display depends on may have changed. */
        fun onPresence() {}
    }

    class SendResult(val msgId: ByteArray, val sent: List<String>, val skipped: List<String>)

    @Volatile var listener: Listener = object : Listener {}

    val localId: String = localId.uppercase()
    private val localBytes = macToBytes(this.localId)

    @Volatile var displayName: String = ""
        private set

    // --- State (guarded by `lock`) ---

    private val lock = Any()
    private val sessions = HashMap<String, Session>()
    private val transportToId = HashMap<String, String>()
    private val groups = LinkedHashMap<String, MeshGroup>()

    private class Outgoing(
        val groupId: ByteArray,
        val text: String,
        val timestamp: Long,
        val pending: MutableSet<String>,
    )

    private val outgoing = LinkedHashMap<String, Outgoing>()
    private val unacked = LinkedHashMap<String, LinkedHashMap<String, ByteArray>>()
    private val msgIds = HashMap<String, ByteArray>()
    private val sentIds = HashSet<String>()
    private val lastAttempt = HashMap<String, Long>()
    private val groupConfirmed = HashSet<String>()
    private val incomingSender = HashMap<String, String>()
    private val readsOwed = HashMap<String, MutableList<ByteArray>>()
    private val relayedSender = LinkedHashMap<String, String>()

    private class Held(val key: String, val frame: ByteArray, val at: Long)

    private val relayQueue = HashMap<String, MutableList<Held>>()
    private val routesFrom = HashMap<String, Map<String, Int>>()
    @Volatile private var indirectVia: Map<String, String> = emptyMap()
    @Volatile private var hopsTo: Map<String, Int> = emptyMap()
    private val routesSent = HashMap<String, List<Pair<String, Int>>>()
    // JVM monitors are re-entrant: a failed send while advertising removes the
    // session, which re-advertises on the same thread.
    private val advertiseLock = Any()

    private val seenRelayed = Recent(tuning.relayDedupMs, clock)
    private val seenAcks = Recent(tuning.receiptDedupMs, clock)
    private val seenReads = Recent(tuning.receiptDedupMs, clock)
    private val seenSetups = Recent(tuning.receiptDedupMs, clock)
    private val receiptEdges = Recent(tuning.receiptDedupMs, clock)
    private val watchdog = Timer("mesh-handshake-watchdog", true)

    private inner class Session(
        val id: String,
        val link: MeshLink,
        val outbound: Boolean?,
        val connectedAt: Long,
    ) {
        private val writeLock = Any()

        /** False until our introductions are out; see PeerState.introduced. */
        @Volatile var introduced = false
        @Volatile var stopped = false

        fun write(frame: ByteArray): Boolean = try {
            synchronized(writeLock) {
                link.output.write(frame)
                link.output.flush()
            }
            true
        } catch (e: IOException) {
            false
        }
    }

    init {
        for (p in store.loadPeers()) {
            p.pubkey?.let { book.learnRelayed(p.wireId, it) }
            p.name?.let { book.setSelfChosenName(p.wireId, it) }
            p.override?.let { book.setOverride(p.wireId, it) }
        }
        book.noteKnown(book.knownPeers().filter { it != this.localId })
        for ((transport, id) in store.loadAliases()) book.alias(transport, id)
        for (g in store.loadGroups()) groups[g.key] = g
        for (pending in store.loadUnacked()) {
            val m = pending.message
            val hex = m.msgId.toHex()
            val out = Outgoing(m.groupId, m.text, m.timestamp, pending.recipients.toMutableSet())
            outgoing[hex] = out
            sentIds += hex
            msgIds[hex] = m.msgId
            val frames = LinkedHashMap<String, ByteArray>()
            for (r in pending.recipients) buildFrame(m.msgId, out, r)?.let { frames[r] = it }
            if (frames.isNotEmpty()) unacked[hex] = frames
        }
    }

    // --- Sessions ---

    /**
     * Handshake over `link` and, if it succeeds, run the session on its own
     * thread. Blocks for the handshake only. `outbound` is true when we
     * dialled, false when we accepted: it feeds the crossed-dial tiebreak.
     *
     * Returns true when we end up with a live session to this peer —
     * including when this link lost the tiebreak to one already up.
     */
    fun attach(link: MeshLink, transport: String, outbound: Boolean?): Boolean {
        val input = DataInputStream(link.input)
        val timeout = watchdog.schedule(tuning.handshakeTimeoutMs) { runCatching { link.close() } }
        val (peerKey, wireId) = try {
            link.output.write(handshakeFrame(pubkey, localBytes))
            link.output.flush()
            val frame = readFrame(input)
            if (frame.type != TYPE_HANDSHAKE) throw MalformedFrame("expected handshake")
            decodeHandshake(frame.payload)
        } catch (e: Exception) {
            log("handshake with $transport failed: ${e.message}")
            runCatching { link.close() }
            return false
        } finally {
            timeout.cancel()
        }

        val transportId = transport.uppercase()
        val id = wireId?.let(::bytesToMac)
            ?: runCatching { bytesToMac(macToBytes(transportId)) }.getOrNull()
            ?: run {
                runCatching { link.close() }
                return false
            }
        if (id == localId) {
            runCatching { link.close() }
            return false
        }

        val previous = book.pubkey(id)
        book.learnDirect(id, peerKey)
        store.savePeerKey(id, peerKey, direct = true)
        if (previous != null && !previous.contentEquals(peerKey)) resealFor(id)

        val session = Session(id, link, outbound, clock())
        val old: Session?
        synchronized(lock) {
            old = sessions[id]
            if (old != null && !shouldReplace(old, session)) {
                runCatching { link.close() }
                return true
            }
            sessions[id] = session
            if (transportId != id) transportToId[transportId] = id
        }
        if (transportId != id) {
            book.alias(transportId, id)
            store.saveAlias(transportId, id)
        }
        old?.let {
            it.stopped = true
            runCatching { it.link.close() }
        }

        Thread({ receiveLoop(session, input) }, "mesh-rx-$id").apply {
            isDaemon = true
            start()
        }

        // Introductions first; only then does the session carry routed
        // traffic, so nobody is relayed a message whose sender's key has not
        // reached them yet.
        if (displayName.isNotEmpty()) session.write(profileFrame(displayName))
        sendPeerAnnc(session)
        announceNewcomer(id)
        val held = synchronized(lock) {
            session.introduced = true
            relayQueue.remove(id).orEmpty()
        }

        book.recordConnected(id, clock())
        onTopologyChange()
        sendSharedGroupSetups(id)
        listener.onPeerChange(id, true)
        listener.onPresence()

        for (h in held) sendTo(id, h.frame)
        resendTo(id, force = true)
        flushReads(id)
        return true
    }

    /** Crossed-dial tiebreak; see PROTOCOL.md. Both ends reach the same answer. */
    private fun shouldReplace(old: Session, new: Session): Boolean {
        if (old.stopped) return true
        if (old.outbound == null || new.outbound == null || old.outbound == new.outbound) return true
        if (new.connectedAt - old.connectedAt > tuning.duplicateSessionMs) return true
        val localIsLower = compareIds(localBytes, macToBytes(new.id)) < 0
        return new.outbound == localIsLower
    }

    private fun removeSession(session: Session) {
        session.stopped = true
        runCatching { session.link.close() }
        val removed = synchronized(lock) {
            if (sessions[session.id] !== session) return@synchronized false
            sessions.remove(session.id)
            routesFrom.remove(session.id)
            routesSent.remove(session.id)
            transportToId.entries.removeAll { it.value == session.id }
            true
        }
        if (!removed) return
        book.recordDisconnected(session.id, clock())
        listener.onPeerChange(session.id, false)
        onTopologyChange()
    }

    /**
     * Drop every live session — Bluetooth went off. Everything owed stays
     * queued and goes out when sessions come back.
     */
    fun disconnectAll() {
        val all = synchronized(lock) { sessions.values.toList() }
        all.forEach(::removeSession)
    }

    /** Shut down for good: no further [attach] will work. */
    fun close() {
        disconnectAll()
        watchdog.cancel()
    }

    private fun sendTo(id: String, frame: ByteArray): Boolean {
        val session = synchronized(lock) { sessions[id] } ?: return false
        if (session.write(frame)) return true
        removeSession(session)
        return false
    }

    private fun routable(id: String): Boolean = synchronized(lock) { sessions[id]?.introduced == true }

    // --- Queries ---

    fun connectedIds(): Set<String> = synchronized(lock) { sessions.keys.toSet() }

    /** True if a live session owns this radio address (or wire id). */
    fun isConnectedTransport(address: String): Boolean {
        val a = address.uppercase()
        return synchronized(lock) { a in sessions || transportToId[a]?.let { it in sessions } == true }
    }

    /** A message sent now has a path: a session or a relay route. */
    fun isReachable(id: String): Boolean {
        val u = id.uppercase()
        return synchronized(lock) { u in sessions } || u in indirectVia
    }

    fun routeVia(id: String): String? = indirectVia[id.uppercase()]

    fun groups(): List<MeshGroup> = synchronized(lock) { groups.values.toList() }

    fun group(id: ByteArray): MeshGroup? = synchronized(lock) { groups[id.toHex()] }

    /** How many (message, recipient) deliveries are still waiting for an ACK. */
    fun pendingDeliveries(): Int = synchronized(lock) { outgoing.values.sumOf { it.pending.size } }

    fun heldFor(id: String): Int = synchronized(lock) { relayQueue[id.uppercase()]?.size ?: 0 }

    // --- Sending ---

    fun setDisplayName(name: String) {
        displayName = name
        val frame = profileFrame(name)
        for (id in connectedIds()) sendTo(id, frame)
    }

    fun setOverride(id: String, name: String) {
        book.setOverride(id, name)
        store.saveOverride(id.uppercase(), name)
        listener.onProfile(id.uppercase(), book.displayName(id))
    }

    /**
     * Seal and send `text` to each of `dests`. A recipient without a key yet
     * is kept pending and sent as soon as one arrives. Throws [FrameTooLarge].
     */
    fun sendMessage(groupId: ByteArray, text: String, dests: List<String>): SendResult {
        val body = text.toByteArray(Charsets.UTF_8)
        val payloadSize = MESSAGE_HEADER_BYTES + NONCE_BYTES + 16 + body.size
        if (payloadSize > MAX_PAYLOAD) throw FrameTooLarge(payloadSize)

        val msgId = newMsgId()
        val hex = msgId.toHex()
        val recipients = dests.map { it.uppercase() }.filter { it != localId }.distinct()
        val out = Outgoing(groupId, text, clock() / 1000L, recipients.toMutableSet())
        val frames = LinkedHashMap<String, ByteArray>()
        val skipped = ArrayList<String>()
        for (r in recipients) {
            val frame = buildFrame(msgId, out, r)
            if (frame == null) skipped += r else frames[r] = frame
        }
        if (recipients.isNotEmpty()) {
            store.saveOutgoing(StoredMessage(msgId, groupId, localId, text, out.timestamp), recipients)
        }
        synchronized(lock) {
            sentIds += hex
            msgIds[hex] = msgId
            if (recipients.isNotEmpty()) outgoing[hex] = out
            if (frames.isNotEmpty()) unacked[hex] = frames
        }
        // A copy: a fast ACK for the first recipient mutates `frames`.
        for ((dest, frame) in frames.entries.toList()) deliver(msgId, dest, frame)
        return SendResult(msgId, frames.keys.toList(), skipped)
    }

    private fun buildFrame(msgId: ByteArray, out: Outgoing, dest: String): ByteArray? {
        val key = book.pubkey(dest) ?: return null
        val destBytes = runCatching { macToBytes(dest) }.getOrNull() ?: return null
        val sealed = sealer.seal(out.text.toByteArray(Charsets.UTF_8), key)
        return messageFrame(out.groupId, msgId, localBytes, destBytes, sealed, out.timestamp)
    }

    private fun resealFor(id: String) {
        synchronized(lock) {
            for ((hex, out) in outgoing) {
                if (id !in out.pending) continue
                val msgId = msgIds[hex] ?: continue
                buildFrame(msgId, out, id)?.let { unacked.getOrPut(hex) { LinkedHashMap() }[id] = it }
            }
        }
    }

    private fun deliver(msgId: ByteArray, dest: String, frame: ByteArray) {
        val hex = msgId.toHex()
        val setup = synchronized(lock) {
            lastAttempt["$hex/$dest"] = clock()
            val out = outgoing[hex]
            if (out == null || out.groupId.contentEquals(ZERO_GROUP_ID)) {
                null
            } else if ("${out.groupId.toHex()}/$dest" in groupConfirmed) {
                null
            } else {
                groups[out.groupId.toHex()]
            }
        }
        // Get the group there ahead of its first message, over the same path.
        setup?.let { routeFrame(dest, groupSetupFrame(it)) }
        routeFrame(dest, frame, hold = false)
    }

    private fun resendTo(id: String, force: Boolean) {
        val now = clock()
        val due = synchronized(lock) {
            unacked.mapNotNull { (hex, frames) ->
                val frame = frames[id] ?: return@mapNotNull null
                val last = lastAttempt["$hex/$id"] ?: 0L
                if (!force && now - last < tuning.retryIntervalMs) return@mapNotNull null
                msgIds[hex]?.let { it to frame }
            }
        }
        for ((msgId, frame) in due) deliver(msgId, id, frame)
    }

    fun createGroup(name: String, members: List<String>): MeshGroup {
        val all = LinkedHashMap<String, ByteArray>()
        all[localId] = pubkey
        for (m in members.map { it.uppercase() }) {
            all[m] = book.pubkey(m) ?: throw IllegalArgumentException("no key for $m yet")
        }
        val group = MeshGroup(newMsgId(), name, all)
        synchronized(lock) { groups[group.key] = group }
        store.saveGroup(group)
        val frame = groupSetupFrame(group)
        for (m in all.keys) if (m != localId) routeFrame(m, frame)
        return group
    }

    private fun groupSetupFrame(group: MeshGroup): ByteArray =
        groupSetupFrame(
            group.id,
            group.members.map { (id, key) -> GroupMember(macToBytes(id), key) },
            group.name,
        )

    private fun sendSharedGroupSetups(id: String) {
        val shared = synchronized(lock) {
            groups.values.filter { id in it.members && localId in it.members }
        }
        for (g in shared) sendTo(id, groupSetupFrame(g))
    }

    /** Tell the sender their message was shown. Held if they have no path now. */
    fun sendRead(msgId: ByteArray) {
        val hex = msgId.toHex()
        val frame = readReceiptFrame(msgId, localBytes)
        seenReads.add("$hex/$localId")
        val sender = synchronized(lock) { incomingSender[hex] }
        if (sender != null && !isReachable(sender)) {
            synchronized(lock) { readsOwed.getOrPut(sender) { ArrayList() }.add(msgId) }
            return
        }
        val targets = synchronized(lock) {
            if (sender != null && sender in sessions) listOf(sender) else sessions.keys.toList()
        }
        for (t in targets) sendTo(t, frame)
    }

    private fun flushReads(id: String) {
        val owed = synchronized(lock) { readsOwed.remove(id).orEmpty() }
        owed.forEach(::sendRead)
    }

    // --- Routing ---

    private fun nextHops(dest: String, exclude: String? = null): List<String> =
        synchronized(lock) {
            routesFrom.mapNotNull { (n, table) ->
                if (n == dest || n == exclude) null else table[dest]?.let { it to n }
            }
        }.sortedWith(compareBy({ it.first }, { it.second })).map { it.second }

    private fun routeFrame(
        dest: String,
        frame: ByteArray,
        exclude: String? = null,
        hold: Boolean = true,
        key: String? = null,
    ) {
        if (routable(dest) && sendTo(dest, frame)) return
        for (hop in nextHops(dest, exclude)) if (sendTo(hop, frame)) return
        // No known path: a copy to every neighbour (each forwards once per
        // dedup window), and one kept here in case the destination shows up.
        val neighbours = synchronized(lock) {
            sessions.values.filter { it.introduced && it.id != dest && it.id != exclude }.map { it.id }
        }
        for (n in neighbours) sendTo(n, frame)
        if (hold) hold(dest, frame, key)
    }

    private fun hold(dest: String, frame: ByteArray, key: String?) {
        val k = key ?: frame.toHex()
        val connectedNow = synchronized(lock) {
            if (sessions[dest]?.introduced == true) {
                true
            } else {
                val held = relayQueue.getOrPut(dest) { ArrayList() }
                if (held.none { it.key == k }) {
                    held += Held(k, frame, clock())
                    while (held.size > tuning.relayQueuePerDest) held.removeAt(0)
                }
                false
            }
        }
        if (connectedNow) sendTo(dest, frame)
    }

    private fun dropHeld(dest: String, key: String) {
        synchronized(lock) {
            val held = relayQueue[dest] ?: return
            held.removeAll { it.key == key }
            if (held.isEmpty()) relayQueue.remove(dest)
        }
    }

    private fun handOffHeld(dest: String) {
        val hops = nextHops(dest)
        if (hops.isEmpty()) return
        val held = synchronized(lock) { relayQueue.remove(dest).orEmpty() }
        for (h in held) {
            if (hops.none { sendTo(it, h.frame) }) hold(dest, h.frame, h.key)
        }
    }

    private fun onTopologyChange() {
        val old: Map<String, String>
        val now: Map<String, String>
        val direct: Set<String>
        synchronized(lock) {
            direct = sessions.values.filter { it.introduced }.map { it.id }.toSet()
            val best = HashMap<String, Pair<Int, String>>()
            for ((n, table) in routesFrom) {
                if (n !in direct) continue
                for ((d, h) in table) {
                    if (d == localId || d in direct) continue
                    val candidate = (h + 1) to n
                    val current = best[d]
                    if (current == null || candidate.first < current.first ||
                        (candidate.first == current.first && candidate.second < current.second)
                    ) {
                        best[d] = candidate
                    }
                }
            }
            old = indirectVia
            hopsTo = best.mapValues { it.value.first }
            indirectVia = best.mapValues { it.value.second }
            now = indirectVia
        }
        val hops = hopsTo
        for ((d, via) in now) book.recordRelay(d, via, hops[d])
        for (d in old.keys) if (d !in now && d !in direct) book.clearRelay(d, clock())

        advertiseRoutes()

        for (d in now.keys) {
            if (d !in old) {
                resendTo(d, force = true)
                handOffHeld(d)
                flushReads(d)
            }
        }
        listener.onPresence()
    }

    /** Caller holds `lock`. Split horizon: never advertise a route back to its next hop. */
    private fun routesFor(neighbour: String, direct: Set<String>): List<Route> {
        val out = ArrayList<Route>()
        for (d in direct.sorted()) if (d != neighbour) out += Route(macToBytes(d), 1)
        for ((d, via) in indirectVia.toSortedMap()) {
            val h = hopsTo[d] ?: continue
            if (via == neighbour || d == neighbour || h > MAX_ROUTE_HOPS) continue
            out += Route(macToBytes(d), h)
        }
        return out.take(255)
    }

    private fun advertiseRoutes() {
        synchronized(advertiseLock) {
            val pending = synchronized(lock) {
                val direct = sessions.values.filter { it.introduced }.map { it.id }.toSortedSet()
                direct.mapNotNull { n ->
                    val routes = routesFor(n, direct)
                    val signature = routes.map { bytesToMac(it.wireId) to it.hops }
                    if (routesSent[n] == signature) null else Triple(n, routes, signature)
                }
            }
            for ((n, routes, signature) in pending) {
                if (sendTo(n, routesFrame(routes))) synchronized(lock) { routesSent[n] = signature }
            }
        }
    }

    // --- Maintenance ---

    /** Periodic housekeeping; call every few seconds. See `peers.py:maintain`. */
    fun maintain() {
        // Recipients whose key has arrived since we sent.
        val building = synchronized(lock) {
            outgoing.mapNotNull { (hex, out) ->
                val have = unacked[hex]?.keys.orEmpty()
                val missing = out.pending - have
                if (missing.isEmpty()) null else Triple(hex, out, missing)
            }
        }
        for ((hex, out, missing) in building) {
            val msgId = synchronized(lock) { msgIds[hex] } ?: continue
            for (dest in missing) {
                val frame = buildFrame(msgId, out, dest) ?: continue
                synchronized(lock) { unacked.getOrPut(hex) { LinkedHashMap() }[dest] = frame }
                deliver(msgId, dest, frame)
            }
        }
        for (d in indirectVia.keys) resendTo(d, force = false)

        val now = clock()
        synchronized(lock) {
            val it = relayQueue.entries.iterator()
            while (it.hasNext()) {
                val e = it.next()
                e.value.removeAll { now - it.at >= tuning.relayQueueTtlMs }
                if (e.value.isEmpty()) it.remove()
            }
        }
        listOf(seenRelayed, seenAcks, seenReads, seenSetups, receiptEdges).forEach { it.prune() }
    }

    // --- Receiving ---

    private fun receiveLoop(session: Session, input: DataInputStream) {
        try {
            while (!session.stopped) {
                val frame = readFrame(input)
                try {
                    dispatch(session.id, frame)
                } catch (e: IOException) {
                    throw e
                } catch (e: Exception) {
                    // One bad frame costs that frame, never the session.
                    log("dropped frame 0x%02x from ${session.id}: ${e.message}".format(frame.type))
                }
            }
        } catch (e: IOException) {
            // closed
        } catch (e: Throwable) {
            log("session ${session.id} failed: $e")
        } finally {
            removeSession(session)
        }
    }

    private fun dispatch(from: String, frame: Frame) {
        when (frame.type) {
            TYPE_MESSAGE -> handleMessage(from, frame.payload)
            TYPE_ACK -> handleAck(from, frame.payload)
            TYPE_READ -> handleRead(from, frame.payload)
            TYPE_GROUP_SETUP -> handleGroupSetup(from, frame.payload)
            TYPE_PROFILE -> handleProfile(from, frame.payload)
            TYPE_PEER_ANNC -> handlePeerAnnc(from, frame.payload)
            TYPE_ROUTES -> handleRoutes(from, frame.payload)
            // A newer peer's frame type: ignoring it is what lets the protocol grow.
            else -> Unit
        }
    }

    private fun handleMessage(from: String, payload: ByteArray) {
        val m = decodeMessage(payload)
        val dest = bytesToMac(m.destMac)
        val sender = bytesToMac(m.senderMac)
        if (dest != localId) {
            relayMessage(from, sender, dest, m.msgId, payload)
            return
        }
        if (!store.claimSeen(m.msgId)) {
            // A resend of something we have: just ACK again.
            sendTo(from, ackFrame(m.msgId, localBytes))
            return
        }
        val key = book.pubkey(sender)
        if (key == null) {
            store.releaseSeen(m.msgId)
            return
        }
        val text = try {
            sealer.open(m.ciphertext, key).toString(Charsets.UTF_8)
        } catch (e: Exception) {
            store.releaseSeen(m.msgId)
            return
        }
        synchronized(lock) {
            if (!m.groupId.contentEquals(ZERO_GROUP_ID)) groupConfirmed += "${m.groupId.toHex()}/$sender"
            incomingSender[m.msgId.toHex()] = sender
        }
        store.saveIncoming(StoredMessage(m.msgId, m.groupId, sender, text, m.timestamp))
        // ACK before the UI: a slow UI must not cost the sender its receipt.
        sendTo(from, ackFrame(m.msgId, localBytes))
        listener.onMessage(m.groupId, sender, text, m.msgId, m.timestamp)
    }

    private fun relayMessage(from: String, sender: String, dest: String, msgId: ByteArray, payload: ByteArray) {
        if (sender == localId || dest == from) return
        val key = "${msgId.toHex()}/$dest"
        val fresh = seenRelayed.claim(key)
        // Always pass on a frame handed over by its own sender: it only
        // resends on purpose, and a loop cannot bring a frame back from it.
        if (!fresh && from != sender) return
        synchronized(lock) {
            relayedSender[msgId.toHex()] = sender
            if (relayedSender.size > 8192) {
                val drop = relayedSender.keys.take(4096)
                drop.forEach { relayedSender.remove(it) }
            }
        }
        routeFrame(dest, frameOf(TYPE_MESSAGE, payload), exclude = from, key = key)
    }

    private fun handleAck(from: String, payload: ByteArray) {
        val (msgId, fromBytes) = decodeAck(payload)
        val acker = bytesToMac(fromBytes)
        val hex = msgId.toHex()
        val key = "$hex/$acker"
        if (seenAcks.claim(key)) {
            synchronized(lock) {
                outgoing[hex]?.let { out ->
                    out.pending.remove(acker)
                    if (!out.groupId.contentEquals(ZERO_GROUP_ID)) {
                        groupConfirmed += "${out.groupId.toHex()}/$acker"
                    }
                    if (out.pending.isEmpty()) outgoing.remove(hex)
                }
                unacked[hex]?.let {
                    it.remove(acker)
                    if (it.isEmpty()) unacked.remove(hex)
                }
                lastAttempt.remove(key)
            }
            // Delivered: stop carrying copies of it for the recipient.
            dropHeld(acker, key)
            store.markAcked(msgId, acker)
            listener.onAck(msgId, acker)
        }
        if (synchronized(lock) { hex in sentIds }) return
        forwardReceipt(TYPE_ACK, key, frameOf(TYPE_ACK, payload), from, acker, hex)
    }

    private fun handleRead(from: String, payload: ByteArray) {
        val (msgId, fromBytes) = decodeRead(payload)
        val reader = bytesToMac(fromBytes)
        val hex = msgId.toHex()
        val key = "$hex/$reader"
        if (seenReads.claim(key)) {
            store.markRead(msgId, reader)
            listener.onRead(msgId, reader)
        }
        if (synchronized(lock) { hex in sentIds }) return
        forwardReceipt(TYPE_READ, key, frameOf(TYPE_READ, payload), from, reader, hex)
    }

    /** Steer a receipt to the sender if we relayed its message, else flood. */
    private fun forwardReceipt(type: Byte, key: String, frame: ByteArray, from: String, origin: String, hex: String) {
        val sender = synchronized(lock) { relayedSender[hex] }
        val targets: List<String> = if (sender != null && sender != from) {
            val t = if (routable(sender)) listOf(sender) else nextHops(sender, from).take(1)
            if (t.isEmpty()) {
                hold(sender, frame, "receipt/$type/$key")
                return
            }
            t
        } else {
            synchronized(lock) { sessions.keys.filter { it != from } }
        }
        val firstHop = from == origin
        for (t in targets) {
            if (receiptEdges.claim("$type/$key/$t") || firstHop) sendTo(t, frame)
        }
    }

    private fun handleProfile(from: String, payload: ByteArray) {
        val name = decodeProfile(payload)
        if (name.isEmpty() || name.equals(from, ignoreCase = true)) {
            val had = book.selfChosenName(from) != null
            book.setSelfChosenName(from, "")
            store.savePeerName(from, "")
            if (had) listener.onProfile(from, book.displayName(from))
            return
        }
        val changed = book.selfChosenName(from) != name
        book.setSelfChosenName(from, name)
        store.savePeerName(from, name)
        if (!changed) return
        listener.onProfile(from, book.displayName(from))
        // Pass the new name on so indirect peers see it without a reconnect.
        val key = book.pubkey(from) ?: return
        val annc = peerAnncFrame(listOf(PeerEntry(macToBytes(from), key, name)))
        for (t in connectedIds()) if (t != from) sendTo(t, annc)
    }

    private fun entryFor(id: String): PeerEntry? {
        val key = book.pubkey(id) ?: return null
        if (key.size != PUBKEY_BYTES) return null
        val mac = runCatching { macToBytes(id) }.getOrNull() ?: return null
        // The peer's own name only — a local override is ours, not theirs.
        return PeerEntry(mac, key, book.selfChosenName(id) ?: "")
    }

    private fun sendPeerAnnc(session: Session) {
        val entries = book.knownPeers()
            .filter { it != localId && it != session.id }
            .sorted()
            .mapNotNull(::entryFor)
        if (entries.isNotEmpty()) session.write(peerAnncFrame(entries.take(255)))
    }

    private fun announceNewcomer(id: String) {
        val entry = entryFor(id) ?: return
        val annc = peerAnncFrame(listOf(entry))
        for (t in connectedIds()) if (t != id) sendTo(t, annc)
    }

    private fun handlePeerAnnc(from: String, payload: ByteArray) {
        val direct = connectedIds()
        val learned = ArrayList<String>()
        val onward = ArrayList<PeerEntry>()
        for (e in decodePeerAnnc(payload)) {
            val id = bytesToMac(e.mac)
            if (id == localId || e.pubkey.size != PUBKEY_BYTES) continue
            val newKey = book.pubkey(id) == null
            book.learnRelayed(id, e.pubkey)
            store.savePeerKey(id, e.pubkey, direct = false)
            if (newKey) learned += id
            val renamed = e.name.isNotEmpty() && acceptName(id, e.name, from, direct)
            if (renamed) {
                book.setSelfChosenName(id, e.name)
                store.savePeerName(id, e.name)
                listener.onProfile(id, book.displayName(id))
            }
            if (newKey || renamed) entryFor(id)?.let { onward += it }
        }
        if (learned.isNotEmpty()) {
            book.noteKnown(learned)
            listener.onPresence()
        }
        if (onward.isEmpty()) return
        // New keys and fresher names travel on; only changes do, so each
        // piece of news crosses the mesh once.
        val annc = peerAnncFrame(onward.take(255))
        for (t in direct) if (t != from) sendTo(t, annc)
    }

    /** Names flow outward from their owner; see `peers.py:_accept_name`. */
    private fun acceptName(id: String, name: String, from: String, direct: Set<String>): Boolean {
        if (id in direct) return false
        val current = book.selfChosenName(id)
        if (current == name) return false
        if (current == null) return true
        return indirectVia[id] == from
    }

    private fun handleRoutes(from: String, payload: ByteArray) {
        val table = HashMap<String, Int>()
        for (r in decodeRoutes(payload)) {
            val d = bytesToMac(r.wireId)
            if (d != localId && d != from) table[d] = minOf(r.hops, table[d] ?: r.hops)
        }
        synchronized(lock) {
            val unchanged = (routesFrom[from] ?: emptyMap<String, Int>()) == table
            routesFrom[from] = table
            if (unchanged) return
        }
        onTopologyChange()
    }

    private fun handleGroupSetup(from: String, payload: ByteArray) {
        val setup = decodeGroupSetup(payload)
        val members = LinkedHashMap<String, ByteArray>()
        for (m in setup.members) members[bytesToMac(m.mac)] = m.pubkey
        val frame = frameOf(TYPE_GROUP_SETUP, payload)
        val hex = setup.groupId.toHex()

        if (localId !in members) {
            // Only a relay for this group: pass it on, never adopt it.
            if (!seenSetups.claim(hex)) return
            for (m in members.keys) if (m != from) routeFrame(m, frame, exclude = from)
            return
        }

        val group = MeshGroup(setup.groupId, setup.name, members)
        val isNew = synchronized(lock) {
            if (from in members) groupConfirmed += "$hex/$from"
            if (hex in groups) {
                false
            } else {
                groups[hex] = group
                true
            }
        }
        if (!isNew) return
        // Member keys fill gaps; they never override one from a handshake.
        for ((id, key) in members) {
            if (id == localId) continue
            book.learnRelayed(id, key)
            store.savePeerKey(id, key, direct = false)
        }
        book.noteKnown(members.keys.filter { it != localId })
        store.saveGroup(group)
        listener.onGroup(group)
        for (m in members.keys) if (m != from && m != localId) routeFrame(m, frame, exclude = from)
    }
}

/** Seals message bodies. The app uses libsodium-android; tests libsodium-java. */
interface Sealer {
    fun seal(plaintext: ByteArray, theirPub: ByteArray): ByteArray

    /** Throws on a wrong key or a tampered body. */
    fun open(sealed: ByteArray, theirPub: ByteArray): ByteArray
}

/** A connected byte stream: an RFCOMM socket in the app, TCP in tests. */
interface MeshLink {
    val input: InputStream
    val output: OutputStream
    fun close()
}

/** Keys seen within the last `windowMs`. Thread-safe. */
internal class Recent(private val windowMs: Long, private val clock: () -> Long) {
    private val seen = HashMap<String, Long>()

    /** True (and records the key) unless it was claimed within the window. */
    @Synchronized
    fun claim(key: String): Boolean {
        val now = clock()
        val at = seen[key]
        if (at != null && now - at < windowMs) return false
        seen[key] = now
        if (seen.size > 4096) prune()
        return true
    }

    @Synchronized
    fun add(key: String) {
        seen[key] = clock()
    }

    @Synchronized
    fun prune() {
        val now = clock()
        seen.entries.removeAll { now - it.value >= windowMs }
    }
}

/** Compare two wire ids as unsigned big-endian integers. */
internal fun compareIds(a: ByteArray, b: ByteArray): Int {
    for (i in 0 until minOf(a.size, b.size)) {
        val d = (a[i].toInt() and 0xFF) - (b[i].toInt() and 0xFF)
        if (d != 0) return d
    }
    return a.size - b.size
}
