package com.muninn

import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer
import java.util.UUID

const val TYPE_HANDSHAKE: Byte = 0x01
const val TYPE_MESSAGE: Byte = 0x02
const val TYPE_ACK: Byte = 0x03
const val TYPE_GROUP_SETUP: Byte = 0x04
const val TYPE_READ: Byte = 0x05
const val TYPE_PROFILE: Byte = 0x06
const val TYPE_PEER_ANNC: Byte = 0x07

const val MUNINN_RFCOMM_UUID_STR = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
val MUNINN_RFCOMM_UUID: UUID = UUID.fromString(MUNINN_RFCOMM_UUID_STR)

const val PUBKEY_BYTES = 32
const val NONCE_BYTES = 24
const val MAC_BYTES = 6
const val UUID_BYTES = 16
const val MAX_PAYLOAD = 0xFFFF

class FrameTooLarge(size: Int) :
    IllegalArgumentException("payload $size bytes exceeds max $MAX_PAYLOAD")

data class Frame(val type: Byte, val payload: ByteArray)

data class MessageFrame(
    val groupId: ByteArray,
    val msgId: ByteArray,
    val senderMac: ByteArray,
    val destMac: ByteArray,
    val timestamp: Long,
    val ciphertext: ByteArray, // first 24 bytes = nonce, rest = NaCl Box ciphertext
)

fun encodeFrame(out: DataOutputStream, type: Byte, payload: ByteArray) {
    if (payload.size > MAX_PAYLOAD) throw FrameTooLarge(payload.size)
    out.writeByte(type.toInt())
    out.writeShort(payload.size)
    out.write(payload)
    out.flush()
}

fun readFrame(input: DataInputStream): Frame {
    val type = input.readByte()
    val length = input.readUnsignedShort()
    val payload = ByteArray(length)
    input.readFully(payload)
    return Frame(type, payload)
}

fun encodeHandshake(out: DataOutputStream, pubkey: ByteArray) {
    require(pubkey.size == PUBKEY_BYTES) { "pubkey must be $PUBKEY_BYTES bytes" }
    encodeFrame(out, TYPE_HANDSHAKE, pubkey)
}

fun encodeMessage(
    out: DataOutputStream,
    groupId: ByteArray,
    msgId: ByteArray,
    senderMac: ByteArray,
    destMac: ByteArray,
    encrypted: ByteArray,
) {
    val payload = ByteBuffer.allocate(UUID_BYTES + UUID_BYTES + MAC_BYTES + MAC_BYTES + 4 + encrypted.size)
        .put(groupId)
        .put(msgId)
        .put(senderMac)
        .put(destMac)
        .putInt((System.currentTimeMillis() / 1000L).toInt())
        .put(encrypted)
        .array()
    encodeFrame(out, TYPE_MESSAGE, payload)
}

fun decodeMessage(payload: ByteArray): MessageFrame {
    require(payload.size >= 48) { "message payload too short: ${payload.size}" }
    val buf = ByteBuffer.wrap(payload)
    val groupId = ByteArray(UUID_BYTES).also { buf.get(it) }
    val msgId = ByteArray(UUID_BYTES).also { buf.get(it) }
    val senderMac = ByteArray(MAC_BYTES).also { buf.get(it) }
    val destMac = ByteArray(MAC_BYTES).also { buf.get(it) }
    val timestamp = buf.int.toLong() and 0xFFFFFFFFL
    val ciphertext = ByteArray(buf.remaining()).also { buf.get(it) }
    return MessageFrame(groupId, msgId, senderMac, destMac, timestamp, ciphertext)
}

fun encodeAck(out: DataOutputStream, msgId: ByteArray, fromMac: ByteArray) {
    val payload = ByteArray(UUID_BYTES + MAC_BYTES)
    System.arraycopy(msgId, 0, payload, 0, UUID_BYTES)
    System.arraycopy(fromMac, 0, payload, UUID_BYTES, MAC_BYTES)
    encodeFrame(out, TYPE_ACK, payload)
}

fun decodeAck(payload: ByteArray): Pair<ByteArray, ByteArray> {
    val msgId = payload.copyOfRange(0, UUID_BYTES)
    val fromMac = payload.copyOfRange(UUID_BYTES, UUID_BYTES + MAC_BYTES)
    return msgId to fromMac
}

fun macToBytes(mac: String): ByteArray =
    mac.split(":").map { it.toInt(16).toByte() }.toByteArray().also {
        require(it.size == MAC_BYTES) { "expected 6-octet MAC, got: $mac" }
    }

fun bytesToMac(bytes: ByteArray): String =
    bytes.joinToString(":") { "%02X".format(it.toInt() and 0xFF) }
