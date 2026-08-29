package com.muninn

/**
 * Peer state for the Android client: keys, display names, message dedup, and
 * presence. The Kotlin counterpart of `groups.py` + `presence.py`.
 *
 * Like Protocol.kt this file has no `android.*` imports, so it is compiled and
 * unit-tested on a plain JVM by `spec/kotlin-conformance`. Keep it that way:
 * this is where the rules that must match the desktop client live, and rules
 * that cannot be tested drift.
 *
 * All state is keyed by *wire id* (the 6-byte addressing identity a peer sends
 * in its handshake, rendered "AA:BB:CC:DD:EE:FF"), never by the transport
 * Bluetooth address. On Android the two differ: API 31+ hides the hardware MAC
 * from apps, so this client announces a stable random id instead.
 *
 * Thread safety: every public method synchronizes on `lock`. Sessions run on
 * IO dispatchers and the UI reads from the main thread.
 */
class PeerBook {

    /** Mirrors `presence.py` — keep the two in step. */
    enum class State { CONNECTED, RELAY, NEARBY, OFFLINE }

    data class PeerStatus(
        val wireId: String,
        val state: State = State.OFFLINE,
        val lastSeen: Long? = null,
        val lastConnected: Long? = null,
        val via: String? = null,
        val rssi: Int? = null,
        val failedDials: Int = 0,
        val lastError: String? = null,
    ) {
        /** True when a message sent right now has a path to this peer. */
        val isReachable: Boolean get() = state == State.CONNECTED || state == State.RELAY

        /**
         * Visible to the radio, but no session can be established — usually the
         * peer needs to open the app, enable Bluetooth, or re-accept pairing.
         */
        val unreachableNearby: Boolean
            get() = state == State.NEARBY && failedDials >= UNREACHABLE_AFTER

        fun describe(now: Long = System.currentTimeMillis()): String = when {
            state == State.CONNECTED -> "connected"
            state == State.RELAY -> via?.let { "via $it" } ?: "via relay"
            state == State.NEARBY && unreachableNearby ->
                "nearby, can't connect · seen ${formatAgo(agoMillis(now))}"
            state == State.NEARBY -> "nearby · seen ${formatAgo(agoMillis(now))}"
            lastSeen == null -> "never seen"
            else -> "last seen ${formatAgo(agoMillis(now))}"
        }

        private fun agoMillis(now: Long): Long? = lastSeen?.let { maxOf(0L, now - it) }
    }

    private val lock = Any()

    private val pubkeys = HashMap<String, ByteArray>()
    private val selfChosenNames = HashMap<String, String>()
    private val overrides = HashMap<String, String>()
    private val statuses = HashMap<String, PeerStatus>()
    // Hex of msg_id. A ByteArray cannot be a sensible Set member — its
    // hashCode is identity-based, so every lookup would miss.
    private val seen = HashSet<String>()

    // --- Keys ---

    /** From a completed handshake: authoritative, overwrites. */
    fun learnDirect(wireId: String, pubkey: ByteArray) {
        synchronized(lock) { pubkeys[wireId.uppercase()] = pubkey }
    }

    /**
     * From a PEER_ANNC or GROUP_SETUP relay: fills a gap but never overwrites.
     *
     * Those frames are plaintext, so honouring them over a handshake key would
     * let any relay redirect our encryption for a third party.
     */
    fun learnRelayed(wireId: String, pubkey: ByteArray) {
        synchronized(lock) { pubkeys.putIfAbsent(wireId.uppercase(), pubkey) }
    }

    fun pubkey(wireId: String): ByteArray? = synchronized(lock) { pubkeys[wireId.uppercase()] }

    fun knownPeers(): Set<String> = synchronized(lock) { pubkeys.keys.toSet() }

    // --- Names ---

    /** The peer's own announced name (PROFILE frame). Empty clears it. */
    fun setSelfChosenName(wireId: String, name: String) {
        val id = wireId.uppercase()
        synchronized(lock) {
            // A peer announcing its own MAC means "no name" — older clients
            // defaulted to that. Treat it as unset rather than showing a MAC
            // twice.
            if (name.isEmpty() || name.equals(id, ignoreCase = true)) {
                selfChosenNames.remove(id)
            } else {
                selfChosenNames[id] = name
            }
        }
    }

    /** A name chosen locally by this user. Always wins over the peer's own. */
    fun setOverride(wireId: String, name: String) {
        val id = wireId.uppercase()
        synchronized(lock) {
            if (name.isEmpty()) overrides.remove(id) else overrides[id] = name
        }
    }

    fun displayName(wireId: String): String {
        val id = wireId.uppercase()
        return synchronized(lock) { overrides[id] ?: selfChosenNames[id] ?: id }
    }

    /** Map a display name (or MAC) back to a wire id. Case-insensitive. */
    fun resolve(query: String): String? {
        val upper = query.uppercase()
        synchronized(lock) {
            if (upper in pubkeys || upper in selfChosenNames || upper in overrides) return upper
            overrides.entries.firstOrNull { it.value.equals(query, true) }?.let { return it.key }
            selfChosenNames.entries
                .firstOrNull { it.value.equals(query, true) && it.key !in overrides }
                ?.let { return it.key }
        }
        return null
    }

