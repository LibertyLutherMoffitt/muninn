package com.muninn

/**
 * How hard to look for peers. The Kotlin twin of `scanpolicy.py` — keep the
 * numbers in step, they are compared by a conformance test.
 *
 * Scanning is a trade: a Bluetooth inquiry is slow, floods the band, degrades
 * any link already up, and on a phone costs real battery. Doing it constantly
 * finds a peer sooner; doing it rarely can leave someone two rows away
 * invisible for minutes.
 *
 * No `android.*` imports — see PeerBook.kt for why that matters.
 */
enum class ScanPolicy(
    val label: String,
    /** Gap between full inquiries, ms. The expensive, disruptive part. */
    val inquiryIntervalMs: Long,
    /** Gap between dial sweeps over devices already known, ms. Cheap. */
    val dialIntervalMs: Long,
    /**
     * Retry curve for a device we believe runs Muninn. Capped low: a peer
     * briefly out of range must be picked up quickly when they return, so this
     * backoff exists to avoid hammering, never to give up.
     */
    val peerBackoffBaseMs: Long,
    val peerBackoffMaxMs: Long,
    /**
     * Retry curve for an unidentified device. In a full cabin most of these are
     * headsets, and each probe costs a slow blocking connect, so failures back
     * off hard and far.
     */
    val probeBackoffBaseMs: Long,
    val probeBackoffMaxMs: Long,
    /**
     * Unknown devices probed per sweep. The cap is what stops a crowded cabin
     * starving the peers we actually care about.
     */
    val probeBudget: Int,
) {
    AGGRESSIVE("Aggressive", 30_000, 8_000, 5_000, 45_000, 60_000, 900_000, 6),
    BALANCED("Balanced", 120_000, 15_000, 10_000, 120_000, 300_000, 3_600_000, 3),
    CONSERVATIVE("Conservative", 300_000, 30_000, 30_000, 300_000, 900_000, 7_200_000, 2);

    /** Lowercase, matching the desktop client's stored value. */
    val key: String get() = name.lowercase()

    val description: String
        get() = "inquiry every ${humanise(inquiryIntervalMs)}, " +
            "dial every ${humanise(dialIntervalMs)}, " +
            "$probeBudget new devices per sweep"

    companion object {
        /**
         * Finding peers unattended is the whole point of the app, so the eager
         * setting is what you get unless you ask for otherwise.
         */
        val DEFAULT = AGGRESSIVE

        fun byKey(key: String?): ScanPolicy? =
            entries.firstOrNull { it.key == key?.trim()?.lowercase() }

        private fun humanise(ms: Long): String {
            val seconds = ms / 1000
            return if (seconds < 60) "${seconds}s" else "${seconds / 60}m"
        }
    }
}
