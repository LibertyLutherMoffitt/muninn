package com.muninn

import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer
import java.util.UUID

/**
 * Wire codec — the cross-platform contract with the desktop client.
 *
 * This file is deliberately free of `android.*` imports so it can be compiled
 * and tested on a plain JVM. `spec/kotlin-conformance` does exactly that,
 * running it against `spec/wire-vectors.json` — the same fixture the Python
 * suite checks. Keep it dependency-free so that stays possible.
 *
 * See PROTOCOL.md for the authoritative field layouts.
 */

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

/** Bytes of message metadata that precede the sealed body (group..timestamp). */
const val MESSAGE_HEADER_BYTES = UUID_BYTES + UUID_BYTES + MAC_BYTES + MAC_BYTES + 4

class FrameTooLarge(size: Int) :
    IllegalArgumentException("payload $size bytes exceeds max $MAX_PAYLOAD")

/** A malformed frame from a peer. Never fatal — the caller drops the frame. */
class MalformedFrame(message: String) : IllegalArgumentException(message)

data class Frame(val type: Byte, val payload: ByteArray) {
    // ByteArray's generated equals is identity-based, which makes the default
    // data-class equals useless (and dangerous in a Set). Compare contents.
    override fun equals(other: Any?): Boolean =
        this === other ||
            (other is Frame && type == other.type && payload.contentEquals(other.payload))

    override fun hashCode(): Int = 31 * type.toInt() + payload.contentHashCode()
}

data class MessageFrame(
    val groupId: ByteArray,
    val msgId: ByteArray,
    val senderMac: ByteArray,
    val destMac: ByteArray,
    val timestamp: Long,
    val ciphertext: ByteArray, // first 24 bytes = nonce, rest = NaCl Box ciphertext
) {
    override fun equals(other: Any?): Boolean =
        this === other || (
            other is MessageFrame &&
                groupId.contentEquals(other.groupId) &&
                msgId.contentEquals(other.msgId) &&
                senderMac.contentEquals(other.senderMac) &&
                destMac.contentEquals(other.destMac) &&
                timestamp == other.timestamp &&
                ciphertext.contentEquals(other.ciphertext)
            )

    override fun hashCode(): Int {
        var h = groupId.contentHashCode()
        h = 31 * h + msgId.contentHashCode()
        h = 31 * h + senderMac.contentHashCode()
        h = 31 * h + destMac.contentHashCode()
        h = 31 * h + timestamp.hashCode()
        return 31 * h + ciphertext.contentHashCode()
    }
}

/** One (mac, pubkey) pair inside a GROUP_SETUP frame. */
class GroupMember(val mac: ByteArray, val pubkey: ByteArray)

data class GroupSetupFrame(
    val groupId: ByteArray,
    val members: List<GroupMember>,
    val name: String,
)

/** One (mac, pubkey, name) triple inside a PEER_ANNC frame. */
class PeerEntry(val mac: ByteArray, val pubkey: ByteArray, val name: String)

// ---------------------------------------------------------------------------
// Framing
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Handshake (0x01)
// ---------------------------------------------------------------------------

fun encodeHandshake(out: DataOutputStream, pubkey: ByteArray, wireId: ByteArray) {
    require(pubkey.size == PUBKEY_BYTES) { "pubkey must be $PUBKEY_BYTES bytes" }
    require(wireId.size == MAC_BYTES) { "wireId must be $MAC_BYTES bytes" }
    encodeFrame(out, TYPE_HANDSHAKE, pubkey + wireId)
}

/**
 * Returns (pubkey, wireId). `wireId` is null for the legacy 32-byte form, in
 * which case the caller falls back to the peer's transport Bluetooth address.
 */
fun decodeHandshake(payload: ByteArray): Pair<ByteArray, ByteArray?> {
    if (payload.size != PUBKEY_BYTES && payload.size != PUBKEY_BYTES + MAC_BYTES) {
        throw MalformedFrame(
            "handshake payload must be $PUBKEY_BYTES or ${PUBKEY_BYTES + MAC_BYTES} " +
                "bytes, got ${payload.size}"
        )
    }
    val pubkey = payload.copyOfRange(0, PUBKEY_BYTES)
    val wireId =
        if (payload.size == PUBKEY_BYTES + MAC_BYTES) {
            payload.copyOfRange(PUBKEY_BYTES, PUBKEY_BYTES + MAC_BYTES)
        } else {
            null
        }
    return pubkey to wireId
}

// ---------------------------------------------------------------------------
// Message (0x02)
// ---------------------------------------------------------------------------

