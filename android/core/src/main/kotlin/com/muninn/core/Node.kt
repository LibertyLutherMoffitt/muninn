package com.muninn.core

import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/** muninn: true = advertises Muninn, false = other services only, null = unknown. */
data class ScanResult(val addr: String, val name: String, val muninn: Boolean?, val rssi: Int? = null, val kind: String = "other") {
    /** Worth one polite look: an unknown phone or computer. Never headsets etc. */
    val probeable get() = muninn == null && (kind == "phone" || kind == "computer")
}

/** Something that can listen, dial and discover (Bluetooth RFCOMM, sim hub, ...). */
interface Transport {
    val maxParallelDials: Int get() = 1
    fun localAddr(): String
    fun start(onIncoming: (Conn, String) -> Unit)
    fun stop()
    fun connect(addr: String): Conn
    /** Connect to an *unconfirmed* device without pairing or prompting anyone. */
    fun probe(addr: String): Conn = connect(addr)
    /** Muninn-capable devices already known to be nearby, without a new inquiry. */
    fun discover(): List<Pair<String, String>> = emptyList()
    /** Active inquiry (costly: only when useful). */
    fun scan(durationMs: Long = 5000): List<ScanResult> = emptyList()
    fun shouldDefer(addr: String): Boolean = false
    /** Drop any OS-level pairing with this device. */
    fun forget(addr: String) {}
}

/**
 * One device: identity + storage + mesh + transport + dialer. Mirrors
 * python/src/muninn/node.py, including the good-citizen dialling rules:
 * known peers always; Muninn-confirmed devices; only in find mode
 * budget one quiet, pairing-free probe of an *unknown phone or computer*.
 * Headsets, watches and peripherals are never contacted.
 */
