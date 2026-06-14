package com.muninn

import android.content.Context

/**
 * Persisted set of peer Bluetooth addresses the user has chosen to talk to (via
 * the pairing sheet). The connect loop dials these directly.
 *
 * Why not rely on [android.bluetooth.BluetoothAdapter.bondedDevices]: Muninn
 * uses *insecure* RFCOMM, which needs no bond at all, and Android's bond can
 * silently downgrade to BOND_TYPE_TEMPORARY (dropping the device out of
 * bondedDevices()) after Just-Works pairing — which would strand the peer. An
 * explicit known-peers list decouples us from that flakiness.
 *
 * Prototype scope: a flat address set. Superseded by the storage.py port
 * (Room) in milestone 4.
 */
object KnownPeers {
    private const val PREFS = "muninn.peers"
    private const val KEY = "addresses"

    fun load(ctx: Context): Set<String> =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getStringSet(KEY, emptySet())
            ?.toSet()
            ?: emptySet()

    fun add(ctx: Context, address: String) {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val updated = load(ctx) + address.uppercase()
        prefs.edit().putStringSet(KEY, updated).apply()
    }

    fun remove(ctx: Context, address: String) {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val updated = load(ctx) - address.uppercase()
        prefs.edit().putStringSet(KEY, updated).apply()
    }
}
