package com.muninn

import android.content.Context
import android.os.Build
import android.util.Log
import com.muninn.core.AppEvent
import com.muninn.core.GameHost
import com.muninn.core.MeshListener
import com.muninn.core.Node
import com.muninn.core.SimTransport
import com.muninn.core.StoredMessage
import com.muninn.core.Transport
import com.muninn.core.convGroupId
import com.muninn.core.dmConv
import com.muninn.core.hex
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File

/**
 * Process-wide Muninn runtime: the Node (mesh + transport), the game host, and
 * a `tick` flow the UI observes to re-read state after any mesh event.
 */
object Runtime {
    private const val TAG = "Muninn"
    @Volatile var node: Node? = null; private set
    @Volatile var games: GameHost? = null; private set
    var transport: Transport? = null; private set
    private lateinit var app: Context

    private val _tick = MutableStateFlow(0L)
    val tick: StateFlow<Long> = _tick.asStateFlow()
    private val _toast = MutableStateFlow<String?>(null)
    val toast: StateFlow<String?> = _toast.asStateFlow()
    private val _rt = MutableStateFlow<Triple<String, String, Any?>?>(null)
    val rt: StateFlow<Triple<String, String, Any?>?> = _rt.asStateFlow() // session, sender, body

    /** Conversation currently on screen (suppresses its notifications). */
    @Volatile var visibleConv: String? = null
    @Volatile var foreground = false

    fun bump() { _tick.value = _tick.value + 1 }
    fun toast(msg: String) { _toast.value = msg }
    fun clearToast() { _toast.value = null }

    fun prefs(ctx: Context) = ctx.getSharedPreferences("muninn", Context.MODE_PRIVATE)

    @Synchronized
    fun start(ctx: Context): Node {
        node?.let { return it }
        app = ctx.applicationContext
        val p = prefs(app)
        val sim = p.getString("sim_hub", "")!!
        val name = p.getString("name", null)?.takeIf { it.isNotEmpty() }
        val t: Transport = if (sim.isNotEmpty()) SimTransport(sim, name ?: Build.MODEL) else BtTransport(app)
        transport = t
        val n = Node(t, AndroidStorage(app), name, log = { Log.d(TAG, it) })
        val gh = GameHost(n.mesh, { bundledGames(app) }, File(app.filesDir, "games"))
        n.mesh.addListener(object : MeshListener {
            override fun onMessage(m: StoredMessage) {
                bump()
                if (m.sender != n.nodeId && (m.kind == "text" || m.kind == "file")) {
                    if (foreground && visibleConv == m.conv) n.mesh.markRead(m.conv)
                    else Notifications.message(app, m)
                }
            }
            override fun onUpdate(msgId: ByteArray) = bump()
            override fun onPeers() = bump()
            override fun onGroup(groupId: ByteArray) = bump()
            override fun onFile(fileId: ByteArray) = bump()
            override fun onTyping(conv: String, node: String) = bump()
            override fun onApp(ev: AppEvent) {
                bump()
                if (ev.type == "invite" && ev.sender != n.nodeId) Notifications.gameInvite(app, ev, gh)
            }
            override fun onRt(src: String, app: String, session: String, body: Any?, blob: ByteArray) {
                _rt.value = Triple(session, src, body)
            }
        })
        n.start()
        node = n
        games = gh
        Log.i(TAG, "node ${n.nodeId} up on ${if (sim.isNotEmpty()) "sim $sim" else "bluetooth"}")
        return n
    }

    @Synchronized
    fun stop() {
        node?.stop()
        node = null
        games = null
    }

    fun bundledGames(ctx: Context): Map<String, String> =
        (ctx.assets.list("games") ?: emptyArray()).filter { it.endsWith(".js") }
            .associateWith { ctx.assets.open("games/$it").bufferedReader().readText() }

    // ---- convenience for UI ----------------------------------------------------

    data class ConvItem(
        val conv: String, val title: String, val isGroup: Boolean, val preview: String, val ts: Long,
        val unread: Int, val status: String, val typing: Boolean, val pending: Int, val subtitle: String,
    )

    fun conversations(): List<ConvItem> {
        val n = node ?: return emptyList()
        val m = n.mesh
        val last = m.storage.lastMessages()
        val unread = m.storage.unreadCounts()
        fun preview(conv: String): Pair<String, Long> {
            val msg = last[conv] ?: return "" to 0L
            val who = if (msg.sender == n.nodeId && msg.kind != "system") "You: " else ""
            return when {
                msg.deleted -> "message deleted" to msg.ts
                msg.kind == "file" -> who + "📎 " + (msg.text.ifEmpty { msg.meta["n"] as? String ?: "file" }) to msg.ts
                else -> who + msg.text.replace('\n', ' ') to msg.ts
            }
        }
        val out = ArrayList<ConvItem>()
        val peers = m.activePeers()
        val groups = synchronized(m.lock) { m.groups.values.toList() }
        for (p in peers) {
            val conv = dmConv(p.nodeId)
            val pres = n.presence(p.nodeId)
            val (text, ts) = preview(conv)
            if (ts == 0L && pres.state == "offline" && (unread[conv] ?: 0) == 0) continue
            out.add(ConvItem(conv, p.display, false, text, if (ts > 0) ts else p.lastSeen, unread[conv] ?: 0, pres.state,
                m.typingIn(conv).isNotEmpty(), m.pendingCount(p.nodeId), pres.text))
        }
        for (g in groups) {
            val (text, ts) = preview(g.conv)
            val members = synchronized(m.lock) { g.members.toList() }.filter { it != n.nodeId }
            val here = members.count { m.reachable(it) }
            out.add(ConvItem(g.conv, g.name + if (g.left) " (left)" else "", true, text, ts, unread[g.conv] ?: 0, "group",
                m.typingIn(g.conv).isNotEmpty(), 0, "$here/${members.size} in range"))
        }
        return out.sortedByDescending { it.ts }
    }

    fun convTitle(conv: String): String {
        val n = node ?: return ""
        if (conv.startsWith("dm:")) return n.mesh.displayName(conv.substring(3))
        val gid = convGroupId(conv) ?: return ""
        return synchronized(n.mesh.lock) { n.mesh.groups[gid.hex()]?.name } ?: "group"
    }

    fun convSubtitle(conv: String): String {
        val n = node ?: return ""
        val m = n.mesh
        val typing = m.typingIn(conv).map { m.displayName(it) }
        if (typing.isNotEmpty()) return typing.joinToString() + if (typing.size == 1) " is typing…" else " are typing…"
        if (conv.startsWith("dm:")) {
            val id = conv.substring(3)
            return n.presence(id).text + m.pendingCount(id).let { if (it > 0) " · $it waiting to send" else "" }
        }
        val gid = convGroupId(conv) ?: return ""
        val g = synchronized(m.lock) { m.groups[gid.hex()] } ?: return ""
        val members = synchronized(m.lock) { g.members.toList() }.filter { it != n.nodeId }
        return "${members.count { m.reachable(it) }}/${members.size} in range · " + members.joinToString { m.displayName(it) }
    }
}
