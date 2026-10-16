package com.muninn.core

import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.PriorityBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Mesh — Kotlin port of python/src/muninn/mesh.py. Same wire behaviour,
 * timers and algorithms (distance-vector routing, persistent outbox with
 * backoff, store-and-forward, time-bounded dedup, keepalive, roster gossip,
 * receiver-driven file transfer, app events).
 */

const val ZERO_GROUP_HEX = "00000000000000000000000000000000"

fun dmConv(node: String) = "dm:$node"
fun groupConv(gid: ByteArray) = "group:" + gid.hex()
fun convGroupId(conv: String): ByteArray? = if (conv.startsWith("group:")) conv.substring(6).unhex() else null

/** A connected byte stream (RFCOMM socket, sim TCP socket, ...). */
interface Conn {
    val input: InputStream
    val output: OutputStream
    fun close()
}

object Tun {
    const val HELLO_TIMEOUT_MS = 10_000L
    const val PING_INTERVAL = 4.0
    const val LINK_TIMEOUT = 15.0
    const val ADVERT_INTERVAL = 4.0
    const val ROUTE_TTL = 13.0
    const val MAX_HOPS = 8
    const val RELAY_DEDUP = 5.0
    const val RETRY_BASE = 6.0
    const val RETRY_MAX = 60.0
    const val RELAY_MAX_AGE = 6 * 3600L
    const val RELAY_MAX_ROWS = 5000
    const val TYPING_TTL = 6.0
    const val CHUNK_SIZE = 16000
    const val FILE_WINDOW = 12
    const val CHUNK_TIMEOUT = 5.0
    const val AUTO_DOWNLOAD_MAX = 15L * 1024 * 1024
    const val MAX_FILE_SIZE = 64L * 1024 * 1024
    const val BULK_QUEUE_MAX = 48

    const val PRIO_CONTROL = 0
    const val PRIO_REALTIME = 1
    const val PRIO_CHAT = 2
    const val PRIO_BULK = 3
}

fun mono(): Double = System.nanoTime() / 1e9

data class PeerInfo(
    val nodeId: String, val pubkey: ByteArray, var name: String = "", var nameTs: Long = 0,
    var override: String? = null, var lastSeen: Long = 0, var replacedBy: String? = null,
) {
    val display: String get() = override?.takeIf { it.isNotEmpty() } ?: name.ifEmpty { nodeId }
}

class Group(val groupId: ByteArray, var name: String, var version: Int, val members: MutableSet<String>, var left: Boolean = false) {
    val conv get() = groupConv(groupId)
}

/** Event callbacks. All run on the mesh event thread, in order. */
interface MeshListener {
    fun onMessage(m: StoredMessage) {}
    fun onUpdate(msgId: ByteArray) {}
    fun onPeers() {}
    fun onGroup(groupId: ByteArray) {}
    fun onFile(fileId: ByteArray) {}
    fun onApp(ev: AppEvent) {}
    fun onRt(src: String, app: String, session: String, body: Any?, blob: ByteArray) {}
    fun onTyping(conv: String, node: String) {}
}

