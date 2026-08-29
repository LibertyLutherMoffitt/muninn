package com.muninn

import com.goterl.lazysodium.LazySodiumJava
import com.goterl.lazysodium.SodiumJava
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import kotlin.test.fail
import org.json.JSONObject

/**
 * Conformance of the Kotlin client's wire code against spec/wire-vectors.json —
 * the same fixture python/tests/test_wire_vectors.py checks.
 *
 * If one language passes and the other fails, the desktop and phone clients no
 * longer speak the same protocol. That is precisely the regression these
 * vectors exist to catch, and it is invisible to any single-language test.
 */
class WireVectorsTest {

    private val vectors: JSONObject by lazy {
        val f = File("../wire-vectors.json")
        assertTrue(f.exists(), "missing ${f.absolutePath}; run python3 spec/generate_vectors.py")
        JSONObject(f.readText())
    }

    private fun frames() = vectors.getJSONObject("frames")

    private fun frameBytes(name: String): ByteArray =
        frames().getJSONObject(name).getString("frame").hexToBytes()

    private fun payload(name: String): ByteArray = frameBytes(name).copyOfRange(3, frameBytes(name).size)

    private fun encoded(block: (DataOutputStream) -> Unit): ByteArray {
        val bos = ByteArrayOutputStream()
        block(DataOutputStream(bos))
        return bos.toByteArray()
    }

    // --- Constants ---

    @Test
    fun `frame type numbers match the spec`() {
        val t = vectors.getJSONObject("frame_types")
        assertEquals(TYPE_HANDSHAKE.toInt(), t.getInt("handshake"))
        assertEquals(TYPE_MESSAGE.toInt(), t.getInt("message"))
        assertEquals(TYPE_ACK.toInt(), t.getInt("ack"))
        assertEquals(TYPE_GROUP_SETUP.toInt(), t.getInt("group_setup"))
        assertEquals(TYPE_READ.toInt(), t.getInt("read"))
        assertEquals(TYPE_PROFILE.toInt(), t.getInt("profile"))
        assertEquals(TYPE_PEER_ANNC.toInt(), t.getInt("peer_annc"))
    }

    @Test
    fun `service uuid matches the spec`() {
        assertEquals(vectors.getString("service_uuid"), MUNINN_RFCOMM_UUID_STR)
    }

    @Test
    fun `every vector frame parses through readFrame`() {
        for (name in frames().keys()) {
            val raw = frameBytes(name)
            val frame = readFrame(DataInputStream(ByteArrayInputStream(raw)))
            assertEquals(raw.size - 3, frame.payload.size, "$name: header length disagrees")
            assertTrue(frame.type in 1..7, "$name: unknown frame type ${frame.type}")
        }
    }

    // --- Handshake ---

    @Test
    fun `handshake vector round trips`() {
        val spec = frames().getJSONObject("handshake")
        val payload = payload("handshake")
        assertEquals(PUBKEY_BYTES + MAC_BYTES, payload.size)
        assertContentEquals(spec.getString("pubkey").hexToBytes(), payload.copyOfRange(0, PUBKEY_BYTES))
        assertEquals(
            spec.getString("wire_id"),
            bytesToMac(payload.copyOfRange(PUBKEY_BYTES, PUBKEY_BYTES + MAC_BYTES)),
        )
        val ours = encoded {
            encodeHandshake(it, spec.getString("pubkey").hexToBytes(), macToBytes(spec.getString("wire_id")))
        }
        assertContentEquals(frameBytes("handshake"), ours)
    }

    @Test
    fun `legacy handshake vector is the bare 32-byte form`() {
        assertEquals(PUBKEY_BYTES, payload("handshake_legacy").size)
    }

    // --- Message ---

    @Test
    fun `message vectors round trip`() {
        for (name in listOf("message", "message_dm")) {
            val spec = frames().getJSONObject(name)
            val msg = decodeMessage(payload(name))
            assertContentEquals(spec.getString("group_id").hexToBytes(), msg.groupId, "$name groupId")
            assertContentEquals(spec.getString("msg_id").hexToBytes(), msg.msgId, "$name msgId")
            assertEquals(spec.getString("sender"), bytesToMac(msg.senderMac), "$name sender")
            assertEquals(spec.getString("dest"), bytesToMac(msg.destMac), "$name dest")
            assertEquals(spec.getLong("timestamp"), msg.timestamp, "$name timestamp")
            assertContentEquals(spec.getString("encrypted").hexToBytes(), msg.ciphertext, "$name ciphertext")

            val ours = encoded {
                encodeMessage(
                    it, msg.groupId, msg.msgId, msg.senderMac, msg.destMac,
                    msg.ciphertext, msg.timestamp,
                )
            }
            assertContentEquals(frameBytes(name), ours, "$name re-encode")
        }
    }

    @Test
    fun `a far-future timestamp stays unsigned`() {
        // 0xFFFFFFFF must read as 4294967295, not -1. A signed read here would
        // make every message look like it arrived in 1969 after 2038.
        assertEquals(4294967295L, decodeMessage(payload("message_dm")).timestamp)
    }

    @Test
    fun `a DM uses the zero group id`() {
        assertContentEquals(ZERO_GROUP_ID, decodeMessage(payload("message_dm")).groupId)
    }

    // --- ACK / READ ---

