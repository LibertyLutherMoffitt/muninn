package com.muninn.core

/** Mirror of python/src/muninn/scanpolicy.py — keep numbers in step (tested). */
data class ScanPolicy(
    val name: String,
    val label: String,
    val inquiryAlone: Double,
    val inquiryLinked: Double,
    val probeBudget: Int,
    val probeBackoff: Double,
    val peerBackoffMax: Double,
) {
    val description: String
        get() {
            fun t(s: Double) = if (s < 60) "${s.toInt()}s" else "${(s / 60).toInt()} min"
            val alone = if (inquiryAlone == 0.0) "never" else "every ${t(inquiryAlone)}"
            val linked = if (inquiryLinked == 0.0) "never" else "every ${t(inquiryLinked)}"
            return "look around $alone when alone, $linked when connected; probe $probeBudget unknown phone/laptop per look"
        }

    companion object {
        const val SETTING_KEY = "scan_mode"
        val EAGER = ScanPolicy("eager", "Eager", 15.0, 60.0, 4, 600.0, 20.0)
        val BALANCED = ScanPolicy("balanced", "Balanced", 25.0, 180.0, 2, 1200.0, 30.0)
        val QUIET = ScanPolicy("quiet", "Quiet", 0.0, 0.0, 0, 3600.0, 45.0)
        val ALL = listOf(EAGER, BALANCED, QUIET).associateBy { it.name }
        val DEFAULT = BALANCED
        fun get(name: String?) = ALL[name ?: ""] ?: DEFAULT
    }
}
