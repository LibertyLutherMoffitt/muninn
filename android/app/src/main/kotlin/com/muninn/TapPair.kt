package com.muninn

import android.annotation.SuppressLint
import android.app.Activity
import android.bluetooth.BluetoothManager
import android.content.Context
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.cardemulation.HostApduService
import android.nfc.tech.IsoDep
import android.os.Bundle
import android.util.Log
import com.muninn.core.Json
import com.muninn.core.hex
import com.muninn.core.str
import com.muninn.core.unhex
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Tap two phones to meet (NFC).
 *
 * Every phone with Muninn answers taps in the background through host card
 * emulation ([TapService], AID F0 "MUNINN"); the phone where the user pressed
 * "Tap phones" reads ([TapPair.startReading]) — only one side may read, a
 * reading phone doesn't answer. One exchange, both directions:
 *
 *   reader → SELECT AID, then  80 10 00 00 <info>     card → <info> 90 00
 *   info = compact JSON {i: node id, k: pubkey hex, n: name, a: our BT address
 *          if a neighbour told us (Android can't read its own), b: BT name}
 *
 * Afterwards both phones know each other's identity and pinned key. Whoever
 * learned the other's address dials it at once; if neither knows one yet
 * (two phones that never met anyone), the reader becomes visible for a
 * moment and both look for the other by Bluetooth name.
 */
object TapPair {
    private const val TAG = "TapPair"
    val AID = "F04D554E494E4E".unhex() // F0 + "MUNINN"
    private val SELECT = byteArrayOf(0x00, 0xA4.toByte(), 0x04, 0x00, AID.size.toByte()) + AID + byteArrayOf(0x00)
    private val OK = byteArrayOf(0x90.toByte(), 0x00)
    const val INS_HELLO: Byte = 0x10

    sealed class State {
        data object Idle : State()
        data object Reading : State()
        data class Connecting(val name: String, val visibleNeeded: Boolean) : State()
        data class Connected(val name: String, val nodeId: String) : State()
        data class Failed(val message: String) : State()
    }

    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state.asStateFlow()

    fun available(ctx: Context) = NfcAdapter.getDefaultAdapter(ctx) != null
    fun enabled(ctx: Context) = NfcAdapter.getDefaultAdapter(ctx)?.isEnabled == true

    @SuppressLint("MissingPermission")
    fun myInfo(ctx: Context): ByteArray? {
        val n = Runtime.node ?: return null
        val bt = runCatching { ctx.getSystemService(BluetoothManager::class.java)?.adapter?.name }.getOrNull() ?: ""
        return Json.write(mapOf(
            "i" to n.nodeId, "k" to n.mesh.pubkey.hex(), "n" to n.mesh.name.take(32),
            "a" to (n.mesh.ownAddr ?: ""), "b" to bt.take(40),
        )).toByteArray()
    }

    // ---- reader side ------------------------------------------------------------

    fun startReading(activity: Activity) {
        val nfc = NfcAdapter.getDefaultAdapter(activity) ?: return
        _state.value = State.Reading
        nfc.enableReaderMode(activity, { tag -> onTag(activity, tag) },
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,
            Bundle().apply { putInt(NfcAdapter.EXTRA_READER_PRESENCE_CHECK_DELAY, 500) })
    }

    fun stopReading(activity: Activity) {
        runCatching { NfcAdapter.getDefaultAdapter(activity)?.disableReaderMode(activity) }
        if (_state.value is State.Reading) _state.value = State.Idle
    }

    fun reset() { _state.value = State.Idle }

    private fun onTag(activity: Activity, tag: Tag) {
        val iso = IsoDep.get(tag) ?: return
        try {
            iso.connect()
            iso.timeout = 3000
            val sel = iso.transceive(SELECT)
            if (!sel.endsWith(OK)) { fail("That phone doesn't have Muninn open in the background (or is locked)"); return }
            val mine = myInfo(activity) ?: run { fail("Muninn is still starting — try again"); return }
            val apdu = byteArrayOf(0x80.toByte(), INS_HELLO, 0x00, 0x00, mine.size.toByte()) + mine + byteArrayOf(0x00)
            val resp = iso.transceive(apdu)
            if (!resp.endsWith(OK)) { fail("The other phone isn't ready — unlock it and try again"); return }
            stopReading(activity)
            met(activity, resp.copyOfRange(0, resp.size - 2), reader = true)
        } catch (e: Exception) {
            Log.w(TAG, "tap failed", e)
            fail("Hold the phones together a bit longer")
        } finally {
            runCatching { iso.close() }
        }
    }

    private fun fail(msg: String) { _state.value = State.Failed(msg) }

    // ---- both sides: we know who they are now ---------------------------------------

    internal fun met(ctx: Context, payload: ByteArray, reader: Boolean) {
        val node = Runtime.node ?: return
        val j = runCatching { Json.obj(String(payload, Charsets.UTF_8)) }.getOrNull() ?: run { fail("Garbled tap — try again"); return }
        val id = j.str("i"); val name = j.str("n").ifEmpty { "someone" }
        val addr = j.str("a"); val btName = j.str("b")
        val pk = runCatching { j.str("k").unhex() }.getOrNull()
        if (id.isEmpty() || pk == null || pk.size != 32) { fail("Garbled tap — try again"); return }
        Log.i(TAG, "met $id ($name) addr=$addr bt=$btName reader=$reader")
        Thread({
            node.mesh.introduce(id, pk, name)
            val iKnowTheirs = addr.isNotEmpty()
            val theyKnowMine = node.mesh.ownAddr != null
            if (reader) _state.value = State.Connecting(name, visibleNeeded = !iKnowTheirs && !theyKnowMine)
            if (iKnowTheirs) {
                node.storage.saveTransport(id, addr) // a known Muninn device now: dialled right away
                node.markSeen(addr)
                node.poke(addr)
            } else if (btName.isNotEmpty()) findByName(node, btName)
            awaitLink(node, id, name, reader)
        }, "tap-$id").apply { isDaemon = true; start() }
    }

    /** Neither address known: look for their phone by its Bluetooth name. */
    private fun findByName(node: com.muninn.core.Node, btName: String) {
        repeat(4) {
            val hit = runCatching { node.scanNow(10_000) }.getOrDefault(emptyList()).firstOrNull { it.name == btName }
            if (hit != null) {
                node.markSeen(hit.addr)
                runCatching { node.connectTo(hit.addr, probe = true) }
                return
            }
        }
    }

    private fun awaitLink(node: com.muninn.core.Node, id: String, name: String, reader: Boolean) {
        val until = System.currentTimeMillis() + 60_000
        while (System.currentTimeMillis() < until) {
            if (id in node.mesh.connected()) {
                if (reader) _state.value = State.Connected(name, id)
                Runtime.toast("Connected to $name")
                Runtime.bump()
                return
            }
            Thread.sleep(400)
        }
        if (reader) fail("Couldn't reach $name over Bluetooth — make one phone visible and try again")
    }
}

/** Answers taps from a reading phone (screen on + unlocked, app needn't be open). */
class TapService : HostApduService() {
    override fun processCommandApdu(apdu: ByteArray, extras: Bundle?): ByteArray {
        val fail = byteArrayOf(0x6A, 0x82.toByte())
        if (apdu.size >= 4 && apdu[0] == 0x00.toByte() && apdu[1] == 0xA4.toByte()) {
            return if (Runtime.node != null) byteArrayOf(0x90.toByte(), 0x00) else fail
        }
        if (apdu.size >= 5 && apdu[1] == TapPair.INS_HELLO) {
            val lc = apdu[4].toInt() and 0xff
            if (apdu.size < 5 + lc) return fail
            val theirs = apdu.copyOfRange(5, 5 + lc)
            val mine = TapPair.myInfo(this) ?: return fail
            TapPair.met(applicationContext, theirs, reader = false)
            return mine + byteArrayOf(0x90.toByte(), 0x00)
        }
        return fail
    }

    override fun onDeactivated(reason: Int) {}
}

private fun ByteArray.endsWith(suffix: ByteArray) =
    size >= suffix.size && copyOfRange(size - suffix.size, size).contentEquals(suffix)
