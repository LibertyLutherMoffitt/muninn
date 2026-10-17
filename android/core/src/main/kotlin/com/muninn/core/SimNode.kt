package com.muninn.core

import java.io.File
import java.net.InetAddress
import java.net.ServerSocket

/**
 * JVM sim node — the Kotlin core (same code the Android app runs) joined to
 * muninn-simhub, with the same JSON control protocol as python's muninn-bot.
 *
 *   ./gradlew :core:run --args="--hub 127.0.0.1:7420 --name kate --control 7601 --echo"
 */
fun main(args: Array<String>) {
    val opts = HashMap<String, String>()
    val flags = HashSet<String>()
    var i = 0
    while (i < args.size) {
        val a = args[i].removePrefix("--")
        if (i + 1 < args.size && !args[i + 1].startsWith("--")) { opts[a] = args[i + 1]; i += 2 } else { flags.add(a); i++ }
    }
    val name = opts["name"] ?: "jvm"
    val hub = opts["hub"] ?: System.getenv("MUNINN_SIM") ?: "127.0.0.1:7420"
    val pos = opts["pos"]?.split(",")?.map { it.toDouble() }?.let { it[0] to it[1] }
    val dir = File(opts["data"] ?: (System.getProperty("java.io.tmpdir") + "/muninn-jvm-$name"))
    val verbose = "verbose" in flags
    val node = Node(SimTransport(hub, name, pos = pos), MemStorage(File(dir, "files")), name,
        log = { if (verbose) println(it) })
    val mesh = node.mesh
    mesh.addListener(object : MeshListener {
        override fun onMessage(m: StoredMessage) {
            if (m.sender == mesh.nodeId || m.kind != "text" && m.kind != "file") return
            println("[$name] ${mesh.displayName(m.sender)} @ ${m.conv}: ${m.text}")
            mesh.markRead(m.conv)
            if ("echo" in flags && m.text.isNotEmpty() && !m.text.startsWith("echo:"))
                mesh.sendText(m.conv, "echo: ${m.text}", m.msgId)
        }
    })
    node.start()
    println("[$name] up as ${mesh.nodeId} on ${node.transport.localAddr()}")
    opts["control"]?.toInt()?.let { startControl(node, it) }
    while (true) Thread.sleep(3_600_000)
}

private fun startControl(node: Node, port: Int) {
    val mesh = node.mesh
    fun peer(s: String) = mesh.resolve(s) ?: throw IllegalArgumentException("unknown peer $s")
    fun group(n: String) = synchronized(mesh.lock) { mesh.groups.values.firstOrNull { it.name == n } }
        ?: throw IllegalArgumentException("unknown group $n")

    fun run(c: Map<String, Any?>): Any? = when (c.str("cmd")) {
        "status" -> mesh.status() + mapOf(
            "peers" to synchronized(mesh.lock) { mesh.peers.mapValues { it.value.display } },
            "groups" to synchronized(mesh.lock) { mesh.groups.mapValues { it.value.name } },
        )
        "send" -> mapOf("msg_id" to mesh.sendText(dmConv(peer(c.str("to"))), c.str("text")).hex())
        "group" -> mapOf("group" to mesh.createGroup(c.str("name"), c.list("members").map { peer(it as String) }).hex())
        "ping" -> mapOf("rtt_ms" to mesh.ping(peer(c.str("to")), c.int("count", 5), c.int("size", 0)))
        "linkcode" -> mapOf("code" to mesh.linkCode())
        "link" -> { mesh.linkTo(c.str("to"), c.str("code")); emptyMap<String, Any?>() }
        "unlink" -> { mesh.unlink(c.str("device")); emptyMap<String, Any?>() }
        "people" -> mesh.activePeers().map { mapOf("id" to it.nodeId, "name" to mesh.displayName(it.nodeId)) }
        "introduce" -> { mesh.introduce(c.str("id"), c.str("pubkey").unhex(), c.str("name")); emptyMap<String, Any?>() }
        "gadd" -> { mesh.updateGroup(group(c.str("group")).groupId, add = c.list("members").map { it as String }); emptyMap<String, Any?>() }
        "gsend" -> mapOf("msg_id" to mesh.sendText(group(c.str("group")).conv, c.str("text")).hex())
        "history" -> mesh.storage.loadHistory(c.str("conv"), c.int("n", 50)).map {
            mapOf("id" to it.idHex, "sender" to it.sender, "text" to it.text, "kind" to it.kind,
                "state" to it.state, "reactions" to it.reactions)
        }
        "file" -> mapOf("msg_id" to mesh.sendFile(dmConv(peer(c.str("to"))), File(c.str("path")), "application/octet-stream").hex())
        "files" -> synchronized(mesh.lock) { mesh.files.values.map { mapOf("name" to it.name, "state" to it.state, "path" to it.path, "sha256" to it.sha256) } }
        "name" -> { mesh.setName(c.str("name")); emptyMap<String, Any?>() }
        "react" -> { mesh.react(c.str("conv"), c.str("msg").unhex(), c.str("emoji")); emptyMap<String, Any?>() }
        else -> throw IllegalArgumentException("unknown cmd ${c.str("cmd")}")
    }

    val server = ServerSocket(port, 50, InetAddress.getByName("127.0.0.1"))
    Thread({
        while (true) {
            val s = server.accept()
            Thread({
                s.use {
                    val r = it.getInputStream().bufferedReader()
                    val w = it.getOutputStream().bufferedWriter()
                    while (true) {
                        val line = r.readLine() ?: break
                        val res = try { mapOf("ok" to true, "result" to run(Json.obj(line))) }
                                  catch (e: Throwable) { mapOf("ok" to false, "error" to e.toString()) }
                        w.write(Json.write(res)); w.write("\n"); w.flush()
                    }
                }
            }, "ctl").apply { isDaemon = true; start() }
        }
    }, "control").apply { isDaemon = true; start() }
}
