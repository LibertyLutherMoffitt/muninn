package com.muninn.node

import com.muninn.MemoryMeshStore
import com.muninn.Mesh
import com.muninn.MeshGroup
import com.muninn.PeerBook
import com.muninn.ZERO_GROUP_ID
import com.muninn.toHex
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import kotlin.concurrent.thread
import org.json.JSONObject

/**
 * A headless Muninn node running the Android client's protocol core
 * ([Mesh]) over the desktop client's loopback backend.
 *
 * It joins the same rendezvous directory as `MUNINN_BT_BACKEND=loopback`
 * Python clients, obeys the same `topology.json`, and is driven by a
 * line-based stdin/stdout protocol, so `python/tests/test_interop_kotlin.py`
 * can put Kotlin and Python clients in one cabin and make each relay for the
 * other. What it cannot cover is the Android glue: Bluetooth sockets, the
 * service, the UI.
 *
 * Environment (same names as the Python loopback backend):
 *   MUNINN_LOOPBACK_DIR  rendezvous directory
 *   MUNINN_LOOPBACK_MAC  this node's wire id
 *   MUNINN_NAME          display name announced to peers
 *
 * Commands (one per line):
 *   dm <peer> <text>          <peer> is a name or wire id
 *   group-new <name> <p1,p2>  create a group
 *   group-say <name> <text>
 *   read <msgid-hex>          send a read receipt
 *   nick <name>
 *   peers                     PEER lines, then END
 *   quit
 * Events: READY, CONNECTED, DISCONNECTED, MSG, SENT, ACK, READ, GROUP,
 * PROFILE, ERROR — see [emit] call sites.
 */
fun main() {
    val dir = File(System.getenv("MUNINN_LOOPBACK_DIR") ?: error("MUNINN_LOOPBACK_DIR is required"))
    val mac = (System.getenv("MUNINN_LOOPBACK_MAC") ?: error("MUNINN_LOOPBACK_MAC is required")).uppercase()
    val name = System.getenv("MUNINN_NAME") ?: ""
    LoopbackNode(dir, mac, name).run()
}

private class LoopbackNode(private val dir: File, private val mac: String, name: String) {
    private val keys = Sodium.keypair()
    private val mesh = Mesh(mac, keys.second, SodiumSealer(keys.first), MemoryMeshStore(), PeerBook())
    private val server = ServerSocket(0, 16, InetAddress.getLoopbackAddress())
    private val record = File(dir, mac.replace(':', '-') + ".json")
    private val links = ConcurrentHashMap<Socket, String>()
    private val nextDial = ConcurrentHashMap<String, Long>()
    @Volatile private var running = true

    init {
        dir.mkdirs()
        mesh.setDisplayName(name)
        mesh.listener = object : Mesh.Listener {
            override fun onMessage(groupId: ByteArray, sender: String, text: String, msgId: ByteArray, timestamp: Long) {
                val where = if (groupId.contentEquals(ZERO_GROUP_ID)) {
                    "dm"
                } else {
                    "group:" + (mesh.group(groupId)?.name ?: groupId.toHex())
                }
                emit("MSG $where ${msgId.toHex()} ${mesh.book.displayName(sender)}: $text")
            }
            override fun onAck(msgId: ByteArray, from: String) = emit("ACK ${msgId.toHex()} ${mesh.book.displayName(from)}")
            override fun onRead(msgId: ByteArray, from: String) = emit("READ ${msgId.toHex()} ${mesh.book.displayName(from)}")
            override fun onGroup(group: MeshGroup) = emit("GROUP ${group.key} ${group.name}")
            override fun onPeerChange(wireId: String, connected: Boolean) =
                emit((if (connected) "CONNECTED " else "DISCONNECTED ") + "$wireId ${mesh.book.displayName(wireId)}")
            override fun onProfile(wireId: String, name: String) = emit("PROFILE $wireId $name")
        }
    }

    fun run() {
        record.writeText(
            JSONObject()
                .put("mac", mac)
                .put("name", mesh.displayName.ifEmpty { mac })
                .put("port", server.localPort)
                .put("pid", ProcessHandle.current().pid())
                .put("uuid", "320bcf9c-94fe-46f4-b9bf-83535cafcd55")
                .put("started", System.currentTimeMillis() / 1000)
                .toString()
        )
        Runtime.getRuntime().addShutdownHook(Thread { record.delete() })
        thread(isDaemon = true, name = "accept") { acceptLoop() }
        thread(isDaemon = true, name = "dial") { dialLoop() }
        thread(isDaemon = true, name = "range") { rangeLoop() }
        thread(isDaemon = true, name = "maintain") {
            while (running) {
                Thread.sleep(2000)
                runCatching { mesh.maintain() }
            }
        }
        emit("READY $mac")
        commandLoop()
        running = false
        record.delete()
        mesh.close()
    }

    // --- Loopback backend (mirrors muninn/bt/loopback.py) ---

