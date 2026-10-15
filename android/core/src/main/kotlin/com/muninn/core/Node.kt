package com.muninn.core

import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

data class ScanResult(val addr: String, val name: String, val muninn: Boolean, val rssi: Int? = null)

/** Something that can listen, dial and discover (Bluetooth RFCOMM, sim hub, ...). */
interface Transport {
    val maxParallelDials: Int get() = 1
    fun localAddr(): String
    fun start(onIncoming: (Conn, String) -> Unit)
    fun stop()
    fun connect(addr: String): Conn
    /** Muninn-capable devices already known to be nearby, without a new inquiry. */
    fun discover(): List<Pair<String, String>> = emptyList()
    /** Active inquiry (costly: only when useful). */
    fun scan(durationMs: Long = 5000): List<ScanResult> = emptyList()
    fun shouldDefer(addr: String): Boolean = false
}

/**
 * One device: identity + storage + mesh + transport + dialer. Mirrors
 * python/src/muninn/node.py.
 *
 * Dialing policy (good Bluetooth citizen): only addresses we have talked to
 * before or that SDP/sim confirmed speak Muninn are ever dialed; everything
 * else is left alone.
 */
class Node(
    val transport: Transport,
    val storage: Storage,
    name: String? = null,
    val log: (String) -> Unit = {},
    /** Background inquiry. Off = only manual scans + known peers + inbound. */
    var autoScan: Boolean = true,
) {
    val identity: Identity
    val mesh: Mesh
    private val stopped = AtomicBoolean(false)
    private val backoff = ConcurrentHashMap<String, Pair<Double, Double>>() // addr -> (nextOk, delay)
    private val deferred = ConcurrentHashMap<String, Double>()
    private val discovered = ConcurrentHashMap<String, Long>()
    private val dialing = ConcurrentHashMap.newKeySet<String>()
    @Volatile var scanResults: List<ScanResult> = emptyList()

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
        transport.start { conn, addr -> Thread({ mesh.addLink(conn, addr, false) }, "incoming").apply { isDaemon = true; start() } }
        Thread({ dialLoop() }, "dialer").apply { isDaemon = true; start() }
        Thread({ scanLoop() }, "scanner").apply { isDaemon = true; start() }
    }

    fun stop() {
        stopped.set(true)
        runCatching { transport.stop() }
        mesh.stop()
    }

    private fun relaxBackoff() {
        val linked = mesh.linkInfo().map { it["addr"] as String }.toSet()
        val now = mono()
        for ((addr, v) in backoff) if (addr !in linked && v.second > 8) backoff[addr] = minOf(v.first, now + 3) to v.second / 2
    }

    fun poke(addr: String? = null) { if (addr == null) backoff.clear() else backoff.remove(addr.uppercase()) }

    private fun candidates(): List<String> {
        val linkedNodes = mesh.connected().toSet()
        val out = LinkedHashSet<String>()
        val now = System.currentTimeMillis()
        discovered.entries.sortedByDescending { it.value }.forEach { if (now - it.value < 180_000) out.add(it.key) }
        for ((node, addr, _) in storage.loadTransports()) if (node !in linkedNodes) out.add(addr)
        val own = runCatching { transport.localAddr().uppercase() }.getOrNull()
        return out.filter { it != own }
    }

    private fun dialLoop() {
        while (!stopped.get()) {
            Thread.sleep(500)
            try { candidates().forEach { maybeDial(it) } } catch (e: Throwable) { log("dial loop: $e") }
        }
    }

    private fun maybeDial(addr: String) {
        val now = mono()
        if (mesh.hasTransport(addr)) { backoff.remove(addr); return }
        if (now < (backoff[addr]?.first ?: 0.0)) return
        if (transport.shouldDefer(addr)) {
            val first = deferred.getOrPut(addr) { now }
            if (now - first < 6) return
        }
        synchronized(dialing) {
            if (addr in dialing || dialing.size >= transport.maxParallelDials) return
            dialing.add(addr)
        }
        Thread({ dial(addr) }, "dial").apply { isDaemon = true; start() }
    }

    private fun dial(addr: String) {
        val conn = try {
            transport.connect(addr)
        } catch (e: Throwable) {
            val d = minOf(maxOf((backoff[addr]?.second ?: 1.0) * 2, 2.0), 45.0)
            backoff[addr] = (mono() + d * (0.8 + Math.random() * 0.4)) to d
            return
        } finally {
            dialing.remove(addr)
        }
        deferred.remove(addr)
        if (mesh.addLink(conn, addr, true) == null) backoff[addr] = (mono() + 2) to 2.0 else backoff.remove(addr)
    }

    private fun scanLoop() {
        var first = true
        while (!stopped.get()) {
            try {
                transport.discover().forEach { markSeen(it.first) }
                if (autoScan && (first || mesh.connected().isEmpty())) {
                    scanResults = transport.scan(5000).also { r -> r.filter { it.muninn }.forEach { markSeen(it.addr) } }
                }
            } catch (e: Throwable) { log("scan: $e") }
            first = false
            val interval = if (mesh.connected().isEmpty()) 20_000L else 120_000L
            Thread.sleep((interval * (0.8 + Math.random() * 0.4)).toLong())
        }
    }

    fun markSeen(addr: String) {
        val a = addr.uppercase()
        val fresh = System.currentTimeMillis() - (discovered[a] ?: 0) > 60_000
        discovered[a] = System.currentTimeMillis()
        if (fresh) backoff.remove(a)
    }

    fun scanNow(durationMs: Long = 6000): List<ScanResult> =
        transport.scan(durationMs).also { r -> scanResults = r; r.filter { it.muninn }.forEach { markSeen(it.addr) } }

    fun connectTo(addr: String): String? {
        backoff.remove(addr.uppercase())
        return mesh.addLink(transport.connect(addr), addr, true)
    }
}
