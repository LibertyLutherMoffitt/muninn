package com.muninn

import android.content.Context

/**
 * User preferences that are not part of the protocol.
 *
 * The desktop client stores the same choice in its SQLite `settings` table
 * under the same key and the same lowercase values, so the two stay legible to
 * each other and to a human reading either.
 */
object Settings {
    private const val PREFS = "muninn.settings"
    private const val KEY_SCAN_POLICY = "scan_policy"

    fun scanPolicy(ctx: Context): ScanPolicy {
        val stored = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_SCAN_POLICY, null)
        return ScanPolicy.byKey(stored) ?: ScanPolicy.DEFAULT
    }

    fun setScanPolicy(ctx: Context, policy: ScanPolicy) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SCAN_POLICY, policy.key)
            .apply()
    }
}
