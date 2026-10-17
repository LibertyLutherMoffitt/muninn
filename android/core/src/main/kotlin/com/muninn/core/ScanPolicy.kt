package com.muninn.core

/**
 * Mirror of python/src/muninn/scanpolicy.py — keep the numbers in step (tested).
 *
 * Reconnecting to people you've met never scans (their address is dialled
 * directly); [ReconnectMode] only sets how eagerly. Looking for *new* people
 * (inquiry + polite probes) happens only in find mode: a few minutes after the
 * user asks, or while they know nobody yet.
 */
data class ReconnectMode(val name: String, val label: String, val backoffMax: Double) {
    val description: String get() = "retry someone who dropped out of range at least every ${backoffMax.toInt()}s (never scans)"
}

object ScanPolicy {
    const val SETTING_KEY = "reconnect"
    const val FIND_SECONDS = 300.0
    const val FIND_INQUIRY = 15.0
    const val FIND_PROBES = 3
    const val FIND_JITTER = 8.0
    const val PROBE_BACKOFF = 21600.0
    const val PROBE_RETRY_FIRST = 45.0
    const val PROBE_RETRY_SECOND = 600.0

    val FAST = ReconnectMode("fast", "Fast", 15.0)
    val SAVER = ReconnectMode("saver", "Battery saver", 60.0)
    val ALL = listOf(FAST, SAVER).associateBy { it.name }
    val DEFAULT = FAST
    fun get(name: String?) = ALL[name ?: ""] ?: DEFAULT
}
