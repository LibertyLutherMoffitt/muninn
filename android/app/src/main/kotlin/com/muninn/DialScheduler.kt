package com.muninn

/**
 * Deciding who to dial next, and when to stop bothering.
 *
 * The Kotlin twin of `dialer.py`. The hard case is a full cabin: forty
 * Bluetooth devices in inquiry range, one of which is the person you want.
 * Every dial is a slow blocking connect, so a loop that treats all devices
 * alike spends its whole cycle on headsets and never reaches the peer.
 *
 * Three rules follow:
 *  - A known peer is never given up on; its retry interval is capped low.
 *  - An unidentified device is probed, then backed off hard — Android's SDP
 *    cache is unreliable enough that trying is the only sure test, but a
 *    refusal is strong evidence it is somebody's earbuds.
 *  - Probes are rationed per sweep so a crowd cannot starve real peers.
 *
 * Pure logic, no Android imports, so `spec/kotlin-conformance` can test the
 * behaviour that is otherwise miserable to reproduce.
 */
class DialScheduler(
    initialPolicy: ScanPolicy = ScanPolicy.DEFAULT,
    private val localId: String = "",
) {
    /**
     * Timings in force. Assigning clears pending backoffs rather than keeping
     * them, so choosing Aggressive takes effect now instead of after the old,
     * longer waits expire.
     */
    @Volatile
    var policy: ScanPolicy = initialPolicy
        set(value) {
            synchronized(lock) {
                if (field == value) return
                field = value
                entries.values.forEach { it.nextAttempt = 0L }
            }
        }

    enum class Kind { PEER, UNKNOWN }

    private class Entry(val addr: String) {
        var kind = Kind.UNKNOWN
        var failures = 0
        var nextAttempt = 0L
        var lastSeen = 0L
        var lastError = ""
        /** Once a device has spoken Muninn it is a peer forever, whatever a
         *  later SDP cache says. */
        var confirmed = false
    }

    /** Known peers first — they are why the app exists. */
    class Plan(val peers: List<String>, val probes: List<String>) {
        val targets: List<String> get() = peers + probes
        val isEmpty: Boolean get() = peers.isEmpty() && probes.isEmpty()
    }

    private val lock = Any()
    private val entries = LinkedHashMap<String, Entry>()

    private fun entry(addr: String) = entries.getOrPut(addr) { Entry(addr) }

    /**
     * The radio can see [addr] right now. [isPeer] means it advertised the
     * Muninn service — a hint, not proof, so it upgrades a device but never
     * downgrades a confirmed one.
     */
    fun saw(addr: String, now: Long, isPeer: Boolean = false) {
        val id = addr.uppercase()
        if (id == localId.uppercase()) return
        synchronized(lock) {
            val e = entry(id)
            e.lastSeen = now
            if (isPeer && e.kind == Kind.UNKNOWN) {
                e.kind = Kind.PEER
                // Newly identified: worth trying now, whatever backoff it
                // accumulated while anonymous.
                e.nextAttempt = 0L
                e.failures = 0
            }
        }
    }

    /** Promote to a known peer — we hold its key, or it has talked to us. */
    fun markPeer(addr: String) {
        val id = addr.uppercase()
        if (id == localId.uppercase()) return
        synchronized(lock) {
            val e = entry(id)
            if (e.kind != Kind.PEER) {
                e.kind = Kind.PEER
                e.failures = 0
                e.nextAttempt = 0L
            }
        }
    }

    /** A session came up. This device is a peer from now on. */
    fun succeeded(addr: String) {
        synchronized(lock) {
            val e = entry(addr.uppercase())
            e.kind = Kind.PEER
            e.confirmed = true
            e.failures = 0
            e.nextAttempt = 0L
            e.lastError = ""
        }
    }

    /** A dial failed. Backs off on the curve for its kind. */
    fun failed(addr: String, now: Long, error: String = "") {
        synchronized(lock) {
            val e = entry(addr.uppercase())
            e.failures += 1
            e.lastError = error
            e.nextAttempt = now + backoffOf(e)
        }
    }

    fun forget(addr: String) {
        synchronized(lock) { entries.remove(addr.uppercase()) }
    }

    internal fun backoffOf(addr: String): Long =
        synchronized(lock) { entries[addr.uppercase()]?.let { backoffOf(it) } ?: 0L }

    private fun backoffOf(e: Entry): Long {
        val base: Long
        val cap: Long
        if (e.kind == Kind.PEER || e.confirmed) {
            base = policy.peerBackoffBaseMs
            cap = policy.peerBackoffMaxMs
        } else {
            base = policy.probeBackoffBaseMs
            cap = policy.probeBackoffMaxMs
        }
        val shift = minOf(e.failures - 1, 16).coerceAtLeast(0)
        val grown = base shl shift
        return if (grown < 0 || grown > cap) cap else grown
    }

    /**
     * Who to dial this sweep. [isConnected] skips live sessions;
     * [visibilityWindowMs] stops us dialling a device that left the cabin an
     * hour ago — pure latency for no chance of success.
     */
    fun plan(
        now: Long,
        isConnected: (String) -> Boolean,
        visibilityWindowMs: Long = 180_000,
    ): Plan {
        synchronized(lock) {
            val peers = ArrayList<Entry>()
            val probes = ArrayList<Entry>()
            for (e in entries.values) {
                if (e.nextAttempt > now) continue
                if (isConnected(e.addr)) continue
                if (e.kind == Kind.PEER || e.confirmed) {
                    // Always worth trying, even if this inquiry missed it —
                    // misses are routine, and our dial may be what finds it.
                    peers.add(e)
                } else if (now - e.lastSeen <= visibilityWindowMs) {
                    probes.add(e)
                }
            }
            // Freshest sighting first: the device that just appeared is most
            // likely the person who just sat down.
            peers.sortWith(compareByDescending<Entry> { it.lastSeen }.thenBy { it.failures })
            probes.sortWith(compareBy<Entry> { it.failures }.thenByDescending { it.lastSeen })
            return Plan(
                peers = peers.map { it.addr },
                probes = probes.take(maxOf(0, policy.probeBudget)).map { it.addr },
            )
        }
    }

    data class Stats(
        val policy: String,
        val tracked: Int,
        val peers: Int,
        val unknown: Int,
        val backingOff: Int,
    )

    fun stats(): Stats = synchronized(lock) {
        Stats(
            policy = policy.key,
            tracked = entries.size,
            peers = entries.values.count { it.kind == Kind.PEER },
            unknown = entries.values.count { it.kind == Kind.UNKNOWN },
            backingOff = entries.values.count { it.failures > 0 },
        )
    }
}
