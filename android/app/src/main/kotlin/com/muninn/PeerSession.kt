package com.muninn

import android.bluetooth.BluetoothSocket
import android.util.Log
import java.io.DataInputStream
import java.io.DataOutputStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * One RFCOMM session: handshake, then a receive loop over the frame types in
 * PROTOCOL.md. The Android counterpart of a single peer entry in `peers.py`.
 *
 * Shared peer state (keys, names, dedup, presence) lives in [PeerBook] so it is
 * unit-testable without an Android SDK; this class is only the socket wiring.
 */
@android.annotation.SuppressLint("MissingPermission")
class PeerSession(
    private val socket: BluetoothSocket,
    private val identity: Identity.Loaded,
    private val book: PeerBook,
    private val scope: CoroutineScope,
    /** Our own display name, announced to the peer after the handshake. */
    private val displayName: String = "",
    val remoteAddress: String = socket.remoteDevice.address,
    // Invoked once when the session ends (handshake failure or socket close) so
    // the owner can drop it from its registry and allow a redial. Without this
    // a dead session lingers and blocks all future reconnects to this peer.
    private val onClosed: (PeerSession) -> Unit = {},
) {
    private val tag = "PeerSession"
    private val input = DataInputStream(socket.inputStream)
    private val output = DataOutputStream(socket.outputStream)

    @Volatile private var peerPubkey: ByteArray? = null
    // The peer's wire id, learned from its handshake — its addressing identity,
    // used as the dest when we send to it. Falls back to the transport address
    // for a legacy peer that sent a bare 32-byte handshake.
    @Volatile private var peerWireMac: ByteArray? = null
    @Volatile private var peerId: String = remoteAddress
    private var job: Job? = null

    // Serializes writes: receipts (recv loop) and outbound messages (UI thread,
    // via ChatRepository) share one DataOutputStream.
    private val writeLock = Any()

    fun start() {
        job = scope.launch(Dispatchers.IO) {
            try {
                runHandshake()
                book.recordConnected(peerId)
                ChatRepository.registerPeer(peerId, ::sendText, ::sendRead)
                announceSelf()
                receiveLoop()
            } catch (e: Throwable) {
                Log.w(tag, "session ($remoteAddress) ended: ${e.javaClass.simpleName}: ${e.message}")
            } finally {
                ChatRepository.unregisterPeer(peerId)
                book.recordDisconnected(peerId)
                runCatching { socket.close() }
                onClosed(this@PeerSession)
            }
        }
    }

    fun stop() {
        runCatching { socket.close() }
        job?.cancel()
    }

    private fun runHandshake() {
        Log.i(tag, "sending handshake (wire id ${identity.wireMacStr})")
        encodeHandshake(output, identity.pubkey, identity.wireMac)

        val frame = readFrame(input)
        require(frame.type == TYPE_HANDSHAKE) {
            "expected handshake, got type 0x%02x".format(frame.type.toInt() and 0xFF)
        }
        val (pubkey, wireId) = decodeHandshake(frame.payload)
        peerPubkey = pubkey
        peerWireMac = wireId
            // Legacy peer (bare 32-byte handshake): fall back to its transport
            // BT address. macToBytes rejects the API-31 sentinel 02:00:.., so
            // getOrNull keeps such a session recv-only rather than crashing.
            ?: runCatching { macToBytes(remoteAddress) }.getOrNull()
        peerId = peerWireMac?.let { bytesToMac(it) } ?: remoteAddress
        book.learnDirect(peerId, pubkey)
        Log.i(tag, "handshake OK; peer wire id $peerId")
    }

    /**
     * Introduce ourselves, exactly as `peers.py:add_peer` does: our display
     * name, then the peers we already know so this peer can learn about
     * devices that are not in its own range.
     */
    private fun announceSelf() {
        if (displayName.isNotEmpty()) {
            runCatching { synchronized(writeLock) { encodeProfile(output, displayName) } }
        }
        val entries = book.knownPeers()
            .filter { it != peerId && it != identity.wireMacStr }
            .mapNotNull { id ->
                book.pubkey(id)?.let { PeerEntry(macToBytes(id), it, book.displayName(id).takeIf { n -> n != id } ?: "") }
            }
        if (entries.isNotEmpty()) {
            runCatching { synchronized(writeLock) { encodePeerAnnc(output, entries) } }
        }
    }

    /** Encode, encrypt, and send a 1:1 message to this peer. */
    private fun sendText(text: String): Boolean {
        val pub = peerPubkey ?: return false
        val dest = peerWireMac ?: return false
        return try {
            val encrypted = Crypto.encrypt(text.toByteArray(Charsets.UTF_8), pub, identity.privkey)
            synchronized(writeLock) {
                encodeMessage(output, ZERO_GROUP_ID, newMsgId(), identity.wireMac, dest, encrypted)
            }
            true
        } catch (e: Throwable) {
            Log.w(tag, "send to $peerId failed: ${e.message}")
            false
        }
    }

    /** Tell the sender their message was displayed. */
    private fun sendRead(msgId: ByteArray): Boolean = try {
        synchronized(writeLock) { encodeRead(output, msgId, identity.wireMac) }
        true
    } catch (e: Throwable) {
        Log.w(tag, "read receipt to $peerId failed: ${e.message}")
        false
    }

    private fun receiveLoop() {
        while (true) {
            val frame = readFrame(input)
            try {
                dispatch(frame)
            } catch (e: MalformedFrame) {
                // A peer sending nonsense should cost us one frame, not the
                // whole session.
                Log.w(tag, "dropping malformed frame from $peerId: ${e.message}")
            }
        }
    }

    private fun dispatch(frame: Frame) {
        when (frame.type) {
            TYPE_MESSAGE -> handleMessage(frame.payload)
            TYPE_ACK -> {
                val (msgId, from) = decodeAck(frame.payload)
                ChatRepository.onAck(bytesToMac(from), msgId)
            }
            TYPE_READ -> {
                val (msgId, from) = decodeRead(frame.payload)
                ChatRepository.onRead(bytesToMac(from), msgId)
            }
            TYPE_PROFILE -> {
                val name = decodeProfile(frame.payload)
                book.setSelfChosenName(peerId, name)
                ChatRepository.onPeerRenamed(peerId, book.displayName(peerId))
            }
            TYPE_PEER_ANNC -> handlePeerAnnc(frame.payload)
            TYPE_GROUP_SETUP -> {
                // Groups are desktop-only for now. Recording the members' keys
                // still costs nothing and means a later 1:1 with one of them
                // works without waiting to meet them directly.
                val setup = decodeGroupSetup(frame.payload)
                for (m in setup.members) book.learnRelayed(bytesToMac(m.mac), m.pubkey)
                Log.i(tag, "group '${setup.name}' (${setup.members.size} members) noted")
            }
            else -> Log.w(tag, "unknown frame type 0x%02x".format(frame.type.toInt() and 0xFF))
        }
    }

    private fun handlePeerAnnc(payload: ByteArray) {
        for (entry in decodePeerAnnc(payload)) {
            val id = bytesToMac(entry.mac)
            if (id == identity.wireMacStr) continue
            // Relayed keys never overwrite one from a handshake — see PeerBook.
            book.learnRelayed(id, entry.pubkey)
            if (entry.name.isNotEmpty()) book.setSelfChosenName(id, entry.name)
            book.recordRelay(id, peerId)
        }
    }

    private fun handleMessage(payload: ByteArray) {
        val msg = decodeMessage(payload)

        // Relaying is desktop-only for now; anything not addressed to us is
        // dropped rather than forwarded.
        if (!msg.destMac.contentEquals(identity.wireMac)) return

        // A sender retransmits every unacked message after a reconnect, so
        // without this the same text appears twice in the thread.
        if (!book.claimSeen(msg.msgId)) {
            ackTo(msg.msgId)
            return
        }

        val sender = bytesToMac(msg.senderMac)
        // Decrypt with the *sender's* key, not the socket peer's. They are the
        // same for a direct message and differ for anything relayed.
        val senderKey = book.pubkey(sender)
        if (senderKey == null) {
            // Release the claim or the sender's retransmit — once we learn
            // their key — would be silently dropped forever.
            book.releaseSeen(msg.msgId)
            Log.w(tag, "no key for $sender; dropping message")
            return
        }

        val text = try {
            Crypto.decrypt(msg.ciphertext, senderKey, identity.privkey).toString(Charsets.UTF_8)
        } catch (e: SecurityException) {
            book.releaseSeen(msg.msgId)
            Log.w(tag, "decrypt failed from $sender: ${e.message}")
            return
        }

        ChatRepository.onIncoming(sender, text, msg.msgId, msg.timestamp)
        ackTo(msg.msgId)
    }

    private fun ackTo(msgId: ByteArray) {
        runCatching { synchronized(writeLock) { encodeAck(output, msgId, identity.wireMac) } }
            .onFailure { Log.w(tag, "ACK to $peerId failed: ${it.message}") }
    }
}