class Node(
    val transport: Transport,
    val storage: Storage,
    name: String? = null,
    val log: (String) -> Unit = {},
) {
    val identity: Identity
    val mesh: Mesh
    @Volatile var reconnect: ReconnectMode = ScanPolicy.get(storage.getSetting(ScanPolicy.SETTING_KEY))
        private set
    private val stopped = AtomicBoolean(false)
    private val scanNow = java.util.concurrent.Semaphore(0)
    private val backoff = ConcurrentHashMap<String, Pair<Double, Double>>() // addr -> (nextOk, delay)
    private val deferred = ConcurrentHashMap<String, Double>()
    private val confirmed = ConcurrentHashMap<String, Long>()
    private val probeQueue = java.util.Collections.synchronizedList(ArrayList<String>())
    @Volatile private var hints: Set<String> = emptySet()
    // radio addresses of people the user forgot: never auto-dialled again
    @Volatile private var forgotten: Set<String> =
        (storage.getSetting("forgotten_addrs")?.let { runCatching { Json.parse(it) as? List<*> }.getOrNull() } ?: emptyList<Any>())
            .map { it.toString() }.toSet()
    private val refused = ConcurrentHashMap<String, Long>() // addr -> until (ms)
    private val refusedAt = ConcurrentHashMap<String, Long>()
    private val refusals = ConcurrentHashMap<String, Int>()
    val sightings = ConcurrentHashMap<String, ScanResult>()
    private val sightedAt = ConcurrentHashMap<String, Long>()
    private val dialing = ConcurrentHashMap.newKeySet<String>()
    @Volatile var scanResults: List<ScanResult> = emptyList()
    @Volatile var lastInquiry = 0.0
    @Volatile private var inquiryGap = ScanPolicy.FIND_INQUIRY
    private val scanLock = Any() // one inquiry at a time: concurrent ones cancel each other

    init {
        var ident = storage.getIdentity() ?: storage.createIdentity(Nacl.newSecretKey(), name ?: "")
        if (name != null && name != ident.displayName) {
            val ts = maxOf(P.now(), ident.nameTs + 1)
            storage.setDisplayName(name, ts)
            ident = ident.copy(displayName = name, nameTs = ts)
        }
        identity = ident
        mesh = Mesh(storage, ident.privkey, ident.nodeId, ident.displayName, ident.nameTs, log)
        mesh.addListener(object : MeshListener {
            override fun onPeers() = relaxBackoff()
        })
    }

    val nodeId get() = identity.nodeId

    fun start() {
        mesh.start()
        transport.start { conn, addr ->
            if (addr.uppercase() in forgotten) setForgotten(forgotten - addr.uppercase()) // they came back
            Thread({ mesh.addLink(conn, addr, false) }, "incoming").apply { isDaemon = true; start() }
        }
        Thread({ dialLoop() }, "dialer").apply { isDaemon = true; start() }
        Thread({ scanLoop() }, "scanner").apply { isDaemon = true; start() }
    }

    fun stop() {
        stopped.set(true)
        scanNow.release()
        runCatching { transport.stop() }
        mesh.stop()
    }

    fun setReconnectMode(name: String): ReconnectMode {
        val m = ScanPolicy.ALL[name] ?: throw IllegalArgumentException("reconnect must be one of ${ScanPolicy.ALL.keys}")
        reconnect = m
        storage.setSetting(ScanPolicy.SETTING_KEY, name)
        return m
    }

    // ---- find mode (mirrors node.py) ----------------------------------------------

    @Volatile var findUntil = 0L // wall clock ms
    @Volatile var autoFind = storage.getSetting("auto_find") != "off"
        private set
    fun setAutoFind(on: Boolean) { autoFind = on; storage.setSetting("auto_find", if (on) "on" else "off") }
    /** Looking for new people right now (scans + polite probes)? */
    val finding: Boolean get() = System.currentTimeMillis() < findUntil || (autoFind && mesh.activePeers().isEmpty())
    fun findLeft(): Int = maxOf(0L, (findUntil - System.currentTimeMillis()) / 1000).toInt()
    fun findPeople(seconds: Double = ScanPolicy.FIND_SECONDS) {
        findUntil = maxOf(findUntil, System.currentTimeMillis() + (seconds * 1000).toLong())
        scanNow.release()
    }
    fun stopFinding() { findUntil = 0 }

    // ---- presence ----------------------------------------------------------

    data class Presence(val state: String, val text: String, val hops: Int)

    /** What the UI should say about a person (a linked person: their best-placed device). */
    fun presence(id: String): Presence {
        val devs = mesh.devicesOf(mesh.person(id))
        if (devs.size < 2) return devicePresence(id)
        val order = mapOf("direct" to 0, "relay" to 1, "nearby" to 2, "offline" to 3)
        val best = devs.map { devicePresence(it) }.minWith(compareBy({ order[it.state] }, { if (it.hops == 0) 99 else it.hops }))
        if (best.state != "offline") return best
        val last = synchronized(mesh.lock) { devs.mapNotNull { mesh.peers[it]?.lastSeen }.maxOrNull() } ?: 0
        return Presence("offline", if (last > 0) "last seen ${ago(last)}" else "not seen yet", 0)
    }

    fun devicePresence(node: String): Presence {
        val h = mesh.hops(node)
        if (h == 1) return Presence("direct", "connected directly", 1)
        if (h != null) return Presence("relay", "in range via ${mesh.displayName(mesh.nextHop(node) ?: "")} · $h hops", h)
        val addrs = storage.loadTransports().filter { it.first == node }.map { it.second }
        val seen = addrs.maxOfOrNull { sightedAt[it] ?: 0 } ?: 0
        if (System.currentTimeMillis() - seen < 120_000) return Presence("nearby", "nearby, not connecting yet", 0)
        val last = synchronized(mesh.lock) { mesh.peers[node]?.lastSeen } ?: 0
        return Presence("offline", if (last > 0) "last seen ${ago(last)}" else "not seen yet", 0)
    }

    // ---- dialing -------------------------------------------------------------

    private fun relaxBackoff() {
        val linked = mesh.linkInfo().map { it["addr"] as String }.toSet()
        val now = mono()
        for ((addr, v) in backoff) if (addr !in linked && v.second > 8) backoff[addr] = minOf(v.first, now + 3) to v.second / 2
    }

    fun poke(addr: String? = null) {
        if (addr == null) { backoff.clear(); scanNow.release() } else backoff.remove(addr.uppercase())
    }

    private fun candidates(): List<Pair<String, Boolean>> {
        val linkedNodes = mesh.connected().toSet()
        val out = LinkedHashMap<String, Boolean>()
        val h = HashSet<String>()
        val people = mesh.activePeers().map { it.nodeId }.toSet() + mesh.siblings()
        for ((node, addr, lastOk) in storage.loadTransports()) {
            if (node in linkedNodes || addr in out) continue
            if (lastOk == 0L) {
                // Gossiped by a neighbour (it had a live Muninn link there),
                // never connected ourselves: worth a try while they're a short
                // relay away — or when someone we know / one of our own devices
                // can't be reached any other way (a linked phone we only ever
                // reached through its laptop, which is now off). See node.py.
                val hops = mesh.hops(node)
                val person = mesh.person(node)
                val stranded = ((person in people || node in people) && !mesh.personReachable(person)) ||
                    (node in people && hops == null)
                if (!stranded && (hops == null || hops > HINT_MAX_HOPS)) continue
                h.add(addr)
            }
            out[addr] = false
        }
        hints = h
        val now = System.currentTimeMillis()
        confirmed.entries.sortedByDescending { it.value }.forEach { if (now - it.value < 180_000) out.putIfAbsent(it.key, false) }
        synchronized(probeQueue) { probeQueue.toList() }.forEach { out.putIfAbsent(it, true) }
        val own = runCatching { transport.localAddr().uppercase() }.getOrNull()
        return out.filter { it.key != own && it.key !in forgotten }.map { it.key to it.value }
    }

    private fun dialLoop() {
        while (!stopped.get()) {
            Thread.sleep(500)
            try { candidates().forEach { (a, p) -> maybeDial(a, p) } } catch (e: Throwable) { log("dial loop: $e") }
        }
    }

    private fun maybeDial(addr: String, probe: Boolean) {
        val now = mono()
        if (mesh.hasTransport(addr)) { backoff.remove(addr); probeQueue.remove(addr); return }
        if (now < (backoff[addr]?.first ?: 0.0)) return
        if (transport.shouldDefer(addr)) {
            val first = deferred.getOrPut(addr) { now }
            if (now - first < 6) return
        }
        synchronized(dialing) {
            if (addr in dialing || dialing.size >= transport.maxParallelDials) return
            dialing.add(addr)
        }
        if (probe) probeQueue.remove(addr)
        Thread({ dial(addr, probe) }, "dial").apply { isDaemon = true; start() }
    }

    private fun dial(addr: String, probe: Boolean) {
        // Gossiped addresses are hearsay: dial them like a stranger (never pair).
        val conn = try {
            if (probe || addr in hints) transport.probe(addr) else transport.connect(addr)
        } catch (e: Throwable) {
            if (probe) refuse(addr)
            else {
                val prev = (backoff[addr]?.second ?: 1.0) * 2
                val d = if (addr in hints) minOf(maxOf(prev, HINT_BACKOFF_MIN), HINT_BACKOFF_MAX)
                        else minOf(maxOf(prev, 2.0), reconnect.backoffMax)
                backoff[addr] = (mono() + d * (0.8 + Math.random() * 0.4)) to d
            }
            return
        } finally {
            dialing.remove(addr)
        }
        deferred.remove(addr)
        if (mesh.addLink(conn, addr, true) == null) {
            if (probe) refuse(addr)
            backoff[addr] = (mono() + 2) to 2.0
        } else backoff.remove(addr)
    }

    // ---- discovery -------------------------------------------------------------

    private fun scanLoop() {
        while (!stopped.get()) {
            try {
                transport.discover().forEach { markSeen(it.first) }
                // no scanning unless looking for new people: known ones are dialled by address
                val due = finding && mono() - lastInquiry >= inquiryGap
                val forced = scanNow.tryAcquire()
                if (due || forced) synchronized(scanLock) { ingest(transport.scan(5000)) }
            } catch (e: Throwable) { log("scan: $e") }
            scanNow.tryAcquire((2000 * (0.8 + Math.random() * 0.4)).toLong(), java.util.concurrent.TimeUnit.MILLISECONDS)
                .let { if (it) scanNow.release() }
        }
    }

    /** [manual]: the user asked (Nearby screen) — check every phone/laptop in
     *  sight, including ones that weren't running Muninn last time. */
    private fun ingest(results: List<ScanResult>, manual: Boolean = false) {
        lastInquiry = mono()
        inquiryGap = ScanPolicy.FIND_INQUIRY + Math.random() * ScanPolicy.FIND_JITTER
        scanResults = results
        val now = System.currentTimeMillis()
        var budget = if (manual) MANUAL_PROBES else ScanPolicy.FIND_PROBES
        val known = storage.loadTransports().map { it.second }.toSet()
        for (r in results.sortedByDescending { it.rssi ?: -100 }) {
            val addr = r.addr.uppercase()
            sightings[addr] = r
            sightedAt[addr] = now
            if (r.muninn == true) markSeen(addr)
            else if (r.probeable && budget > 0 && addr !in known && ((refused[addr] ?: 0) < now || (manual && now - (refusedAt[addr] ?: 0) > MANUAL_RECHECK_MS)) &&
                addr !in probeQueue && !mesh.hasTransport(addr)) {
                probeQueue.add(addr); budget--
            }
        }
    }

    /** A probe didn't find Muninn: maybe a radio hiccup the first time (short
     *  pause), after that the device is left alone for hours. */
    private fun refuse(addr: String) {
        val n = refusals.merge(addr, 1, Int::plus)!!
        // first miss is often a busy radio (the other phone mid-inquiry): retry soon
        val wait = when (n) {
            1 -> ScanPolicy.PROBE_RETRY_FIRST * (0.8 + Math.random() * 0.4)
            2 -> ScanPolicy.PROBE_RETRY_SECOND
            else -> ScanPolicy.PROBE_BACKOFF
        }
        val now = System.currentTimeMillis()
        refused[addr] = now + (wait * 1000).toLong()
        refusedAt[addr] = now
    }

    fun markSeen(addr: String) {
        val a = addr.uppercase()
        val fresh = System.currentTimeMillis() - (confirmed[a] ?: 0) > 60_000
        confirmed[a] = System.currentTimeMillis()
        sightedAt[a] = System.currentTimeMillis()
        if (fresh) backoff.remove(a)
    }

    fun scanNow(durationMs: Long = 6000): List<ScanResult> {
        // the user is looking: keep looking for a while (this scan counts)
        findUntil = maxOf(findUntil, System.currentTimeMillis() + (ScanPolicy.FIND_SECONDS * 1000).toLong())
        return synchronized(scanLock) { transport.scan(durationMs).also { ingest(it, manual = true) } }
    }

    /** What the Nearby screen shows for a scanned device. */
    fun deviceState(addr: String): String {
        val a = addr.uppercase()
        val now = System.currentTimeMillis()
        return when {
            mesh.hasTransport(a) -> "connected"
            a in dialing || a in probeQueue -> "checking"
            now - (confirmed[a] ?: 0) < 180_000 -> "muninn"
            (refused[a] ?: 0) > now -> "not_muninn"
            sightings[a]?.probeable == false && sightings[a]?.muninn != true -> "ignored"
            else -> "unknown"
        }
    }

    private fun setForgotten(a: Set<String>) {
        forgotten = a
        storage.setSetting("forgotten_addrs", Json.write(a.sorted()))
    }

    /** Forget (optionally block) a person and never auto-dial them again. */
    fun forget(id: String, block: Boolean = false) {
        val addrs = mesh.forget(id, block).map { it.uppercase() }.toSet()
        setForgotten(forgotten + addrs)
        addrs.forEach { a -> runCatching { transport.forget(a) } }
    }

    fun connectTo(addr: String, probe: Boolean = false): String? {
        if (addr.uppercase() in forgotten) setForgotten(forgotten - addr.uppercase())
        backoff.remove(addr.uppercase())
        return mesh.addLink(if (probe) transport.probe(addr) else transport.connect(addr), addr, true)
    }

    fun status(): Map<String, Any?> = mapOf(
        "reconnect" to reconnect.name, "finding" to finding, "find_left" to findLeft(),
        "sighted" to sightedAt.values.count { System.currentTimeMillis() - it < 120_000 },
        "probe_queue" to probeQueue.size,
    )

    companion object {
        const val HINT_MAX_HOPS = 3 // dial gossiped addresses of peers at most this far away
        const val HINT_BACKOFF_MIN = 10.0
        const val HINT_BACKOFF_MAX = 300.0
        const val MANUAL_PROBES = 6 // a user-initiated scan checks at most this many unknowns
        const val MANUAL_RECHECK_MS = 60_000L // ...and may re-check a "not Muninn" verdict this old

        fun ago(ts: Long): String {
            val d = P.now() - ts
            return when {
                d < 90 -> "just now"
                d < 3600 -> "${d / 60} min ago"
                d < 86400 -> "${d / 3600} h ago"
                else -> "${d / 86400} d ago"
            }
        }
    }
}
