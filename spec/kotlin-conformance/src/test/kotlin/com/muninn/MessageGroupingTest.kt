package com.muninn

import java.io.File
import java.util.Locale
import java.util.TimeZone
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Mirrors the day-divider and message-run rules in the desktop client's
 * `gui/models.py`. A thread that groups differently on the phone and the
 * laptop reads as a bug in whichever one you looked at second.
 */
class MessageGroupingTest {

    private val utc = TimeZone.getTimeZone("UTC")
    private val en = Locale.UK
    private val day = 86_400_000L
    private val peer = "BB:BB:BB:BB:BB:BB"

    private fun msg(
        peer: String = this.peer,
        at: Long,
        outgoing: Boolean = false,
    ) = ChatRepository.Message(peer, "hi", outgoing, at)

    private fun section(at: Long, now: Long) =
        MessageGrouping.daySection(at, now, utc, en)

    // --- Day sections ---

    @Test
    fun `today and yesterday are named`() {
        val now = 1_700_000_000_000L
        assertEquals("Today", section(now, now))
        assertEquals("Yesterday", section(now - day, now))
    }

    @Test
    fun `a day boundary counts, not a 24-hour gap`() {
        // 00:30 and 23:30 the previous evening are one hour apart but two days.
        val now = 1_700_000_000_000L
        val midnight = now - (now % day)
        assertEquals("Today", section(midnight + 1_800_000, now))
        assertEquals("Yesterday", section(midnight - 1_800_000, now))
    }

    @Test
    fun `the last week uses weekday names`() {
        val now = 1_700_000_000_000L
        val label = section(now - day * 3, now)
        assertTrue(
            label in listOf(
                "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday",
            ),
            "expected a weekday, got $label",
        )
    }

    @Test
    fun `older dates are explicit and a previous year carries the year`() {
        val now = 1_700_000_000_000L
        assertTrue(section(now - day * 30, now).any { it.isDigit() })
        assertEquals(3, section(now - day * 400, now).split(" ").size)
    }

    // --- Runs ---

    @Test
    fun `the first message always starts a run`() {
        assertTrue(MessageGrouping.startsRun(msg(at = 1_000), null))
    }

    @Test
    fun `a quick reply from the same sender continues the run`() {
        val first = msg(at = 1_700_000_000_000L)
        val second = msg(at = first.timestamp + MessageGrouping.RUN_GAP_MS - 1)
        assertFalse(MessageGrouping.startsRun(second, first, now = second.timestamp))
    }

    @Test
    fun `a long pause starts a new run`() {
        val first = msg(at = 1_700_000_000_000L)
        val second = msg(at = first.timestamp + MessageGrouping.RUN_GAP_MS + 1)
        assertTrue(MessageGrouping.startsRun(second, first, now = second.timestamp))
    }

    @Test
    fun `a different sender starts a new run`() {
        val first = msg(at = 1_700_000_000_000L)
        val second = msg(peer = "CC:CC:CC:CC:CC:CC", at = first.timestamp + 1)
        assertTrue(MessageGrouping.startsRun(second, first, now = second.timestamp))
    }

    @Test
    fun `a reply of our own starts a new run`() {
        // Same peer id on both sides of a 1:1 thread; direction is what differs.
        val theirs = msg(at = 1_700_000_000_000L, outgoing = false)
        val ours = msg(at = theirs.timestamp + 1, outgoing = true)
        assertTrue(MessageGrouping.startsRun(ours, theirs, now = ours.timestamp))
    }

    @Test
    fun `crossing midnight starts a new run`() {
        // Otherwise the first message of a day sits under a divider with no name.
        val now = 1_700_000_000_000L
        val first = msg(at = now - day)
        val second = msg(at = now)
        assertTrue(MessageGrouping.startsRun(second, first, now = now))
    }

    // --- Cross-language agreement ---

    @Test
    fun `the run gap matches the desktop client`() {
        val src = File("../../python/src/muninn/gui/models.py").readText()
        val seconds = Regex("RUN_GAP_SECONDS = (\\d+)").find(src)?.groupValues?.get(1)
        requireNotNull(seconds) { "RUN_GAP_SECONDS missing from models.py" }
        assertEquals(seconds.toLong() * 1000, MessageGrouping.RUN_GAP_MS)
    }

    @Test
    fun `the desktop uses the same day labels`() {
        val src = File("../../python/src/muninn/gui/models.py").readText()
        for (label in listOf("Today", "Yesterday")) {
            assertTrue("\"$label\"" in src, "models.py no longer emits $label")
        }
    }
}
