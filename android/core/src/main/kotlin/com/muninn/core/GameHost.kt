package com.muninn.core

import java.io.File

/**
 * Game sessions over app events — Kotlin port of python/src/muninn/gamehost.py.
 * Plugins are JavaScript files (python/src/muninn/games/README.md); this class
 * only manages sessions and the event log. Rendering/rules run in a JS engine
 * (WebView on Android).
 */
class GameHost(private val mesh: Mesh, private val bundled: () -> Map<String, String>, private val userDir: File) {

    /** One header option: [choices] for the invite UI, [default] if none picked. */
    data class Option(val id: String, val label: String, val choices: List<Any?>, val default: Any?)
    data class Meta(val id: String, val name: String, val icon: String, val min: Int, val max: Int, val options: List<Option> = emptyList())

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
            @Suppress("UNCHECKED_CAST")
            val opts = (j["options"] as? List<Any?>).orEmpty().mapNotNull { o ->
                val m = o as? Map<String, Any?> ?: return@mapNotNull null
                Option(m.str("id"), m.str("label", m.str("id")), (m["choices"] as? List<Any?>).orEmpty(), m["default"])
            }
            return Meta(id, j.str("name", id), j.str("icon", "🎮"), min, j.int("max", min), opts)
        }

        /** Mirror of gamehost.resolve_opts: validate, fill defaults. */
        fun resolveOpts(meta: Meta, opts: Map<String, Any?>?): Map<String, Any?> {
            val given = opts.orEmpty()
            val out = LinkedHashMap<String, Any?>()
            for (o in meta.options) {
                var v = given[o.id] ?: o.default
                if (o.choices.isNotEmpty() && o.choices.none { it == v }) {
                    v = o.choices.firstOrNull { it.toString() == v.toString() }
                        ?: throw IllegalArgumentException("${o.id} must be one of: ${o.choices.joinToString(", ")}")
                }
                out[o.id] = v
            }
            val unknown = given.keys - out.keys
            if (unknown.isNotEmpty()) throw IllegalArgumentException("unknown option(s): ${unknown.sorted().joinToString(", ")}")
            return out
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

    fun invite(conv: String, gameId: String, opts: Map<String, Any?>? = null): String {
        val src = sources()[gameId] ?: throw IllegalArgumentException("no such game: $gameId")
        if (src.toByteArray().size > MAX_CODE) throw IllegalArgumentException("game code too large")
        val meta = parseHeader(src)
        val chosen = resolveOpts(meta, opts)
        if (mesh.convMembers(conv).isEmpty()) throw IllegalArgumentException("nobody to play with here")
        val session = Nacl.randomBytes(6).hex()
        mesh.appEvent(conv, gameId, session, "invite", mapOf(
            "game" to gameId, "name" to meta.name, "icon" to meta.icon, "min" to meta.min, "max" to meta.max,
            "seed" to (Math.random() * Int.MAX_VALUE).toLong(), "code" to src, "opts" to chosen,
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
        val players = if (start != null) ((start.body as? Map<String, Any?>)?.list("players") ?: emptyList()).map { mesh.person(it as String) }.filter { it in joined }
                      else joined.take(mx)
        // an unsent invite stays unsent everywhere (see gamehost.py)
        val withdrawn = start == null && evs.any { it.type == "withdraw" && it.sender == creator }
        val started = (start != null || players.size >= mx) && !withdrawn
        val moves = evs.filter { it.type == "move" }.sortedWith(compareBy({ it.seq }, { it.ts }, { it.sender }))
        val ended = evs.filter { it.type == "resign" || it.type == "end" }
        var result = evs.firstOrNull { it.type == "result" }?.body as? Map<String, Any?>
        if (result == null && ended.firstOrNull()?.type == "resign") {
            val rest = players.filter { it != ended[0].sender }
            result = mapOf("winners" to (if (rest.size == 1) rest else emptyList()), "text" to "resigned")
        }
        val me = mesh.me
        // over by the rules (someone's move produced a result) or by resign/end
        val finished = ended.isNotEmpty() || result != null
        val status = when {
            withdrawn -> "invite withdrawn"
            result != null && ended.isEmpty() -> "finished · " + ((result["text"] as? String)?.takeIf { it.isNotEmpty() }
                ?: if ((result["winners"] as? List<*>).isNullOrEmpty()) "draw" else "game over")
            ended.isNotEmpty() -> "${mesh.displayName(ended[0].sender)} " + if (ended[0].type == "resign") "resigned" else "ended the game"
            started -> "in progress · ${moves.size} moves"
            players.size >= mn -> "${players.size}/$mx joined — ready, the host can start"
            else -> (mn - players.size).let { n -> "waiting for $n more player${if (n > 1) "s" else ""}" }
        }
        val names = (joined + me).toSet().associateWith { mesh.displayName(it) }
        val shown = if (started) players else joined
        return mapOf(
            "session" to session, "app" to inv.app, "conv" to inv.conv, "name" to b.str("name", inv.app),
            "icon" to b.str("icon", "🎮"), "creator" to creator, "seed" to b.long("seed"), "opts" to ((b["opts"] as? Map<*, *>) ?: emptyMap<String, Any?>()), "min" to mn, "max" to mx,
            "joined" to joined, "players" to shown, "declined" to declined, "started" to started,
            "ended" to finished, "finished" to finished, "endedBy" to (ended.firstOrNull()?.sender ?: ""),
            "endType" to (ended.firstOrNull()?.type ?: ""), "me" to me, "amPlayer" to (me in shown),
            // a full or running game can still be watched, not joined
            "invitedMe" to (me != creator && me !in joined && me !in declined && !started && !withdrawn && joined.size < mx),
            "spectator" to (started && me !in players), "names" to names,
            "moves" to moves.map { mapOf("seq" to it.seq, "sender" to it.sender, "m" to (it.body as? Map<String, Any?>)?.get("m")) },
            "status" to status, "withdrawn" to withdrawn, "result" to result, "ts" to (evs.lastOrNull()?.ts ?: 0L),
        )
    }

    @Suppress("UNCHECKED_CAST")
    fun codeFor(session: String): String {
        val inv = mesh.storage.loadAppEvents(session).firstOrNull { it.type == "invite" } ?: return ""
        return (inv.body as? Map<String, Any?>)?.str("code") ?: ""
    }

    fun sessions(conv: String?, includeWithdrawn: Boolean = false): List<Map<String, Any?>> =
        mesh.storage.listAppSessions(conv).map { sessionInfo(it) }
            .filter { it.isNotEmpty() && (includeWithdrawn || it["withdrawn"] != true) }
            .sortedBy { it["finished"] == true || it["withdrawn"] == true } // open and running first (stable: newest first)

    data class Score(val app: String, val name: String, val icon: String, var played: Int = 0, var draws: Int = 0,
                     val wins: MutableMap<String, Int> = LinkedHashMap())

    /** Wins per player for each game (finished games only). Mirrors gamehost.scores. */
    @Suppress("UNCHECKED_CAST")
    fun scores(conv: String? = null): List<Score> {
        val by = LinkedHashMap<String, Score>()
        for (s in sessions(conv)) {
            val r = s["result"] as? Map<String, Any?> ?: continue
            if (s["started"] != true) continue
            val g = by.getOrPut(s.str("app")) { Score(s.str("app"), s.str("name"), s.str("icon", "🎮")) }
            g.played++
            val winners = (r["winners"] as? List<*>).orEmpty().filterIsInstance<String>()
            if (winners.isEmpty()) g.draws++
            winners.forEach { g.wins[it] = (g.wins[it] ?: 0) + 1 }
        }
        return by.values.sortedByDescending { it.played }
    }

    fun withdraw(session: String) {
        val info = sessionInfo(session)
        if (info.isEmpty()) throw IllegalArgumentException("unknown game session")
        if (info["creator"] != mesh.me) throw IllegalArgumentException("only whoever sent the invite can unsend it")
        if (info["started"] == true) throw IllegalArgumentException("the game already started — resign instead")
        act(session, "withdraw", emptyMap<String, Any?>())
    }

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
        val to = mesh.convMembers(info.str("conv")) // watchers (and my other devices) see it live too
        mesh.rtSend(info.str("conv"), info.str("app"), session, body, to = to)
    }
}