class Mesh(
    val storage: Storage,
    private val secret: ByteArray,
    val nodeId: String,
    name: String,
    nameTs: Long,
    private val log: (String) -> Unit = {},
) {
    val pubkey: ByteArray = Nacl.publicKey(secret)
    @Volatile var name: String = name; private set
    @Volatile var nameTs: Long = nameTs; private set
    val lock = Any()

    private val keys = ConcurrentHashMap<String, ByteArray>() // pubkey hex -> shared key

    val links = LinkedHashMap<String, Link>()
    private val pending = HashSet<Link>()
    val peers = LinkedHashMap<String, PeerInfo>()
    val groups = LinkedHashMap<String, Group>() // gid hex -> group
    val routes = HashMap<String, HashMap<String, Pair<Int, Double>>>()
    private var reachableSet = HashSet<String>()
    private val outbox = LinkedHashMap<String, OutItem>()
    private val relaySeen = HashMap<String, Double>()
    private val ackSeen = HashMap<String, Double>()
    private val unrelSeen = HashMap<String, Double>()
    @Volatile private var advertDue = 0.0
    val downloads = LinkedHashMap<String, Download>()
    val files = LinkedHashMap<String, FileRecord>()
    private val serveQ = ArrayList<Triple<String, ByteArray, Int>>()
    val typing = ConcurrentHashMap<Pair<String, String>, Double>()
    var autoDownloadMax = Tun.AUTO_DOWNLOAD_MAX
    val stats = ConcurrentHashMap<String, AtomicLong>()

    private val listeners = java.util.concurrent.CopyOnWriteArrayList<MeshListener>()
    private val events = LinkedBlockingQueue<() -> Unit>()
    private val stopped = AtomicBoolean(false)

    class OutItem(
        val packetId: ByteArray, val dst: String, val body: ByteArray, val ts: Long,
        val msgId: ByteArray?, val flags: Int, var frame: ByteArray? = null,
        var attempts: Int = 0, var nextTry: Double = 0.0,
    )

    class Download(val rec: FileRecord) {
        val inflight = HashMap<Int, Double>()
        var lastChunk = 0.0
        var started = mono()
        var dirty = 0
    }

    init {
        for (p in storage.loadPeers()) peers[p.nodeId] = PeerInfo(p.nodeId, p.pubkey, p.name, p.nameTs, p.override, p.lastSeen, p.replacedBy)
        for (g in storage.loadGroups()) groups[g.groupId.hex()] = Group(g.groupId, g.name, g.version, g.members.toMutableSet(), g.left)
        for (e in storage.outboxLoad()) outbox[e.packetId.hex()] = OutItem(e.packetId, e.dst, e.body, e.ts, e.msgId, P.F_RELIABLE)
        for (f in storage.loadFiles()) {
            files[f.fileId.hex()] = f
            if (f.state == "downloading") downloads[f.fileId.hex()] = Download(f)
        }
    }

    private fun stat(k: String) = stats.getOrPut(k) { AtomicLong() }.incrementAndGet()

    fun addListener(l: MeshListener) = listeners.add(l)
    fun removeListener(l: MeshListener) = listeners.remove(l)
    private fun emit(f: (MeshListener) -> Unit) = events.put { listeners.forEach { runCatching { f(it) }.onFailure { e -> log("listener: $e") } } }

    fun start() {
        thread("mesh-events") {
            while (!stopped.get()) {
                val ev = events.take()
                ev()
            }
        }
        thread("mesh-maint") { maintenanceLoop() }
        thread("mesh-serve") { serveLoop() }
    }

    fun stop() {
        stopped.set(true)
        val all = synchronized(lock) { links.values.toList() + pending.toList() }
        all.forEach { it.close() }
        events.put {}
    }

    private fun thread(name: String, body: () -> Unit) = Thread({
        try { body() } catch (e: Throwable) { if (!stopped.get()) log("$name died: $e") }
    }, name).apply { isDaemon = true; start() }

    private fun sharedKey(pub: ByteArray): ByteArray = keys.getOrPut(pub.hex()) { Nacl.beforeNm(pub, secret) }

    // ================================================================ links

    inner class Link(val conn: Conn, transportAddr: String, val initiated: Boolean) {
        val transportAddr = transportAddr.uppercase()
        @Volatile var nodeId: String? = null
        @Volatile var hello: P.Hello? = null
        private val seq = AtomicLong()
        private val q = PriorityBlockingQueue<Triple<Int, Long, ByteArray?>>(64, compareBy({ it.first }, { it.second }))
        val bulkPending = AtomicInteger()
        val closed = AtomicBoolean(false)
        @Volatile var lastRx = mono()
        @Volatile var lastTx = mono()
        @Volatile var rtt: Double? = null
        @Volatile var ping: Pair<ByteArray, Double>? = null
        val bytesIn = AtomicLong()
        val bytesOut = AtomicLong()

        fun send(frame: ByteArray, prio: Int = Tun.PRIO_CHAT): Boolean {
            if (closed.get()) return false
            if (prio == Tun.PRIO_BULK) {
                if (bulkPending.get() >= Tun.BULK_QUEUE_MAX) return false
                bulkPending.incrementAndGet()
            }
            q.put(Triple(prio, seq.incrementAndGet(), frame))
            return true
        }

        fun bulkRoom() = Tun.BULK_QUEUE_MAX - bulkPending.get()

        fun close() {
            if (!closed.compareAndSet(false, true)) return
            runCatching { conn.close() }
            q.put(Triple(-1, -1, null))
        }

        fun writer() {
            try {
                val out = conn.output
                while (!closed.get()) {
                    val (prio, _, frame) = q.take()
                    if (frame == null) break
                    if (prio == Tun.PRIO_BULK) bulkPending.decrementAndGet()
                    out.write(frame)
                    out.flush()
                    lastTx = mono()
                    bytesOut.addAndGet(frame.size.toLong())
                }
            } catch (_: Throwable) {
            } finally {
                linkDown(this)
            }
        }

        fun reader() {
            try {
                val input = java.io.DataInputStream(java.io.BufferedInputStream(conn.input, 1 shl 16))
                while (!closed.get()) {
                    val f = P.readFrame(input)
                    lastRx = mono()
                    bytesIn.addAndGet(f.payload.size + 3L)
                    try {
                        onFrame(this, f.type, f.payload)
                    } catch (_: P.Malformed) {
                    } catch (e: Throwable) {
                        log("frame ${f.type} from $nodeId: $e")
                    }
                }
            } catch (_: Throwable) {
            } finally {
                linkDown(this)
            }
        }
    }

    /** Handshake on a freshly connected stream; blocks up to HELLO_TIMEOUT. */
    fun addLink(conn: Conn, transportAddr: String, initiated: Boolean): String? {
        val link = Link(conn, transportAddr, initiated)
        synchronized(lock) { pending.add(link) }
        val done = AtomicBoolean(false)
        // RFCOMM sockets have no read timeout: a watchdog closes a silent peer.
        thread("hello-watchdog") {
            Thread.sleep(Tun.HELLO_TIMEOUT_MS)
            if (!done.get()) runCatching { conn.close() }
        }
        val hello: P.Hello
        try {
            conn.output.write(P.encodeHello(pubkey, nodeId, name, nameTs))
            conn.output.flush()
            val f = P.readFrame(conn.input)
            done.set(true)
            if (f.type != P.T_HELLO) throw IllegalStateException("expected HELLO got ${f.type}")
            hello = P.decodeHello(f.payload)
            if (hello.version != P.VERSION) throw IllegalStateException("protocol version ${hello.version}")
            if (hello.nodeId == nodeId) throw IllegalStateException("connected to self")
        } catch (e: Throwable) {
            done.set(true)
            log("handshake with $transportAddr failed: $e")
            synchronized(lock) { pending.remove(link) }
            link.close()
            return null
        }
        link.nodeId = hello.nodeId
        link.hello = hello
        link.lastRx = mono()
        var oldToClose: Link? = null
        synchronized(lock) {
            pending.remove(link)
            val peer = peers[hello.nodeId]
            if (peer != null && !peer.pubkey.contentEquals(hello.pubkey)) {
                log("pubkey mismatch for ${hello.nodeId}")
                link.close()
                return null
            }
            val old = links[hello.nodeId]
            if (old != null && !old.closed.get()) {
                if (!preferNew(old, link)) {
                    link.close()
                    return hello.nodeId
                }
                oldToClose = old
            }
            links[hello.nodeId] = link
        }
        oldToClose?.close()
        val changed = learnPeer(P.RosterEntry(hello.nodeId, hello.pubkey, hello.name, hello.nameTs))
        checkReinstall(hello.nodeId, hello.name, link.transportAddr)
        storage.saveTransport(hello.nodeId, link.transportAddr)
        storage.touchPeer(hello.nodeId)
        thread("link-r ${hello.nodeId}") { link.reader() }
        thread("link-w ${hello.nodeId}") { link.writer() }
        for (f in P.encodeRoster(rosterEntries(exclude = hello.nodeId))) link.send(f, Tun.PRIO_CONTROL)
        link.send(routesFrameFor(hello.nodeId), Tun.PRIO_CONTROL)
        if (changed.isNotEmpty()) gossip(changed, hello.nodeId)
        advertDue = 0.0
        log("link up ${hello.nodeId} (${hello.name}) via ${link.transportAddr}")
        onTopologyChange()
        return hello.nodeId
    }

    // ================================================================ reinstalls

    /** Same radio address + same name, different identity → reinstalled app. */
    private fun checkReinstall(new: String, name: String, addr: String) {
        for ((old, a, _) in storage.loadTransports()) {
            if (a != addr || old == new) continue
            val (p, live) = synchronized(lock) { peers[old] to (old in links) }
            if (p == null || p.replacedBy != null || live) continue
            if (p.name.isNotEmpty() && name.isNotEmpty() && p.name != name) continue
            supersede(old, new, announce = true)
        }
    }

    private fun supersede(old: String, new: String, announce: Boolean) {
        val touched: List<Group>
        synchronized(lock) {
            val pOld = peers[old] ?: return
            val pNew = peers[new] ?: return
            if (pOld.replacedBy != null) return
            pOld.replacedBy = new
            if (pOld.override != null && pNew.override == null) { pNew.override = pOld.override; storage.setPeerOverride(new, pOld.override) }
            outbox.values.removeIf { it.dst == old }
            touched = groups.values.filter { old in it.members }
            touched.forEach { it.members.remove(old); it.members.add(new) }
        }
        val entries = storage.migratePeer(old, new)
        val now = mono()
        for (e in entries) {
            val item = OutItem(Nacl.randomBytes(16), new, e.body, P.now(), e.msgId, P.F_RELIABLE, nextTry = now)
            storage.outboxAdd(OutboxEntry(item.packetId, new, e.body, item.ts, e.msgId, 0))
            synchronized(lock) { outbox[item.packetId.hex()] = item }
        }
        log("$old reinstalled as $new; moved ${entries.size} pending")
        system(dmConv(new), "${displayName(new)} reinstalled Muninn (new security key). Your conversation continues here.")
        if (announce) {
            val others = synchronized(lock) { peers.values.filter { it.replacedBy == null && it.nodeId != old && it.nodeId != new }.map { it.nodeId } }
            sendBody(others, Body.B(Body.REPLACED, mapOf("o" to old, "n" to new)))
            for (g in touched) if (!g.left && nodeId in g.members) {
                synchronized(lock) { g.version += 1 }
                saveGroup(g)
                sendBody(synchronized(lock) { g.members.filter { it != nodeId } }, groupBody(g))
            }
        }
        emit { it.onPeers() }
    }

    fun activePeers(): List<PeerInfo> = synchronized(lock) { peers.values.filter { it.replacedBy == null } }

    private fun preferNew(old: Link, new: Link): Boolean {
        if (mono() - old.lastRx > Tun.PING_INTERVAL * 2.5) return true
        val lowerIsMe = nodeId < (new.nodeId ?: "")
        fun canonical(l: Link) = l.initiated == lowerIsMe
        if (canonical(new) && !canonical(old)) return true
        if (canonical(old) && !canonical(new)) return false
        return true
    }

    private fun linkDown(link: Link) {
        link.close()
        synchronized(lock) {
            pending.remove(link)
            val id = link.nodeId ?: return
            if (links[id] !== link) return
            links.remove(id)
            for (dest in routes.keys.toList()) {
                routes[dest]?.remove(id)
                if (routes[dest].isNullOrEmpty()) routes.remove(dest)
            }
        }
        log("link down ${link.nodeId}")
        advertDue = 0.0
        onTopologyChange()
    }

    fun connected(): List<String> = synchronized(lock) { links.keys.toList() }

    fun hasTransport(addr: String): Boolean {
        val a = addr.uppercase()
        return synchronized(lock) { links.values.any { it.transportAddr == a } || pending.any { it.transportAddr == a } }
    }

    fun linkInfo(): List<Map<String, Any?>> {
        val now = mono()
        return synchronized(lock) {
            links.map { (id, l) ->
                mapOf(
                    "node" to id, "addr" to l.transportAddr, "rtt_ms" to l.rtt?.let { (it * 1000).toLong() },
                    "idle" to Math.round((now - l.lastRx) * 10) / 10.0, "in" to l.bytesIn.get(),
                    "out" to l.bytesOut.get(), "initiated" to l.initiated,
                )
            }
        }
    }

    // ================================================================ frames

    private fun onFrame(link: Link, type: Int, payload: ByteArray) {
        when (type) {
            P.T_PACKET -> onPacket(link, payload)
            P.T_ACK -> onAck(link, payload)
            P.T_PING -> link.send(P.encodePong(payload), Tun.PRIO_CONTROL)
            P.T_PONG -> link.ping?.let { (tok, t) -> if (tok.contentEquals(payload)) { link.rtt = mono() - t; link.ping = null } }
            P.T_ROUTES -> onRoutes(link, P.decodeRoutes(payload))
            P.T_ROSTER -> {
                val changed = P.decodeRoster(payload).flatMap { learnPeer(it) }
                if (changed.isNotEmpty()) gossip(changed, link.nodeId)
            }
        }
    }

    // ================================================================ peers / roster

    private fun learnPeer(e: P.RosterEntry): List<P.RosterEntry> {
        if (e.nodeId == nodeId) return emptyList()
        val peer: PeerInfo
        synchronized(lock) {
            val p = peers[e.nodeId]
            if (p == null) {
                peer = PeerInfo(e.nodeId, e.pubkey, e.name, e.nameTs)
                peers[e.nodeId] = peer
            } else if (!p.pubkey.contentEquals(e.pubkey)) {
                return emptyList()
            } else if (e.nameTs > p.nameTs) {
                p.name = e.name; p.nameTs = e.nameTs; peer = p
            } else return emptyList()
        }
        storage.upsertPeer(e.nodeId, e.pubkey, peer.name, peer.nameTs)
        emit { it.onPeers() }
        return listOf(P.RosterEntry(peer.nodeId, peer.pubkey, peer.name, peer.nameTs))
    }

    private fun rosterEntries(exclude: String?): List<P.RosterEntry> = synchronized(lock) {
        listOf(P.RosterEntry(nodeId, pubkey, name, nameTs)) +
            peers.values.filter { it.nodeId != exclude && it.replacedBy == null }.map { P.RosterEntry(it.nodeId, it.pubkey, it.name, it.nameTs) }
    }

    private fun gossip(entries: List<P.RosterEntry>, exclude: String?) {
        val frames = P.encodeRoster(entries)
        val targets = synchronized(lock) { links.filter { it.key != exclude }.values.toList() }
        for (l in targets) frames.forEach { l.send(it, Tun.PRIO_CONTROL) }
    }

    fun setName(newName: String) {
        name = newName
        nameTs = maxOf(P.now(), nameTs + 1)
        storage.setDisplayName(newName, nameTs)
        gossip(listOf(P.RosterEntry(nodeId, pubkey, newName, nameTs)), null)
        emit { it.onPeers() }
    }

    fun setOverride(node: String, override: String?) {
        synchronized(lock) { peers[node]?.override = override?.ifEmpty { null } }
        storage.setPeerOverride(node, override)
        emit { it.onPeers() }
    }

    fun displayName(node: String): String {
        if (node == nodeId) return name.ifEmpty { "me" }
        return synchronized(lock) {
            val p = peers[node] ?: return@synchronized node
            val label = p.display
            if (label == node || p.override != null) return@synchronized label
            val clash = peers.values.any { it.nodeId != node && it.replacedBy == null && it.display.equals(label, true) } ||
                label.equals(name, true)
            if (clash) "$label ·" + node.replace(":", "").takeLast(4) else label
        }
    }

    fun resolve(s: String): String? {
        val up = s.uppercase()
        val ps = synchronized(lock) { if (up in peers) return up; peers.values.filter { it.replacedBy == null } }
        val low = s.lowercase()
        ps.firstOrNull { it.override?.lowercase() == low }?.let { return it.nodeId }
        ps.firstOrNull { displayName(it.nodeId).lowercase() == low }?.let { return it.nodeId }
        ps.firstOrNull { it.name.lowercase() == low }?.let { return it.nodeId }
        return ps.firstOrNull { it.display.lowercase().startsWith(low) }?.nodeId
    }

    // ================================================================ routing

    private fun onRoutes(link: Link, entries: List<Pair<String, Int>>) {
        val via = link.nodeId ?: return
        val now = mono()
        synchronized(lock) {
            val fresh = HashMap<String, Int>()
            for ((dest, hops) in entries) {
                if (dest == nodeId || dest == via) continue
                if (hops != P.ROUTE_UNREACHABLE && hops + 1 <= Tun.MAX_HOPS) fresh[dest] = hops + 1
            }
            for (dest in routes.keys.toList()) {
                val r = routes[dest]!!
                if (via in r && dest !in fresh) {
                    r.remove(via)
                    if (r.isEmpty()) routes.remove(dest)
                }
            }
            for ((dest, hops) in fresh) routes.getOrPut(dest) { HashMap() }[via] = hops to now
        }
        onTopologyChange()
    }

    fun nextHop(dest: String, exclude: String? = null): String? = synchronized(lock) {
        if (dest in links) return dest
        var best: Triple<String, Int, Double>? = null
        for ((via, ht) in routes[dest] ?: return null) {
            if (via == exclude || via !in links) continue
            val (h, t) = ht
            if (best == null || h < best.second || (h == best.second && t > best.third)) best = Triple(via, h, t)
        }
        best?.first
    }

    fun hops(dest: String): Int? = synchronized(lock) {
        if (dest in links) return 1
        routes[dest]?.filter { it.key in links }?.values?.minOfOrNull { it.first }
    }

    fun reachable(dest: String) = nextHop(dest) != null

    private fun routesFrameFor(neighbour: String): ByteArray {
        val entries = ArrayList<Pair<String, Int>>()
        synchronized(lock) {
            for (id in links.keys) if (id != neighbour) entries.add(id to 1)
            for ((dest, vias) in routes) {
                if (dest in links || dest == neighbour) continue
                val live = vias.filter { it.key in links }.map { it.value.first to it.key }
                if (live.isEmpty()) continue
                val (h, v) = live.minWith(compareBy({ it.first }, { it.second }))
                entries.add(dest to if (v == neighbour) P.ROUTE_UNREACHABLE else h)
            }
        }
        return P.encodeRoutes(entries)
    }

    private fun onTopologyChange() {
        val newly: Set<String>
        synchronized(lock) {
            val now = HashSet(links.keys)
            for ((d, vias) in routes) if (vias.keys.any { it in links }) now.add(d)
            newly = now - reachableSet
            val gone = reachableSet - now
            reachableSet = now
            val stamp = P.now()
            for (id in newly + gone) peers[id]?.lastSeen = stamp
            advertDue = minOf(advertDue, mono() + 0.3)
        }
        newly.forEach { flushFor(it) }
        emit { it.onPeers() }
    }

    private fun flushFor(dest: String) {
        for (f in storage.relayTake(dest)) {
            val hop = nextHop(dest) ?: break
            sendVia(hop, f)
        }
        val now = mono()
        val stalled = ArrayList<String>()
        synchronized(lock) {
            for (it in outbox.values) if (it.dst == dest) { it.nextTry = now; it.attempts = minOf(it.attempts, 1) }
            for ((fid, dl) in downloads) if (fileSource(dl.rec) == dest) { dl.inflight.clear(); dl.started = now; stalled.add(fid) }
        }
        stalled.forEach { requestChunks(it.unhex()) }
    }

    private fun sendVia(hop: String, frame: ByteArray, prio: Int? = null): Boolean {
        val l = synchronized(lock) { links[hop] } ?: return false
        return l.send(frame, prio ?: framePrio(frame))
    }

    private fun framePrio(frame: ByteArray): Int {
        if ((frame[0].toInt() and 0xff) == P.T_PACKET && frame.size > 3 + 29) {
            val flags = frame[3 + 29].toInt()
            if (flags and P.F_REALTIME != 0) return Tun.PRIO_REALTIME
            if (flags and P.F_BULK != 0) return Tun.PRIO_BULK
            return Tun.PRIO_CHAT
        }
        return Tun.PRIO_CONTROL
    }

    // ================================================================ packets out

    private fun encryptFrame(it: OutItem): ByteArray? {
        val peer = synchronized(lock) { peers[it.dst] } ?: return null
        val sealed = Nacl.seal(it.body, sharedKey(peer.pubkey))
        if (sealed.size > P.MAX_BOX) throw P.FrameTooLarge("body too large")
        return P.encodePacket(P.Packet(it.packetId, nodeId, it.dst, P.DEFAULT_TTL, it.flags, it.ts, pubkey, sealed))
    }

    private fun sendBody(dsts: Collection<String>, body: Body.B, msgId: ByteArray? = null): List<ByteArray> {
        val raw = Body.encode(body)
        var flags = 0
        if (body.reliable) flags = flags or P.F_RELIABLE
        if (body.kind == Body.CHUNK) flags = flags or P.F_BULK
        if (body.kind == Body.RT || body.kind == Body.TYPING) flags = flags or P.F_REALTIME
        val ts = P.now()
        val pids = ArrayList<ByteArray>()
        for (dst in dsts) {
            if (dst == nodeId) continue
            val item = OutItem(Nacl.randomBytes(16), dst, raw, ts, msgId, flags)
            item.frame = encryptFrame(item)
            pids.add(item.packetId)
            if (body.reliable) {
                storage.outboxAdd(OutboxEntry(item.packetId, dst, raw, ts, msgId, 0))
                item.attempts = 1
                item.nextTry = mono() + Tun.RETRY_BASE
                synchronized(lock) { outbox[item.packetId.hex()] = item }
            }
            val f = item.frame ?: continue
            nextHop(dst)?.let { sendVia(it, f); stat("sent") }
        }
        return pids
    }

    private fun sendAck(pkt: P.Packet) {
        val frame = P.encodeAck(P.Ack(pkt.packetId, nodeId, pkt.src, P.DEFAULT_TTL))
        val hop = nextHop(pkt.src)
        if (hop != null) sendVia(hop, frame, Tun.PRIO_CONTROL)
        else storage.relayPut(pkt.packetId + byteArrayOf('A'.code.toByte()), pkt.src, frame)
    }

    // ================================================================ packets in

    private fun onPacket(link: Link, payload: ByteArray) {
        val pkt = P.decodePacket(payload)
        if (pkt.dst != nodeId) { relayPacket(link, pkt, payload); return }
        if (pkt.src == nodeId) return
        val peer = synchronized(lock) { peers[pkt.src] }
        if (peer != null && !peer.pubkey.contentEquals(pkt.srcPubkey)) { log("pubkey mismatch from ${pkt.src}"); return }
        val body = try { Body.decode(Nacl.open(pkt.box, sharedKey(pkt.srcPubkey))) } catch (_: Throwable) { return }
        if (peer == null) learnPeer(P.RosterEntry(pkt.src, pkt.srcPubkey, "", 0))
        if (pkt.reliable) {
            sendAck(pkt)
            if (!storage.claimSeen(pkt.packetId)) return
        } else {
            synchronized(lock) {
                if (unrelSeen.containsKey(pkt.packetId.hex())) return
                unrelSeen[pkt.packetId.hex()] = mono()
            }
        }
        stat("received")
        try { dispatch(pkt, body) } catch (e: Throwable) { log("dispatch ${body.kind}: $e") }
    }

    private fun relayPacket(link: Link, pkt: P.Packet, payload: ByteArray) {
        if (pkt.src == nodeId || pkt.ttl <= 1) return
        val now = mono()
        synchronized(lock) {
            val t = relaySeen[pkt.packetId.hex()]
            if (t != null && now - t < Tun.RELAY_DEDUP) return
            relaySeen[pkt.packetId.hex()] = now
        }
        val frame = P.packetWithTtl(payload, pkt.ttl - 1)
        stat("relayed")
        val hop = nextHop(pkt.dst, exclude = link.nodeId)
        if (hop != null && sendVia(hop, frame)) return
        if (pkt.reliable) { storage.relayPut(pkt.packetId, pkt.dst, frame); stat("stored") }
    }

    private fun onAck(link: Link, payload: ByteArray) {
        val ack = P.decodeAck(payload)
        if (ack.dst != nodeId) {
            if (ack.ttl <= 1) return
            val key = ack.packetId.hex() + ack.src
            val now = mono()
            synchronized(lock) {
                val t = ackSeen[key]
                if (t != null && now - t < Tun.RELAY_DEDUP) return
                ackSeen[key] = now
            }
            val frame = P.encodeAck(P.Ack(ack.packetId, ack.src, ack.dst, ack.ttl - 1))
            val hop = nextHop(ack.dst, exclude = link.nodeId)
            if (hop == null || !sendVia(hop, frame, Tun.PRIO_CONTROL))
                storage.relayPut(ack.packetId + byteArrayOf('A'.code.toByte()), ack.dst, frame)
            return
        }
        val item = synchronized(lock) { outbox.remove(ack.packetId.hex()) }
        val res = storage.outboxAck(ack.packetId)
        val msgId = item?.msgId ?: res?.second
        if (msgId != null && storage.markDelivered(msgId, ack.src)) emit { it.onUpdate(msgId) }
    }

    // ================================================================ dispatch

    private fun convFor(src: String, meta: Map<String, Any?>): String {
        val g = meta.str("g")
        if (g.isEmpty()) return dmConv(src)
        val known = synchronized(lock) { groups[g] }
        if (known != null) return known.conv
        val grp = Group(g.unhex(), "group", 0, mutableSetOf(src, nodeId))
        synchronized(lock) { groups[g] = grp }
        saveGroup(grp)
        sendBody(listOf(src), Body.B(Body.GROUP_REQ, mapOf("g" to g)))
        emit { it.onGroup(grp.groupId) }
        return grp.conv
    }

    private fun dispatch(pkt: P.Packet, body: Body.B) {
        val m = body.meta
        val src = pkt.src
        val ts = minOf(pkt.ts, P.now() + 60)
        when (body.kind) {
            Body.TEXT -> {
                val msg = StoredMessage(
                    m.str("i").unhex(), convFor(src, m), src, ts, "text", m.str("t"),
                    m.str("r").takeIf { it.isNotEmpty() }?.unhex(),
                )
                if (storage.saveMessage(msg)) {
                    typing.remove(msg.conv to src)
                    emit { it.onMessage(msg) }
                }
            }
            Body.FILE -> onFileOffer(src, ts, m, body.blob)
            Body.REACT -> {
                val mid = m.str("m").unhex()
                storage.setReaction(mid, src, m.str("e").take(32), !m.bool("x"))
                emit { it.onUpdate(mid) }
            }
            Body.EDIT -> { val mid = m.str("m").unhex(); if (storage.editMessage(mid, src, m.str("t"))) emit { it.onUpdate(mid) } }
            Body.DELETE -> { val mid = m.str("m").unhex(); if (storage.deleteMessage(mid, src)) emit { it.onUpdate(mid) } }
            Body.RECEIPT -> for (h in m.list("r")) {
                val mid = (h as String).unhex()
                if (storage.markRead(mid, src)) emit { it.onUpdate(mid) }
            }
            Body.GROUP -> onGroupInfo(src, m)
            Body.GROUP_REQ -> {
                val g = synchronized(lock) { groups[m.str("g")] }
                if (g != null && src in g.members && g.version > 0) sendBody(listOf(src), groupBody(g))
            }
            Body.CHUNK -> onChunk(m, body.blob)
            Body.FILE_REQ -> onFileReq(src, m)
            Body.TYPING -> {
                val conv = convFor(src, m)
                typing[conv to src] = mono() + Tun.TYPING_TTL
                emit { it.onTyping(conv, src) }
            }
            Body.APP -> {
                val ev = AppEvent(m.str("i").unhex(), m.str("s"), m.str("a"), convFor(src, m), src, m.int("q"), m.str("y"), m["b"], ts)
                if (storage.saveAppEvent(ev)) emit { it.onApp(ev) }
            }
            Body.REPLACED -> {
                val old = m.str("o"); val new = m.str("n")
                val (pOld, pNew, live) = synchronized(lock) { Triple(peers[old], peers[new], old in links) }
                if (pOld != null && pNew != null && (pOld.name.isEmpty() || pOld.name == pNew.name) && !live)
                    supersede(old, new, announce = false)
            }
            Body.RT -> emit { it.onRt(src, m.str("a"), m.str("s"), m["b"], body.blob) }
        }
    }

    // ================================================================ chat API

    fun convMembers(conv: String): List<String> {
        if (conv.startsWith("dm:")) return listOf(conv.substring(3))
        val gid = convGroupId(conv) ?: return emptyList()
        val g = synchronized(lock) { groups[gid.hex()] } ?: return emptyList()
        if (g.left) return emptyList()
        return synchronized(lock) { g.members.filter { it != nodeId } }
    }

    private fun convMeta(conv: String): Map<String, Any?> = convGroupId(conv)?.let { mapOf("g" to it.hex()) } ?: emptyMap()

    fun sendText(conv: String, text: String, replyTo: ByteArray? = null): ByteArray {
        val dsts = convMembers(conv)
        if (dsts.isEmpty()) throw IllegalArgumentException("no recipients for this conversation")
        val msgId = Nacl.randomBytes(16)
        val meta = LinkedHashMap<String, Any?>(mapOf("i" to msgId.hex(), "t" to text))
        meta.putAll(convMeta(conv))
        if (replyTo != null) meta["r"] = replyTo.hex()
        val body = Body.B(Body.TEXT, meta)
        if (Body.encode(body).size > 60000) throw P.FrameTooLarge("message too long")
        val msg = StoredMessage(msgId, conv, nodeId, P.now(), "text", text, replyTo)
        storage.saveMessage(msg, dsts)
        emit { it.onMessage(msg) }
        sendBody(dsts, body, msgId)
        return msgId
    }

    fun react(conv: String, msgId: ByteArray, emoji: String, remove: Boolean = false) {
        storage.setReaction(msgId, nodeId, emoji, !remove)
        emit { it.onUpdate(msgId) }
        val meta = LinkedHashMap<String, Any?>(mapOf("m" to msgId.hex(), "e" to emoji)).apply { putAll(convMeta(conv)) }
        if (remove) meta["x"] = 1
        sendBody(convMembers(conv), Body.B(Body.REACT, meta))
    }

    fun edit(conv: String, msgId: ByteArray, text: String) {
        if (!storage.editMessage(msgId, nodeId, text)) return
        emit { it.onUpdate(msgId) }
        sendBody(convMembers(conv), Body.B(Body.EDIT, mapOf("m" to msgId.hex(), "t" to text) + convMeta(conv)))
    }

    fun delete(conv: String, msgId: ByteArray) {
        if (!storage.deleteMessage(msgId, nodeId)) return
        emit { it.onUpdate(msgId) }
        sendBody(convMembers(conv), Body.B(Body.DELETE, mapOf("m" to msgId.hex()) + convMeta(conv)))
    }

    fun markRead(conv: String) {
        val bySender = LinkedHashMap<String, MutableList<String>>()
        for ((mid, sender) in storage.markReadLocal(conv)) if (sender != nodeId) bySender.getOrPut(sender) { ArrayList() }.add(mid.hex())
        for ((sender, ids) in bySender) ids.chunked(500).forEach { sendBody(listOf(sender), Body.B(Body.RECEIPT, mapOf("r" to it))) }
    }

    fun sendTyping(conv: String) = sendBody(convMembers(conv), Body.B(Body.TYPING, convMeta(conv)))

    fun typingIn(conv: String): List<String> {
        val now = mono()
        return typing.filter { it.key.first == conv && it.value > now }.map { it.key.second }
    }

    // ================================================================ groups

    private fun groupBody(g: Group): Body.B {
        val members = synchronized(lock) {
            g.members.sorted().map { id -> listOf(id, if (id == nodeId) pubkey.hex() else peers[id]!!.pubkey.hex()) }
        }
        return Body.B(Body.GROUP, mapOf("g" to g.groupId.hex(), "n" to g.name, "v" to g.version, "m" to members))
    }

    private fun saveGroup(g: Group) = storage.saveGroup(g.groupId, g.name, g.version, synchronized(lock) { g.members.sorted() }, g.left)

    private fun system(conv: String, text: String) {
        val m = StoredMessage(Nacl.randomBytes(16), conv, nodeId, P.now(), "system", text)
        storage.saveMessage(m)
        emit { it.onMessage(m) }
    }

    fun createGroup(name: String, members: List<String>): ByteArray {
        val ms = members.filter { it != nodeId }
        synchronized(lock) { ms.forEach { if (it !in peers) throw IllegalArgumentException("unknown peer $it") } }
        val g = Group(Nacl.randomBytes(16), name, 1, (ms + nodeId).toMutableSet())
        synchronized(lock) { groups[g.groupId.hex()] = g }
        saveGroup(g)
        sendBody(ms, groupBody(g))
        system(g.conv, "${displayName(nodeId)} created group “$name”")
        emit { it.onGroup(g.groupId) }
        return g.groupId
    }

    fun updateGroup(gid: ByteArray, name: String? = null, add: List<String> = emptyList(), remove: List<String> = emptyList()) {
        val g: Group
        val old: Set<String>
        synchronized(lock) {
            g = groups[gid.hex()] ?: throw IllegalArgumentException("unknown group")
            old = HashSet(g.members)
            g.version += 1
            if (!name.isNullOrEmpty()) g.name = name
            g.members.addAll(add.filter { it in peers })
            g.members.removeAll(remove.toSet())
            if (nodeId !in g.members) g.left = true
        }
        saveGroup(g)
        sendBody((old + g.members) - nodeId, groupBody(g))
        val bits = ArrayList<String>()
        if (!name.isNullOrEmpty()) bits.add("renamed the group to “$name”")
        if (add.isNotEmpty()) bits.add("added " + add.joinToString { displayName(it) })
        val removed = remove.filter { it != nodeId }
        if (removed.isNotEmpty()) bits.add("removed " + removed.joinToString { displayName(it) })
        if (nodeId in remove) bits.add("left")
        system(g.conv, "${displayName(nodeId)} " + bits.joinToString(", "))
        emit { it.onGroup(gid) }
    }

    fun leaveGroup(gid: ByteArray) = updateGroup(gid, remove = listOf(nodeId))

    private fun onGroupInfo(src: String, m: Map<String, Any?>) {
        val gidHex = m.str("g")
        val version = m.int("v")
        val members = LinkedHashMap<String, ByteArray>()
        for (e in m.list("m")) { val l = e as List<*>; members[l[0] as String] = (l[1] as String).unhex() }
        val prev = synchronized(lock) { groups[gidHex] }
        if (prev != null && prev.version >= version) return
        if (prev != null && prev.version > 0 && src !in prev.members) return
        for ((id, pk) in members) if (id != nodeId && synchronized(lock) { id !in peers }) learnPeer(P.RosterEntry(id, pk, "", 0))
        val isNew = prev == null || prev.version == 0
        val prevMembers = if (prev != null && prev.version > 0) HashSet(prev.members) else HashSet()
        val g = Group(gidHex.unhex(), m.str("n"), version, members.keys.toMutableSet())
        g.left = nodeId !in g.members
        synchronized(lock) { groups[gidHex] = g }
        saveGroup(g)
        val who = displayName(src)
        if (isNew) system(g.conv, "$who added you to “${g.name}”")
        else {
            val parts = ArrayList<String>()
            val added = g.members - prevMembers
            val removed = prevMembers - g.members
            if (added.isNotEmpty()) parts.add("added " + added.joinToString { displayName(it) })
            if (removed.isNotEmpty()) parts.add("removed " + removed.joinToString { displayName(it) })
            if (parts.isNotEmpty()) system(g.conv, "$who " + parts.joinToString(", "))
        }
        emit { it.onGroup(g.groupId) }
    }

    // ================================================================ files

    fun sendFile(
        conv: String, file: File, mime: String, name: String = file.name, caption: String = "",
        thumb: ByteArray = ByteArray(0), replyTo: ByteArray? = null,
    ): ByteArray {
        val dsts = convMembers(conv)
        if (dsts.isEmpty()) throw IllegalArgumentException("no recipients for this conversation")
        val size = file.length()
        if (size > Tun.MAX_FILE_SIZE) throw IllegalArgumentException("file too large (max ${Tun.MAX_FILE_SIZE / 1024 / 1024} MB)")
        val fileId = Nacl.randomBytes(16)
        val msgId = Nacl.randomBytes(16)
        val dir = File(storage.filesDir, fileId.hex()).apply { mkdirs() }
        val dest = File(dir, safeName(name))
        file.copyTo(dest, overwrite = true)
        val sha = sha256(dest)
        val chunks = maxOf(1, ((size + Tun.CHUNK_SIZE - 1) / Tun.CHUNK_SIZE).toInt())
        val rec = FileRecord(fileId, msgId, conv, nodeId, name, mime, size, sha, Tun.CHUNK_SIZE, chunks, dest.path, "complete",
            ByteArray((chunks + 7) / 8) { 0xff.toByte() })
        storage.saveFile(rec)
        synchronized(lock) { files[fileId.hex()] = rec }
        val meta = LinkedHashMap<String, Any?>(mapOf(
            "i" to msgId.hex(), "f" to fileId.hex(), "n" to name, "mt" to mime, "s" to size,
            "h" to sha, "cs" to Tun.CHUNK_SIZE, "c" to chunks,
        ))
        meta.putAll(convMeta(conv))
        if (caption.isNotEmpty()) meta["t"] = caption
        if (replyTo != null) meta["r"] = replyTo.hex()
        val t = if (thumb.size <= 12000) thumb else ByteArray(0)
        val localMeta = LinkedHashMap(meta.filterKeys { it != "i" && it != "g" })
        if (t.isNotEmpty()) localMeta["thumb"] = saveThumb(fileId, t)
        val msg = StoredMessage(msgId, conv, nodeId, P.now(), "file", caption, replyTo, localMeta)
        storage.saveMessage(msg, dsts)
        emit { it.onMessage(msg) }
        sendBody(dsts, Body.B(Body.FILE, meta, t), msgId)
        return msgId
    }

    private fun onFileOffer(src: String, ts: Long, m: Map<String, Any?>, thumb: ByteArray) {
        val fileId = m.str("f").unhex()
        val msgId = m.str("i").unhex()
        val conv = convFor(src, m)
        val size = m.long("s")
        val chunks = m.int("c")
        if (size > Tun.MAX_FILE_SIZE || chunks > 100000) return
        val meta = LinkedHashMap(m.filterKeys { it != "i" && it != "g" })
        if (thumb.isNotEmpty()) meta["thumb"] = saveThumb(fileId, thumb)
        val msg = StoredMessage(msgId, conv, src, ts, "file", m.str("t"), m.str("r").takeIf { it.isNotEmpty() }?.unhex(), meta)
        if (!storage.saveMessage(msg)) return
        val rec = FileRecord(fileId, msgId, conv, src, m.str("n"), m.str("mt"), size, m.str("h"), m.int("cs"), chunks, "", "offered",
            ByteArray((chunks + 7) / 8))
        synchronized(lock) { files[fileId.hex()] = rec }
        storage.saveFile(rec)
        emit { it.onMessage(msg) }
        if (size <= autoDownloadMax) download(fileId)
    }

    fun download(fileId: ByteArray) {
        synchronized(lock) {
            val rec = files[fileId.hex()] ?: return
            if (rec.state == "complete" || rec.state == "downloading") return
            rec.state = "downloading"
            val dir = File(storage.filesDir, fileId.hex()).apply { mkdirs() }
            val part = File(dir, safeName(rec.name) + ".part")
            rec.path = part.path
            if (!part.exists()) RandomAccessFile(part, "rw").use { it.setLength(rec.size) }
            downloads[fileId.hex()] = Download(rec)
            storage.saveFile(rec)
        }
        requestChunks(fileId)
        emit { it.onFile(fileId) }
    }

    fun fileProgress(fileId: ByteArray): Triple<Int, Int, String> {
        val r = synchronized(lock) { files[fileId.hex()] } ?: return Triple(0, 0, "unknown")
        return Triple(r.haveCount, r.chunks, r.state)
    }

    private fun fileSource(rec: FileRecord): String? =
        (listOf(rec.sender) + convMembers(rec.conv).filter { it != rec.sender }).firstOrNull { reachable(it) }

    private fun requestChunks(fileId: ByteArray) {
        val want = ArrayList<Int>()
        val src: String
        synchronized(lock) {
            val dl = downloads[fileId.hex()] ?: return
            val rec = dl.rec
            src = fileSource(rec) ?: return
            val now = mono()
            val stalled = now - maxOf(dl.lastChunk, dl.started) > Tun.CHUNK_TIMEOUT
            dl.inflight.entries.removeIf { (i, t) -> rec.has(i) || (stalled && now - t > Tun.CHUNK_TIMEOUT) }
            for (i in 0 until rec.chunks) {
                if (dl.inflight.size + want.size >= Tun.FILE_WINDOW) break
                if (!rec.has(i) && i !in dl.inflight) want.add(i)
            }
            want.forEach { dl.inflight[it] = now }
        }
        if (want.isNotEmpty()) sendBody(listOf(src), Body.B(Body.FILE_REQ, mapOf("f" to fileId.hex(), "w" to want)))
    }

    private fun onFileReq(src: String, m: Map<String, Any?>) {
        val fid = m.str("f")
        synchronized(lock) {
            val rec = files[fid] ?: return
            if (rec.state != "complete") return
            if (src !in convMembers(rec.conv) && src != rec.sender) return
            for (x in m.list("w").take(Tun.FILE_WINDOW * 2)) {
                val i = (x as Number).toInt()
                if (i in 0 until rec.chunks && serveQ.none { it.first == src && it.second.contentEquals(rec.fileId) && it.third == i })
                    serveQ.add(Triple(src, rec.fileId, i))
            }
        }
    }

    private fun serveLoop() {
        while (!stopped.get()) {
            var item: Triple<String, ByteArray, Int>? = null
            var rec: FileRecord? = null
            synchronized(lock) {
                val idx = serveQ.indexOfFirst { (dst, _, _) ->
                    val hop = nextHop(dst)
                    hop != null && (links[hop]?.bulkRoom() ?: 0) > 0
                }
                if (idx >= 0) { item = serveQ.removeAt(idx); rec = files[item!!.second.hex()] }
            }
            val it = item; val r = rec
            if (it == null || r == null) { Thread.sleep(20); continue }
            val data = try {
                RandomAccessFile(r.path, "r").use { f ->
                    f.seek(it.third.toLong() * r.chunkSize)
                    val n = minOf(r.chunkSize.toLong(), r.size - it.third.toLong() * r.chunkSize).toInt()
                    ByteArray(n).also { b -> f.readFully(b) }
                }
            } catch (_: Throwable) { continue }
            sendBody(listOf(it.first), Body.B(Body.CHUNK, mapOf("f" to r.fileId.hex(), "i" to it.third), data))
        }
    }

    private fun onChunk(m: Map<String, Any?>, data: ByteArray) {
        val fid = m.str("f")
        val i = m.int("i")
        var done = false
        val dl: Download
        synchronized(lock) {
            dl = downloads[fid] ?: return
            val rec = dl.rec
            if (i !in 0 until rec.chunks || rec.has(i)) return
            val expected = minOf(rec.chunkSize.toLong(), rec.size - i.toLong() * rec.chunkSize).toInt()
            if (data.size != expected) return
            RandomAccessFile(rec.path, "rw").use { it.seek(i.toLong() * rec.chunkSize); it.write(data) }
            rec.mark(i)
            dl.inflight.remove(i)
            dl.lastChunk = mono()
            dl.dirty++
            if (rec.haveCount == rec.chunks) { done = true; downloads.remove(fid) }
        }
        if (done) finishDownload(dl.rec) else {
            if (dl.dirty >= 16) { dl.dirty = 0; storage.saveFile(dl.rec) }
            requestChunks(fid.unhex())
        }
        emit { it.onFile(fid.unhex()) }
    }

    private fun finishDownload(rec: FileRecord) {
        if (sha256(File(rec.path)) != rec.sha256) {
            log("file ${rec.name}: hash mismatch, restarting")
            rec.have = ByteArray(rec.have.size); rec.state = "offered"
            storage.saveFile(rec)
            download(rec.fileId)
            return
        }
        val final = File(rec.path.removeSuffix(".part"))
        File(rec.path).renameTo(final)
        rec.path = final.path
        rec.state = "complete"
        storage.saveFile(rec)
        emit { it.onFile(rec.fileId) }
        emit { it.onUpdate(rec.msgId) }
    }

    private fun saveThumb(fileId: ByteArray, thumb: ByteArray): String {
        val dir = File(storage.filesDir, fileId.hex()).apply { mkdirs() }
        return File(dir, "thumb.jpg").apply { writeBytes(thumb) }.path
    }

    // ================================================================ apps

    fun appEvent(conv: String, app: String, session: String, type: String, body: Any?, seq: Int = 0): AppEvent {
        val ev = AppEvent(Nacl.randomBytes(16), session, app, conv, nodeId, seq, type, body, P.now())
        storage.saveAppEvent(ev)
        emit { it.onApp(ev) }
        val meta = LinkedHashMap<String, Any?>(mapOf("i" to ev.eventId.hex(), "a" to app, "s" to session, "y" to type, "b" to body, "q" to seq))
        meta.putAll(convMeta(conv))
        sendBody(convMembers(conv), Body.B(Body.APP, meta))
        return ev
    }

    fun rtSend(conv: String, app: String, session: String, body: Any?, blob: ByteArray = ByteArray(0), to: List<String>? = null) {
        sendBody(to ?: convMembers(conv), Body.B(Body.RT, mapOf("a" to app, "s" to session, "b" to body), blob))
    }

    // ================================================================ maintenance

    private fun maintenanceLoop() {
        var lastExpire = 0.0
        while (!stopped.get()) {
            Thread.sleep(250)
            val now = mono()
            try {
                keepalive(now)
                if (now >= advertDue) { advertise(); advertDue = now + Tun.ADVERT_INTERVAL }
                retransmit(now)
                if (now - lastExpire > 2.0) {
                    lastExpire = now
                    expire(now)
                    synchronized(lock) { downloads.keys.toList() }.forEach { requestChunks(it.unhex()) }
                }
            } catch (e: Throwable) { log("maintenance: $e") }
        }
    }

    private fun keepalive(now: Double) {
        for (l in synchronized(lock) { links.values.toList() }) {
            if (now - l.lastRx > Tun.LINK_TIMEOUT) { log("link ${l.nodeId} timed out"); l.close(); continue }
            val p = l.ping
            if (p == null && now - l.lastTx > Tun.PING_INTERVAL) {
                val tok = Nacl.randomBytes(8)
                l.ping = tok to now
                l.send(P.encodePing(tok), Tun.PRIO_CONTROL)
            } else if (p != null && now - p.second > Tun.PING_INTERVAL * 2) l.ping = null
        }
    }

    private fun advertise() {
        for ((id, l) in synchronized(lock) { links.toList() }) l.send(routesFrameFor(id), Tun.PRIO_CONTROL)
    }

    private fun retransmit(now: Double) {
        val due = synchronized(lock) { outbox.values.filter { it.nextTry <= now } }
        for (item in due) {
            val hop = nextHop(item.dst)
            if (hop == null) { item.nextTry = now + 1.0; continue }
            if (item.frame == null) {
                item.frame = encryptFrame(item)
                if (item.frame == null) { item.nextTry = now + 5.0; continue }
            }
            if (sendVia(hop, item.frame!!)) stat("retransmits")
            item.attempts++
            val delay = minOf(Tun.RETRY_BASE * Math.pow(2.0, (item.attempts - 2).toDouble()), Tun.RETRY_MAX)
            item.nextTry = now + delay * (0.85 + Math.random() * 0.3)
        }
    }

    private fun expire(now: Double) {
        var changed = false
        synchronized(lock) {
            relaySeen.entries.removeIf { now - it.value > 60 }
            unrelSeen.entries.removeIf { now - it.value > 60 }
            ackSeen.entries.removeIf { now - it.value > 60 }
            for (dest in routes.keys.toList()) {
                val r = routes[dest]!!
                if (r.entries.removeIf { now - it.value.second > Tun.ROUTE_TTL }) changed = true
                if (r.isEmpty()) routes.remove(dest)
            }
        }
        typing.entries.removeIf { it.value < now }
        storage.relayExpire(Tun.RELAY_MAX_AGE, Tun.RELAY_MAX_ROWS)
        if (changed) onTopologyChange()
    }

    // ================================================================ introspection

    fun pendingCount(dst: String? = null): Int = synchronized(lock) { outbox.values.count { dst == null || it.dst == dst } }

    fun status(): Map<String, Any?> = synchronized(lock) {
        mapOf(
            "node" to nodeId, "name" to name, "links" to linkInfo(),
            "routes" to routes.mapValues { (_, v) -> v.mapValues { it.value.first } },
            "reachable" to reachableSet.sorted(), "outbox" to outbox.size,
            "downloads" to downloads.mapValues { listOf(it.value.rec.haveCount, it.value.rec.chunks) },
            "stats" to stats.mapValues { it.value.get() },
        )
    }

    companion object {
        fun safeName(name: String): String {
            val keep = name.map { if (it.isLetterOrDigit() || it in "._- ()") it else '_' }.joinToString("").trim()
            return keep.take(120).ifEmpty { "file" }
        }

        fun sha256(f: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { s ->
                val buf = ByteArray(1 shl 16)
                while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) }
            }
            return md.digest().hex()
        }
    }
}