fun encodeMessage(
    out: DataOutputStream,
    groupId: ByteArray,
    msgId: ByteArray,
    senderMac: ByteArray,
    destMac: ByteArray,
    encrypted: ByteArray,
    timestamp: Long = System.currentTimeMillis() / 1000L,
) {
    val payload = ByteBuffer.allocate(MESSAGE_HEADER_BYTES + encrypted.size)
        .put(groupId)
        .put(msgId)
        .put(senderMac)
        .put(destMac)
        .putInt(timestamp.toInt()) // wraps to the uint32 the spec defines
        .put(encrypted)
        .array()
    encodeFrame(out, TYPE_MESSAGE, payload)
}

fun decodeMessage(payload: ByteArray): MessageFrame {
    if (payload.size < MESSAGE_HEADER_BYTES) {
        throw MalformedFrame("message payload too short: ${payload.size}")
    }
    val buf = ByteBuffer.wrap(payload)
    val groupId = ByteArray(UUID_BYTES).also { buf.get(it) }
    val msgId = ByteArray(UUID_BYTES).also { buf.get(it) }
    val senderMac = ByteArray(MAC_BYTES).also { buf.get(it) }
    val destMac = ByteArray(MAC_BYTES).also { buf.get(it) }
    // The spec's timestamp is an unsigned 32-bit count of seconds. Masking
    // keeps it positive past 2038 instead of wrapping to a negative Long.
    val timestamp = buf.int.toLong() and 0xFFFFFFFFL
    val ciphertext = ByteArray(buf.remaining()).also { buf.get(it) }
    return MessageFrame(groupId, msgId, senderMac, destMac, timestamp, ciphertext)
}

// ---------------------------------------------------------------------------
// ACK (0x03) and READ (0x05) — identical 22-byte shape
// ---------------------------------------------------------------------------

private fun encodeReceipt(out: DataOutputStream, type: Byte, msgId: ByteArray, fromMac: ByteArray) {
    require(msgId.size == UUID_BYTES) { "msgId must be $UUID_BYTES bytes" }
    require(fromMac.size == MAC_BYTES) { "fromMac must be $MAC_BYTES bytes" }
    encodeFrame(out, type, msgId + fromMac)
}

private fun decodeReceipt(payload: ByteArray, what: String): Pair<ByteArray, ByteArray> {
    if (payload.size < UUID_BYTES + MAC_BYTES) {
        throw MalformedFrame("$what payload too short: ${payload.size}")
    }
    return payload.copyOfRange(0, UUID_BYTES) to
        payload.copyOfRange(UUID_BYTES, UUID_BYTES + MAC_BYTES)
}

fun encodeAck(out: DataOutputStream, msgId: ByteArray, fromMac: ByteArray) =
    encodeReceipt(out, TYPE_ACK, msgId, fromMac)

fun decodeAck(payload: ByteArray): Pair<ByteArray, ByteArray> = decodeReceipt(payload, "ack")

fun encodeRead(out: DataOutputStream, msgId: ByteArray, fromMac: ByteArray) =
    encodeReceipt(out, TYPE_READ, msgId, fromMac)

fun decodeRead(payload: ByteArray): Pair<ByteArray, ByteArray> = decodeReceipt(payload, "read")

// ---------------------------------------------------------------------------
// Group setup (0x04)
// ---------------------------------------------------------------------------

fun encodeGroupSetup(
    out: DataOutputStream,
    groupId: ByteArray,
    members: List<GroupMember>,
    name: String,
) {
    require(members.size <= 255) { "member_count is a uint8; got ${members.size}" }
    val nameBytes = name.toByteArray(Charsets.UTF_8)
    val buf = ByteBuffer.allocate(
        UUID_BYTES + 1 + members.size * (MAC_BYTES + PUBKEY_BYTES) + 2 + nameBytes.size
    )
    buf.put(groupId).put(members.size.toByte())
    for (m in members) buf.put(m.mac).put(m.pubkey)
    buf.putShort(nameBytes.size.toShort()).put(nameBytes)
    encodeFrame(out, TYPE_GROUP_SETUP, buf.array())
}

fun decodeGroupSetup(payload: ByteArray): GroupSetupFrame {
    if (payload.size < UUID_BYTES + 1) {
        throw MalformedFrame("group setup payload too short: ${payload.size}")
    }
    val buf = ByteBuffer.wrap(payload)
    val groupId = ByteArray(UUID_BYTES).also { buf.get(it) }
    val count = buf.get().toInt() and 0xFF
    val entry = MAC_BYTES + PUBKEY_BYTES
    if (buf.remaining() < count * entry + 2) {
        throw MalformedFrame("group setup truncated: $count members won't fit")
    }
    val members = ArrayList<GroupMember>(count)
    repeat(count) {
        val mac = ByteArray(MAC_BYTES).also { buf.get(it) }
        val pubkey = ByteArray(PUBKEY_BYTES).also { buf.get(it) }
        members.add(GroupMember(mac, pubkey))
    }
    val nameLen = buf.short.toInt() and 0xFFFF
    if (buf.remaining() < nameLen) throw MalformedFrame("group name truncated")
    val name = ByteArray(nameLen).also { buf.get(it) }.toString(Charsets.UTF_8)
    return GroupSetupFrame(groupId, members, name)
}

