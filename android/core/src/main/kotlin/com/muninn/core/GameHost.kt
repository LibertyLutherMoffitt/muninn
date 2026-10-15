package com.muninn.core

import java.io.File

/**
 * Game sessions over app events — Kotlin port of python/src/muninn/gamehost.py.
 * Plugins are JavaScript files (python/src/muninn/games/README.md); this class
 * only manages sessions and the event log. Rendering/rules run in a JS engine
 * (WebView on Android).
 */
class GameHost(private val mesh: Mesh, private val bundled: () -> Map<String, String>, private val userDir: File) {

    data class Meta(val id: String, val name: String, val icon: String, val min: Int, val max: Int)

    companion object {
        private val HEADER = Regex("""^\s*//\s*@muninn-game\s+(\{.*\})\s*$""")
        const val MAX_CODE = 40_000

        fun parseHeader(src: String): Meta {
            val first = src.trimStart('﻿').lineSequence().firstOrNull() ?: ""
            val m = HEADER.find(first) ?: throw IllegalArgumentException("not a Muninn game (missing // @muninn-game header)")
            val j = Json.obj(m.groupValues[1])
            val id = j.str("id")
            if (!Regex("[a-z0-9_\\-]{1,32}").matches(id)) throw IllegalArgumentException("bad game id")
            val min = j.int("min", 2)
            return Meta(id, j.str("name", id), j.str("icon", "🎮"), min, j.int("max", min))
        }
    }

    init { userDir.mkdirs() }

    fun sources(): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for ((_, src) in bundled()) runCatching { out[parseHeader(src).id] = src }
        userDir.listFiles { f -> f.name.endsWith(".js") }?.sortedBy { it.name }?.forEach { f ->
            runCatching { val s = f.readText(); out[parseHeader(s).id] = s }
        }
        return out
    }

    fun catalog(): List<Meta> = sources().values.map { parseHeader(it) }.sortedBy { it.name.lowercase() }

    fun install(file: File): String {
        val src = file.readText()
        val meta = parseHeader(src)
        File(userDir, "${meta.id}.js").writeText(src)
        return meta.id
    }

    fun invite(conv: String, gameId: String): String {
        val src = sources()[gameId] ?: throw IllegalArgumentException("no such game: $gameId")
        if (src.toByteArray().size > MAX_CODE) throw IllegalArgumentException("game code too large")
        val meta = parseHeader(src)
        if (mesh.convMembers(conv).isEmpty()) throw IllegalArgumentException("nobody to play with here")
        val session = Nacl.randomBytes(6).hex()
        mesh.appEvent(conv, gameId, session, "invite", mapOf(
            "game" to gameId, "name" to meta.name, "icon" to meta.icon, "min" to meta.min, "max" to meta.max,
            "seed" to (Math.random() * Int.MAX_VALUE).toLong(), "code" to src,
        ), 0)
        return session
    }

    @Suppress("UNCHECKED_CAST")
    fun sessionInfo(session: String): Map<String, Any?> {
        val evs = mesh.storage.loadAppEvents(session)
        val inv = evs.firstOrNull { it.type == "invite" } ?: return emptyMap()
        val b = (inv.body as? Map<String, Any?>) ?: emptyMap()
        val creator = inv.sender
        val joins = evs.filter { it.type == "join" && it.sender != creator }.sortedWith(compareBy({ it.ts }, { it.sender }))
        val declined = evs.filter { it.type == "decline" }.map { it.sender }.toSortedSet().toList()
        val start = evs.firstOrNull { it.type == "start" && it.sender == creator }
        val mx = b.int("max", 2); val mn = b.int("min", 2)
        val joined = ArrayList<String>().apply { add(creator); joins.forEach { if (it.sender !in this) add(it.sender) } }
        val players = if (start != null) ((start.body as? Map<String, Any?>)?.list("players") ?: emptyList()).map { it as String }.filter { it in joined }
                      else joined.take(mx)
        val started = start != null || players.size >= mx
        val moves = evs.filter { it.type == "move" }.sortedWith(compareBy({ it.seq }, { it.ts }, { it.sender }))
        val ended = evs.filter { it.type == "resign" || it.type == "end" }
        val me = mesh.nodeId
        val status = when {
            ended.isNotEmpty() -> "${mesh.displayName(ended[0].sender)} " + if (ended[0].type == "resign") "resigned" else "ended the game"
            started -> "in progress · ${moves.size} moves"
            else -> "waiting for ${maxOf(mn - players.size, 1)} more player(s)"
        }
        val names = (joined + me).toSet().associateWith { mesh.displayName(it) }
        val shown = if (started) players else joined
        return mapOf(
            "session" to session, "app" to inv.app, "conv" to inv.conv, "name" to b.str("name", inv.app),
            "icon" to b.str("icon", "🎮"), "creator" to creator, "seed" to b.long("seed"), "min" to mn, "max" to mx,
            "joined" to joined, "players" to shown, "declined" to declined, "started" to started,
            "ended" to ended.isNotEmpty(), "endedBy" to (ended.firstOrNull()?.sender ?: ""),
            "endType" to (ended.firstOrNull()?.type ?: ""), "me" to me, "amPlayer" to (me in shown),
            "invitedMe" to (me != creator && me !in joined && me !in declined), "names" to names,
            "moves" to moves.map { mapOf("seq" to it.seq, "sender" to it.sender, "m" to (it.body as? Map<String, Any?>)?.get("m")) },
            "status" to status, "ts" to (evs.lastOrNull()?.ts ?: 0L),
        )
    }

    @Suppress("UNCHECKED_CAST")
    fun codeFor(session: String): String {
        val inv = mesh.storage.loadAppEvents(session).firstOrNull { it.type == "invite" } ?: return ""
        return (inv.body as? Map<String, Any?>)?.str("code") ?: ""
    }

    fun sessions(conv: String?): List<Map<String, Any?>> =
        mesh.storage.listAppSessions(conv).map { sessionInfo(it) }.filter { it.isNotEmpty() }

    @Suppress("UNCHECKED_CAST")
    fun act(session: String, type: String, body: Any?) {
        val info = sessionInfo(session)
        if (info.isEmpty()) throw IllegalArgumentException("unknown game session")
        var seq = 0
        var b = body ?: emptyMap<String, Any?>()
        if (type == "move") {
            seq = ((info["moves"] as List<Map<String, Any?>>).maxOfOrNull { it.int("seq") } ?: 0) + 1
            b = mapOf("m" to body)
        }
        mesh.appEvent(info.str("conv"), info.str("app"), session, type, b, seq)
    }

    @Suppress("UNCHECKED_CAST")
    fun rt(session: String, body: Any?) {
        val info = sessionInfo(session)
        if (info.isEmpty()) return
        val to = (info["players"] as List<String>).filter { it != mesh.nodeId }
        mesh.rtSend(info.str("conv"), info.str("app"), session, body, to = to)
    }
}