    private fun topology(): Set<Set<String>>? {
        val f = File(dir, "topology.json")
        if (!f.exists()) return null
        return try {
            val links = JSONObject(f.readText()).getJSONArray("links")
            (0 until links.length()).map { i ->
                val pair = links.getJSONArray(i)
                setOf(pair.getString(0).uppercase(), pair.getString(1).uppercase())
            }.toSet()
        } catch (e: Exception) {
            lastTopology
        }.also { lastTopology = it }
    }

    @Volatile private var lastTopology: Set<Set<String>>? = null

    private fun inRange(other: String): Boolean = topology()?.contains(setOf(mac, other.uppercase())) ?: true

    private fun acceptLoop() {
        while (running) {
            val sock = runCatching { server.accept() }.getOrNull() ?: break
            thread(isDaemon = true) {
                // The dialler announces its address first, as in loopback.py.
                val announced = StringBuilder()
                val input = sock.getInputStream()
                while (announced.length < 64) {
                    val c = input.read()
                    if (c < 0 || c == '\n'.code) break
                    announced.append(c.toChar())
                }
                val peer = announced.toString().trim().uppercase()
                if (!inRange(peer)) {
                    sock.close()
                    return@thread
                }
                links[sock] = peer
                if (!mesh.attach(TcpLink(sock), peer, outbound = false)) links.remove(sock)
            }
        }
    }

    private fun records(): List<JSONObject> =
        dir.listFiles { f -> f.name.endsWith(".json") && f.name != "topology.json" }
            .orEmpty()
            .mapNotNull { runCatching { JSONObject(it.readText()) }.getOrNull() }
            .filter { it.has("mac") && it.has("port") }
            .filter { r ->
                val pid = r.optLong("pid", 0)
                pid <= 0 || ProcessHandle.of(pid).map { it.isAlive }.orElse(false)
            }

    private fun dialLoop() {
        while (running) {
            val now = System.currentTimeMillis()
            for (r in records()) {
                val peer = r.getString("mac").uppercase()
                if (peer == mac || !inRange(peer) || mesh.isConnectedTransport(peer)) continue
                if ((nextDial[peer] ?: 0L) > now) continue
                val ok = runCatching {
                    val sock = Socket(InetAddress.getLoopbackAddress(), r.getInt("port"))
                    sock.getOutputStream().write("$mac\n".toByteArray())
                    links[sock] = peer
                    mesh.attach(TcpLink(sock), peer, outbound = true).also { if (!it) links.remove(sock) }
                }.getOrDefault(false)
                // Jittered so two nodes that lost each other at once do not
                // keep redialling in lockstep.
                nextDial[peer] = now + if (ok) 1000 else 1500 + (0..1000).random()
            }
            Thread.sleep(500)
        }
    }

    private fun rangeLoop() {
        while (running) {
            for ((sock, peer) in links) {
                if (sock.isClosed) {
                    links.remove(sock)
                } else if (!inRange(peer)) {
                    links.remove(sock)
                    runCatching { sock.shutdownInput() }
                    runCatching { sock.shutdownOutput() }
                    runCatching { sock.close() }
                }
            }
            Thread.sleep(100)
        }
    }

    // --- Commands ---

    private fun commandLoop() {
        while (true) {
            val line = readlnOrNull() ?: return
            val parts = line.trim().split(" ", limit = 3)
            try {
                when (parts[0]) {
                    "" -> Unit
                    "quit" -> return
                    "nick" -> mesh.setDisplayName(line.trim().removePrefix("nick").trim())
                    "dm" -> {
                        val peer = resolve(parts[1])
                        val r = mesh.sendMessage(ZERO_GROUP_ID, parts[2], listOf(peer))
                        emit("SENT ${r.msgId.toHex()} ${r.sent.joinToString(",")}")
                    }
                    "group-new" -> {
                        val members = parts[2].split(",").map(::resolve)
                        val g = mesh.createGroup(parts[1], members)
                        emit("GROUP ${g.key} ${g.name}")
                    }
                    "group-say" -> {
                        val g = mesh.groups().firstOrNull { it.name == parts[1] || it.key == parts[1] }
                            ?: error("no group ${parts[1]}")
                        val r = mesh.sendMessage(g.id, parts[2], g.members.keys.filter { it != mac })
                        emit("SENT ${r.msgId.toHex()} ${r.sent.joinToString(",")}")
                    }
                    "read" -> mesh.sendRead(parts[1].hexToBytes())
                    "peers" -> {
                        for ((id, s) in mesh.book.muninnStatuses().toSortedMap()) {
                            if (id == mac) continue
                            emit("PEER $id ${mesh.book.displayName(id)} ${s.state} ${s.via ?: "-"} ${s.hops ?: "-"}")
                        }
                        emit("END")
                    }
                    else -> emit("ERROR unknown command ${parts[0]}")
                }
            } catch (e: Exception) {
                emit("ERROR ${e.message}")
            }
        }
    }

    private fun resolve(query: String): String =
        mesh.book.resolve(query) ?: error("unknown peer $query")

    @Synchronized
    private fun emit(line: String) {
        println(line)
        System.out.flush()
    }
}

private fun String.hexToBytes(): ByteArray = chunked(2).map { it.toInt(16).toByte() }.toByteArray()
