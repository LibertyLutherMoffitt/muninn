package com.muninn.core

/**
 * Linked devices: signed device sets and the device → person index.
 * Mirror of python/src/muninn/devices.py; wire format in PROTOCOL.md
 * "DEVICES (0x07)", design in DESIGN.md "Linked devices".
 */
object Devices {
    const val MAX_DEVICES = 8

    class Entry(val nodeId: String, val pubkey: ByteArray, val joined: Long, val consent: ByteArray)

    class DeviceSet(val account: String, val version: Int, entries: List<Entry>, var sig: ByteArray = ByteArray(0)) {
        var entries: List<Entry> = entries; private set
        val devices: List<String> get() = entries.map { it.nodeId }
        fun entry(id: String) = entries.firstOrNull { it.nodeId == id }

        /** What the primary signs. */
        fun body(): ByteArray {
            val sb = StringBuilder("muninn-devices\n$account\n$version\n")
            for (e in entries) sb.append("${e.nodeId} ${e.pubkey.hex()} ${e.joined} ${e.consent.hex()}\n")
            return sb.toString().toByteArray()
        }

        fun toJson(): String = Json.write(mapOf(
            "a" to account, "v" to version,
            "d" to entries.map { listOf(it.nodeId, it.pubkey.hex(), it.joined, it.consent.hex()) },
            "s" to sig.hex(),
        ))

        fun sign(priv: ByteArray): DeviceSet {
            entries = entries.sortedBy { it.nodeId }
            sig = XEdDSA.sign(priv, body())
            return this
        }

        companion object {
            fun fromJson(s: String): DeviceSet {
                val j = Json.obj(s)
                val entries = j.list("d").map {
                    val l = it as List<*>
                    Entry(l[0] as String, (l[1] as String).unhex(), (l[2] as Number).toLong(), (l[3] as String).unhex())
                }
                return DeviceSet(j.str("a"), j.int("v"), entries, j.str("s").unhex())
            }
        }
    }

    fun leaveBody(account: String, nodeId: String, joined: Long) = "muninn-leave\n$account\n$nodeId\n$joined".toByteArray()

    /** A device's own signed statement that it left [account] (the membership
     *  that started at [joined]); valid without the first device (see devices.py). */
    class Leave(val account: String, val nodeId: String, val joined: Long, val sig: ByteArray) {
        fun toJson(): String = Json.write(mapOf("leave" to mapOf("a" to account, "n" to nodeId, "j" to joined, "s" to sig.hex())))
        companion object {
            fun fromObj(j: Map<String, Any?>) = Leave(j.str("a"), j.str("n"), j.long("j"), j.str("s").unhex())
        }
    }

    fun makeLeave(priv: ByteArray, account: String, nodeId: String, joined: Long) =
        Leave(account, nodeId, joined, XEdDSA.sign(priv, leaveBody(account, nodeId, joined)))

    fun checkLeave(lv: Leave, pinned: (String) -> ByteArray?): Boolean {
        val pk = pinned(lv.nodeId) ?: return false
        return XEdDSA.verify(pk, leaveBody(lv.account, lv.nodeId, lv.joined), lv.sig)
    }

    fun consentBody(account: String, nodeId: String, joined: Long) = "muninn-join\n$account\n$nodeId\n$joined".toByteArray()

    fun makeConsent(priv: ByteArray, account: String, nodeId: String, joined: Long) =
        XEdDSA.sign(priv, consentBody(account, nodeId, joined))

    /** null if valid, else why not. */
    fun check(ds: DeviceSet, pinned: (String) -> ByteArray?): String? {
        val ids = ds.devices
        if (ds.version < 1 || ids.size !in 1..MAX_DEVICES) return "bad size/version"
        if (ids.toSet().size != ids.size || ids != ids.sorted()) return "devices not unique/sorted"
        val prim = ds.entry(ds.account) ?: return "account not listed"
        for (e in ds.entries) {
            val known = pinned(e.nodeId)
            if (known != null && !known.contentEquals(e.pubkey)) return "key mismatch for ${e.nodeId}"
            if (e.nodeId == ds.account) {
                if (e.joined != 0L || e.consent.isNotEmpty()) return "primary entry carries a consent"
            } else if (!XEdDSA.verify(e.pubkey, consentBody(ds.account, e.nodeId, e.joined), e.consent)) {
                return "no valid consent from ${e.nodeId}"
            }
        }
        if (!XEdDSA.verify(prim.pubkey, ds.body(), ds.sig)) return "bad primary signature"
        return null
    }

    /** All valid sets we know, and which person each device is. Immutable:
     *  the mesh swaps in a new one on change. */
    class Index(val sets: Map<String, DeviceSet> = emptyMap(), val revoked: Map<Pair<String, String>, Long> = emptyMap()) {
        fun newer(ds: DeviceSet) = (sets[ds.account]?.version ?: 0) < ds.version

        fun owner(nodeId: String): String {
            var best = nodeId
            var bestTs = -1L
            for (ds in sets.values) {
                val e = ds.entry(nodeId) ?: continue
                if (ds.entries.size < 2) continue
                if ((revoked[ds.account to nodeId] ?: -1L) >= e.joined) continue // it left (a newer consent brings it back)
                if (e.joined > bestTs) { best = ds.account; bestTs = e.joined }
            }
            return best
        }

        fun devices(person: String): List<String> {
            val ds = sets[person] ?: return listOf(person)
            return ds.devices.filter { owner(it) == person }.ifEmpty { listOf(person) }
        }
    }
}
