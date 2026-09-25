package com.muninn

import android.content.Context
import android.util.Log

/**
 * The one [Mesh] per process, shared by the service (which feeds it
 * Bluetooth sockets) and the UI (which reads and sends through
 * [ChatRepository]). Built on first use by whichever of the two starts first,
 * so a message typed before the radio is up is still stored and queued.
 */
object AppGraph {
    @Volatile private var mesh: Mesh? = null

    fun mesh(ctx: Context): Mesh =
        mesh ?: synchronized(this) { mesh ?: build(ctx.applicationContext).also { mesh = it } }

    private fun build(ctx: Context): Mesh {
        val identity = Identity.load(ctx)
        val store = SqliteMeshStore(ctx)
        val mesh = Mesh(
            identity.wireMacStr,
            identity.pubkey,
            CryptoSealer(identity.privkey),
            store,
            log = { Log.i("Mesh", it) },
        )
        mesh.setDisplayName(displayName(ctx))
        ChatRepository.attach(mesh, store)
        return mesh
    }

    /**
     * The name peers see: the user's own choice if they made one, else the
     * phone's device name — already a name they picked, so it needs no setup.
     */
    fun displayName(ctx: Context): String =
        Settings.displayName(ctx).ifEmpty {
            runCatching {
                android.provider.Settings.Global.getString(ctx.contentResolver, "device_name")
            }.getOrNull()?.takeIf { it.isNotBlank() } ?: android.os.Build.MODEL ?: ""
        }

    fun setDisplayName(ctx: Context, name: String) {
        Settings.setDisplayName(ctx, name.trim())
        mesh(ctx).setDisplayName(displayName(ctx))
    }
}

/** libsodium crypto_box, wire-compatible with PyNaCl's Box. */
class CryptoSealer(private val secret: ByteArray) : Sealer {
    override fun seal(plaintext: ByteArray, theirPub: ByteArray): ByteArray =
        Crypto.encrypt(plaintext, theirPub, secret)

    override fun open(sealed: ByteArray, theirPub: ByteArray): ByteArray =
        Crypto.decrypt(sealed, theirPub, secret)
}