// ---------------------------------------------------------------------------
// Profile (0x06) — bare UTF-8, bounded by the header length
// ---------------------------------------------------------------------------

fun encodeProfile(out: DataOutputStream, name: String) =
    encodeFrame(out, TYPE_PROFILE, name.toByteArray(Charsets.UTF_8))

fun decodeProfile(payload: ByteArray): String = payload.toString(Charsets.UTF_8)

// ---------------------------------------------------------------------------
// Peer announcement (0x07)
// ---------------------------------------------------------------------------

/**
 * Encode `name` as UTF-8, cut to at most [limit] bytes on a codepoint
 * boundary. A naive slice can split a multi-byte sequence, which the receiver
 * would render as U+FFFD.
 */
internal fun truncateUtf8(name: String, limit: Int): ByteArray {
    val encoded = name.toByteArray(Charsets.UTF_8)
    if (encoded.size <= limit) return encoded
    var end = limit
    // UTF-8 continuation bytes are 0b10xxxxxx.
    while (end > 0 && (encoded[end].toInt() and 0xC0) == 0x80) end--
    return encoded.copyOfRange(0, end)
}

fun encodePeerAnnc(out: DataOutputStream, peers: List<PeerEntry>) {
    require(peers.size <= 255) { "peer_count is a uint8; got ${peers.size}" }
    val names = peers.map { truncateUtf8(it.name, 255) }
    val size = 1 + peers.indices.sumOf { MAC_BYTES + PUBKEY_BYTES + 1 + names[it].size }
    val buf = ByteBuffer.allocate(size)
    buf.put(peers.size.toByte())
    peers.forEachIndexed { i, p ->
        buf.put(p.mac).put(p.pubkey).put(names[i].size.toByte()).put(names[i])
    }
    encodeFrame(out, TYPE_PEER_ANNC, buf.array())
}

fun decodePeerAnnc(payload: ByteArray): List<PeerEntry> {
    if (payload.isEmpty()) throw MalformedFrame("peer annc payload is empty")
    val buf = ByteBuffer.wrap(payload)
    val count = buf.get().toInt() and 0xFF
    val peers = ArrayList<PeerEntry>(count)
    repeat(count) {
        if (buf.remaining() < MAC_BYTES + PUBKEY_BYTES + 1) {
            throw MalformedFrame("peer annc truncated after ${peers.size} entries")
        }
        val mac = ByteArray(MAC_BYTES).also { buf.get(it) }
        val pubkey = ByteArray(PUBKEY_BYTES).also { buf.get(it) }
        val nameLen = buf.get().toInt() and 0xFF
        if (buf.remaining() < nameLen) throw MalformedFrame("peer annc name truncated")
        val name = ByteArray(nameLen).also { buf.get(it) }.toString(Charsets.UTF_8)
        peers.add(PeerEntry(mac, pubkey, name))
    }
    return peers
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** 16 zero bytes — the group_id used for 1:1 DMs (see PROTOCOL.md). */
val ZERO_GROUP_ID = ByteArray(UUID_BYTES)

/** Random 16-byte message id (UUID v4 bytes), unique per message. */
fun newMsgId(): ByteArray {
    val uuid = UUID.randomUUID()
    return ByteBuffer.allocate(UUID_BYTES)
        .putLong(uuid.mostSignificantBits)
        .putLong(uuid.leastSignificantBits)
        .array()
}

private val MAC_RE = Regex("\\A[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\\z")

/**
 * Parse "AA:BB:CC:DD:EE:FF" into 6 bytes, MSB first.
 *
 * Strict on purpose: every wire field that takes a MAC is fixed at 6 bytes, so
 * a malformed address must throw here rather than silently shift every
 * following field in the frame.
 */
fun macToBytes(mac: String): ByteArray {
    require(MAC_RE.matches(mac)) { "not a 6-octet MAC address: $mac" }
    return ByteArray(MAC_BYTES) { mac.substring(it * 3, it * 3 + 2).toInt(16).toByte() }
}

fun bytesToMac(bytes: ByteArray): String =
    bytes.joinToString(":") { "%02X".format(it.toInt() and 0xFF) }

/** Lowercase hex — used as a map/set key where a ByteArray cannot be one. */
fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
