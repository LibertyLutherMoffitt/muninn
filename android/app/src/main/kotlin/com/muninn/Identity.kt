package com.muninn

import android.content.Context
import android.util.Base64
import com.goterl.lazysodium.utils.KeyPair
import java.security.SecureRandom

/**
 * Static keypair + 6-byte wire identity, persisted to SharedPreferences so
 * they survive process restarts. Mirrors the desktop client's static-key
 * model from `storage.py:identity` table.
 *
 * Why a fake MAC: BluetoothAdapter.getAddress() returns 02:00:00:00:00:00 to
 * non-system apps on API 31+. The wire protocol uses a 6-byte ID purely as a
 * stable peer identifier, not as a Bluetooth-routable address — so we
 * generate 6 random bytes on first launch and reuse them. Linux side stores
 * this as that peer's MAC; everything else still works.
 *
 * TODO: migrate to Room when porting `storage.py`.
 */
object Identity {
    private const val PREFS = "muninn.identity"
    private const val KEY_PRIV = "x25519_secret_b64"
    private const val KEY_MAC = "wire_mac_b64"

    fun load(ctx: Context): Loaded {
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        val keyPair: KeyPair = prefs.getString(KEY_PRIV, null)?.let { b64 ->
            Crypto.keyPairFromSecret(Base64.decode(b64, Base64.NO_WRAP))
        } ?: Crypto.generateKeyPair().also {
            prefs.edit().putString(KEY_PRIV, Base64.encodeToString(it.secretKey.asBytes, Base64.NO_WRAP)).apply()
        }

        val wireMac: ByteArray = prefs.getString(KEY_MAC, null)?.let {
            Base64.decode(it, Base64.NO_WRAP)
        } ?: ByteArray(MAC_BYTES).also {
            SecureRandom().nextBytes(it)
            // Set locally administered + unicast bit pattern, just so peers
            // can tell this isn't a real OUI-assigned address.
            it[0] = ((it[0].toInt() and 0xFC) or 0x02).toByte()
            prefs.edit().putString(KEY_MAC, Base64.encodeToString(it, Base64.NO_WRAP)).apply()
        }

        return Loaded(keyPair, wireMac)
    }

    data class Loaded(val keyPair: KeyPair, val wireMac: ByteArray) {
        val pubkey: ByteArray get() = keyPair.publicKey.asBytes
        val privkey: ByteArray get() = keyPair.secretKey.asBytes
        val wireMacStr: String get() = bytesToMac(wireMac)
    }
}