    // --- Dedup ---

    /**
     * First-wins claim on a message id. Returns true the first time only.
     *
     * A sender retransmits every unacked message after a reconnect, so without
     * this the same message is shown twice.
     */
    fun claimSeen(msgId: ByteArray): Boolean = synchronized(lock) { seen.add(msgId.toHex()) }

    /**
     * Undo a claim. Call when a message could not be decrypted, or the sender's
     * retransmit — once we hold their key — would be silently dropped forever.
     */
    fun releaseSeen(msgId: ByteArray) {
        synchronized(lock) { seen.remove(msgId.toHex()) }
    }

    // --- Presence ---

    private fun mutate(wireId: String, block: (PeerStatus) -> PeerStatus): PeerStatus {
        val id = wireId.uppercase()
        return synchronized(lock) {
            val next = block(statuses[id] ?: PeerStatus(id))
            statuses[id] = next
            next
        }
    }

    fun recordSighting(wireId: String, rssi: Int? = null, now: Long = System.currentTimeMillis()) {
        mutate(wireId) {
            it.copy(
                lastSeen = now,
                rssi = rssi ?: it.rssi,
                state = if (it.state == State.OFFLINE) State.NEARBY else it.state,
            )
        }
    }

    fun recordConnected(wireId: String, now: Long = System.currentTimeMillis()) {
        mutate(wireId) {
            it.copy(
                state = State.CONNECTED,
                lastSeen = now,
                lastConnected = now,
                via = null,
                failedDials = 0,
                lastError = null,
            )
        }
    }

    /**
     * A session ended. Drops to NEARBY, not OFFLINE — the device was here a
     * moment ago, so let the next scan decide whether it has actually gone.
     */
    fun recordDisconnected(wireId: String, now: Long = System.currentTimeMillis()) {
        mutate(wireId) {
            if (it.state == State.CONNECTED) it.copy(state = State.NEARBY, lastSeen = now) else it
        }
    }

    fun recordDialFailure(
        wireId: String,
        error: String = "",
        now: Long = System.currentTimeMillis(),
    ) {
        mutate(wireId) {
            it.copy(
                failedDials = it.failedDials + 1,
                lastError = error.ifEmpty { null },
                lastSeen = now,
                state = if (it.state == State.CONNECTED) it.state else State.NEARBY,
            )
        }
    }

    /** Reachable through `via`. Never downgrades a live direct session. */
    fun recordRelay(wireId: String, via: String) {
        mutate(wireId) {
            if (it.state == State.CONNECTED) it
            else it.copy(state = State.RELAY, via = via.uppercase())
        }
    }

    fun clearRelay(wireId: String, now: Long = System.currentTimeMillis()) {
        mutate(wireId) {
            if (it.state != State.RELAY) it
            else it.copy(via = null, state = if (isRecent(it, now)) State.NEARBY else State.OFFLINE)
        }
    }

    private fun isRecent(status: PeerStatus, now: Long): Boolean =
        status.lastSeen != null && now - status.lastSeen < NEARBY_WINDOW_MS

    /**
     * Apply the nearby-window timeout on read rather than on a timer, so the
     * book needs no background thread and a status can only be stale in the
     * instant between the window expiring and someone asking.
     */
    private fun aged(status: PeerStatus, now: Long): PeerStatus =
        if (status.state == State.NEARBY && !isRecent(status, now)) {
            status.copy(state = State.OFFLINE)
        } else {
            status
        }

    fun status(wireId: String, now: Long = System.currentTimeMillis()): PeerStatus {
        val id = wireId.uppercase()
        return synchronized(lock) { statuses[id]?.let { aged(it, now) } ?: PeerStatus(id) }
    }

    fun statuses(now: Long = System.currentTimeMillis()): Map<String, PeerStatus> =
        synchronized(lock) { statuses.mapValues { aged(it.value, now) } }

    fun connected(): List<String> =
        statuses().filterValues { it.state == State.CONNECTED }.keys.sorted()

    fun nearbyUnreachable(): List<String> =
        statuses().filterValues { it.unreachableNearby }.keys.sorted()

    companion object {
        /**
         * How long after a sighting a device still counts as "nearby". Matches
         * `presence.NEARBY_WINDOW` on the desktop client.
         */
        const val NEARBY_WINDOW_MS = 180_000L

        /**
         * Consecutive failed dials before a visible peer is called unreachable.
         * One failure is normal — RFCOMM connects routinely lose a race with
         * the peer's own outgoing attempt.
         */
        const val UNREACHABLE_AFTER = 2

        /** Compact relative time: "just now", "4m ago", "3h ago", "2d ago". */
        fun formatAgo(millis: Long?): String {
            if (millis == null) return "never"
            val seconds = millis / 1000
            return when {
                seconds < 45 -> "just now"
                seconds < 3600 -> "${seconds / 60}m ago"
                seconds < 86400 -> "${seconds / 3600}h ago"
                else -> "${seconds / 86400}d ago"
            }
        }
    }
}
