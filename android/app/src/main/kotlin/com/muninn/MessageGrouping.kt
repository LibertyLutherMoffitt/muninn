package com.muninn

import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/**
 * Day dividers and message runs — the Kotlin twin of the same logic in the
 * desktop client's `gui/models.py`. A conformance test compares the constants,
 * because a thread that groups differently on the phone and the laptop reads
 * as a bug in whichever one you looked at second.
 *
 * No `android.*` imports, so it can be unit-tested on a plain JVM.
 */
object MessageGrouping {

    /**
     * Messages closer together than this from the same sender are drawn as one
     * run: the name is shown once, at the top.
     */
    const val RUN_GAP_MS = 300_000L

    /** Label for the day divider: Today, Yesterday, a weekday, or a date. */
    fun daySection(
        timestampMs: Long,
        now: Long = System.currentTimeMillis(),
        zone: TimeZone = TimeZone.getDefault(),
        locale: Locale = Locale.getDefault(),
    ): String {
        val day = midnight(timestampMs, zone)
        val today = midnight(now, zone)
        val delta = ((today - day) / 86_400_000L).toInt()
        return when {
            delta == 0 -> "Today"
            delta == 1 -> "Yesterday"
            delta in 2..6 -> format(timestampMs, "EEEE", zone, locale)
            sameYear(timestampMs, now, zone) -> format(timestampMs, "EEE d MMM", zone, locale)
            else -> format(timestampMs, "d MMM yyyy", zone, locale)
        }
    }

    /**
     * True when this message should carry a sender label.
     *
     * A run is broken by a different sender, a day boundary, or a pause long
     * enough that the two no longer read as one burst.
     */
    fun startsRun(
        message: ChatRepository.Message,
        previous: ChatRepository.Message?,
        now: Long = System.currentTimeMillis(),
    ): Boolean {
        if (previous == null) return true
        if (previous.outgoing != message.outgoing) return true
        if (previous.peer != message.peer) return true
        if (daySection(previous.timestamp, now) != daySection(message.timestamp, now)) return true
        return message.timestamp - previous.timestamp > RUN_GAP_MS
    }

    /** Compact relative time, matching `presence.format_ago` on the desktop. */
    fun formatAgo(millis: Long?): String = PeerBook.formatAgo(millis)

    private fun midnight(ms: Long, zone: TimeZone): Long {
        val cal = Calendar.getInstance(zone).apply {
            timeInMillis = ms
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    private fun sameYear(a: Long, b: Long, zone: TimeZone): Boolean {
        val ca = Calendar.getInstance(zone).apply { timeInMillis = a }
        val cb = Calendar.getInstance(zone).apply { timeInMillis = b }
        return ca.get(Calendar.YEAR) == cb.get(Calendar.YEAR)
    }

    private fun format(ms: Long, pattern: String, zone: TimeZone, locale: Locale): String {
        val fmt = java.text.SimpleDateFormat(pattern, locale)
        fmt.timeZone = zone
        return fmt.format(java.util.Date(ms))
    }
}
