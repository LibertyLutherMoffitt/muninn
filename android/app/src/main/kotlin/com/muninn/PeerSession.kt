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
) {
    private val tag = "PeerSession"
    private val input = DataInputStream(socket.inputStream)
    private val output = DataOutputStream(socket.outputStream)

    @Volatile private var peerPubkey: ByteArray? = null
    private var job: Job? = null

    fun start() {
        job = scope.launch(Dispatchers.IO) {
            try {
                runHandshake()
                receiveLoop()
            } catch (e: Throwable) {
                Log.w(tag, "session ($remoteAddress) ended: ${e.javaClass.simpleName}: ${e.message}")
            } finally {
                runCatching { socket.close() }
            }
        }
    }

    fun stop() {
        runCatching { socket.close() }
        job?.cancel()
    }

    private fun runHandshake() {
        Log.i(tag, "sending handshake (pubkey ${identity.pubkey.size}b)")
        encodeHandshake(output, identity.pubkey)

        val frame = readFrame(input)
        require(frame.type == TYPE_HANDSHAKE) {
            "expected handshake, got type 0x%02x".format(frame.type.toInt() and 0xFF)
        }
        require(frame.payload.size == PUBKEY_BYTES) {
            "handshake payload must be $PUBKEY_BYTES bytes, got ${frame.payload.size}"
        }
        peerPubkey = frame.payload
        Log.i(tag, "handshake OK; shared secret derived")
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
            Log.i(tag, "  decrypted: ${plaintext.toString(Charsets.UTF_8)}")
            encodeAck(output, msg.msgId, identity.wireMac)
            Log.i(tag, "  ACK sent")
        } catch (e: SecurityException) {
            Log.w(tag, "  decrypt failed: ${e.message}")
        }
    }
}

private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