    @Test
    fun `ack and read vectors round trip`() {
        for ((name, type) in listOf("ack" to TYPE_ACK, "read" to TYPE_READ)) {
            val spec = frames().getJSONObject(name)
            val (msgId, from) = if (type == TYPE_ACK) decodeAck(payload(name)) else decodeRead(payload(name))
            assertContentEquals(spec.getString("msg_id").hexToBytes(), msgId, "$name msgId")
            assertEquals(spec.getString("from"), bytesToMac(from), "$name from")
            val ours = encoded {
                if (type == TYPE_ACK) encodeAck(it, msgId, from) else encodeRead(it, msgId, from)
            }
            assertContentEquals(frameBytes(name), ours, "$name re-encode")
        }
    }

    // --- Profile ---

    @Test
    fun `profile vectors round trip including unicode and empty`() {
        for (name in listOf("profile", "profile_unicode", "profile_empty")) {
            val spec = frames().getJSONObject(name)
            assertEquals(spec.getString("name"), decodeProfile(payload(name)), "$name decode")
            val ours = encoded { encodeProfile(it, spec.getString("name")) }
            assertContentEquals(frameBytes(name), ours, "$name re-encode")
        }
    }

    // --- Group setup ---

    @Test
    fun `group setup vectors round trip`() {
        for (name in listOf("group_setup", "group_setup_empty")) {
            val spec = frames().getJSONObject(name)
            val setup = decodeGroupSetup(payload(name))
            assertContentEquals(spec.getString("group_id").hexToBytes(), setup.groupId, "$name gid")
            assertEquals(spec.getString("name"), setup.name, "$name name")
            val expected = spec.getJSONArray("members")
            assertEquals(expected.length(), setup.members.size, "$name member count")
            for (i in 0 until expected.length()) {
                assertEquals(expected.getJSONObject(i).getString("mac"), bytesToMac(setup.members[i].mac))
                assertContentEquals(
                    expected.getJSONObject(i).getString("pubkey").hexToBytes(),
                    setup.members[i].pubkey,
                )
            }
            val ours = encoded { encodeGroupSetup(it, setup.groupId, setup.members, setup.name) }
            assertContentEquals(frameBytes(name), ours, "$name re-encode")
        }
    }

    // --- Peer announcement ---

    @Test
    fun `peer annc vectors round trip`() {
        for (name in listOf("peer_annc", "peer_annc_empty")) {
            val spec = frames().getJSONObject(name)
            val peers = decodePeerAnnc(payload(name))
            val expected = spec.getJSONArray("peers")
            assertEquals(expected.length(), peers.size, "$name peer count")
            for (i in 0 until expected.length()) {
                assertEquals(expected.getJSONObject(i).getString("mac"), bytesToMac(peers[i].mac))
                assertContentEquals(
                    expected.getJSONObject(i).getString("pubkey").hexToBytes(),
                    peers[i].pubkey,
                )
                assertEquals(expected.getJSONObject(i).getString("name"), peers[i].name)
            }
            val ours = encoded { encodePeerAnnc(it, peers) }
            assertContentEquals(frameBytes(name), ours, "$name re-encode")
        }
    }

    // --- Crypto interop ---

    @Test
    fun `libsodium opens the ciphertext PyNaCl sealed`() {
        // lazysodium-java is the desktop twin of the app's lazysodium-android:
        // same libsodium primitives, same crypto_box_easy construction. If this
        // opens, the phone can read what the desktop client sends.
        val c = vectors.getJSONObject("crypto")
        val sodium = LazySodiumJava(SodiumJava())
        val sealed = c.getString("sealed").hexToBytes()
        val nonce = sealed.copyOfRange(0, NONCE_BYTES)
        val ct = sealed.copyOfRange(NONCE_BYTES, sealed.size)
        val pt = ByteArray(ct.size - 16)

        assertContentEquals(c.getString("nonce").hexToBytes(), nonce, "sealed must start with the nonce")
        val ok = sodium.cryptoBoxOpenEasy(
            pt, ct, ct.size.toLong(), nonce,
            c.getString("alice_public").hexToBytes(),
            c.getString("bob_secret").hexToBytes(),
        )
        if (!ok) fail("crypto_box_open_easy rejected the PyNaCl vector")
        assertEquals(c.getString("plaintext_utf8"), String(pt, Charsets.UTF_8))
    }

    @Test
    fun `libsodium reseals to the exact PyNaCl bytes`() {
        val c = vectors.getJSONObject("crypto")
        val sodium = LazySodiumJava(SodiumJava())
        val pt = c.getString("plaintext_utf8").toByteArray(Charsets.UTF_8)
        val nonce = c.getString("nonce").hexToBytes()
        val ct = ByteArray(pt.size + 16)
        val ok = sodium.cryptoBoxEasy(
            ct, pt, pt.size.toLong(), nonce,
            c.getString("bob_public").hexToBytes(),
            c.getString("alice_secret").hexToBytes(),
        )
        if (!ok) fail("crypto_box_easy failed")
        assertContentEquals(c.getString("sealed").hexToBytes(), nonce + ct)
    }

    @Test
    fun `x25519 public keys derive to the documented values`() {
        val c = vectors.getJSONObject("crypto")
        val sodium = LazySodiumJava(SodiumJava())
        for ((secret, public) in listOf(
            c.getString("alice_secret") to c.getString("alice_public"),
            c.getString("bob_secret") to c.getString("bob_public"),
        )) {
            val pub = ByteArray(32)
            assertTrue(sodium.cryptoScalarMultBase(pub, secret.hexToBytes()))
            assertContentEquals(public.hexToBytes(), pub)
        }
    }
}

private fun String.hexToBytes(): ByteArray =
    ByteArray(length / 2) { substring(it * 2, it * 2 + 2).toInt(16).toByte() }
