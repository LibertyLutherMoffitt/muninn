package com.muninn

import android.bluetooth.BluetoothSocket
import android.util.Log
import com.goterl.lazysodium.utils.KeyPair
import java.io.DataInputStream
import java.io.DataOutputStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Per-RFCOMM-socket session: handshake, then receive loop. Matches the
 * desktop client's `peers.py` flow at the protocol level.
 *
 * Milestone 3 scope: handshake → derive shared secret → on Message frame,
 * decrypt and log + ACK. Group setup, relay, profile, peer announcements,
 * unacked-resend — all deferred to milestone 4 (ConnectionManager port).
 */
@android.annotation.SuppressLint("MissingPermission")
class PeerSession(
    private val socket: BluetoothSocket,
    private val identity: Identity.Loaded,
    private val scope: CoroutineScope,
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

    // Serializes writes to the socket: ACKs (recv loop) and outbound messages
    // (UI thread, via ChatRepository) share one DataOutputStream.
    private val writeLock = Any()

    fun start() {
        job = scope.launch(Dispatchers.IO) {
            try {
                runHandshake()
                ChatRepository.registerPeer(peerId, ::sendText)
                receiveLoop()
            } catch (e: Throwable) {
                Log.w(tag, "session ($remoteAddress) ended: ${e.javaClass.simpleName}: ${e.message}")
            } finally {
                ChatRepository.unregisterPeer(peerId)
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
        Log.i(tag, "sending handshake (pubkey ${identity.pubkey.size}b + wire id ${identity.wireMacStr})")
        encodeHandshake(output, identity.pubkey, identity.wireMac)

        val frame = readFrame(input)
        require(frame.type == TYPE_HANDSHAKE) {
            "expected handshake, got type 0x%02x".format(frame.type.toInt() and 0xFF)
        }
        // 32 = legacy (pubkey only); 38 = pubkey + 6-byte wire id.
        require(frame.payload.size == PUBKEY_BYTES || frame.payload.size == PUBKEY_BYTES + MAC_BYTES) {
            "handshake payload must be $PUBKEY_BYTES or ${PUBKEY_BYTES + MAC_BYTES} bytes, got ${frame.payload.size}"
        }
        peerPubkey = frame.payload.copyOfRange(0, PUBKEY_BYTES)
        peerWireMac =
            if (frame.payload.size >= PUBKEY_BYTES + MAC_BYTES) {
                frame.payload.copyOfRange(PUBKEY_BYTES, PUBKEY_BYTES + MAC_BYTES)
            } else {
                // Legacy peer (bare 32-byte handshake): fall back to its
                // transport BT address. macToBytes throws on the API-31
                // sentinel 02:00:..; getOrNull keeps the session recv-only.
                runCatching { macToBytes(remoteAddress) }.getOrNull()
            }
        peerId = peerWireMac?.let { bytesToMac(it) } ?: remoteAddress
        Log.i(tag, "handshake OK; shared secret derived; peer wire id $peerId")
    }

    /** Encode, encrypt, and send a 1:1 message to this peer. UI thread. */
    private fun sendText(text: String): Boolean {
        val pub = peerPubkey ?: return false
        val dest = peerWireMac ?: return false
        return try {
            val encrypted = Crypto.encrypt(text.toByteArray(Charsets.UTF_8), pub, identity.privkey)
            synchronized(writeLock) {
                encodeMessage(output, ZERO_GROUP_ID, newMsgId(), identity.wireMac, dest, encrypted)
            }
            Log.i(tag, "sent MSG to $peerId (${text.length} chars)")
            true
        } catch (e: Throwable) {
            Log.w(tag, "send to $peerId failed: ${e.message}")
            false
        }
    }

    private fun receiveLoop() {
        val pub = peerPubkey ?: error("receiveLoop without peer pubkey")
        while (true) {
            val frame = readFrame(input)
            when (frame.type) {
                TYPE_MESSAGE -> handleMessage(frame.payload, pub)
                TYPE_ACK -> {
                    val (msgId, from) = decodeAck(frame.payload)
                    Log.i(tag, "ACK msg=${msgId.toHex()} from=${bytesToMac(from)}")
                }
                TYPE_PROFILE,
                TYPE_PEER_ANNC,
                TYPE_GROUP_SETUP,
                TYPE_READ,
                -> Log.d(tag, "unhandled frame type 0x%02x len=${frame.payload.size}".format(frame.type.toInt()))
                else -> Log.w(tag, "unknown frame type 0x%02x".format(frame.type.toInt() and 0xFF))
            }
        }
    }

    private fun handleMessage(payload: ByteArray, peerPub: ByteArray) {
        val msg = decodeMessage(payload)
        Log.i(
            tag,
            "MSG group=${msg.groupId.toHex()} id=${msg.msgId.toHex()} " +
                "sender=${bytesToMac(msg.senderMac)} dest=${bytesToMac(msg.destMac)}"
        )

        // Only decrypt messages addressed to us. Anything else would be relay
        // traffic in the full protocol — out of scope for milestone 3, drop.
        if (!msg.destMac.contentEquals(identity.wireMac)) {
            Log.d(tag, "  not for us; ignoring (relay TBD)")
            return
        }

        try {
            val plaintext = Crypto.decrypt(msg.ciphertext, peerPub, identity.privkey)
            val text = plaintext.toString(Charsets.UTF_8)
            Log.i(tag, "  decrypted: $text")
            ChatRepository.onIncoming(bytesToMac(msg.senderMac), text)
            synchronized(writeLock) { encodeAck(output, msg.msgId, identity.wireMac) }
            Log.i(tag, "  ACK sent")
        } catch (e: SecurityException) {
            Log.w(tag, "  decrypt failed: ${e.message}")
        }
    }
}

private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
