package com.muninn.core

import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/**
 * History between linked devices of mine, in the backup zip format of
 * python/src/muninn/backup.py (manifest.json, tables.json with python column
 * names and hex blobs, files/<id>/<name>). Built only on the [Storage]
 * interface, so the JVM sim node and the Android app share it; the app's
 * Backup.kt (user-facing export with an optional identity) writes the same
 * format straight from SQLite.
 */
object HistorySync {
    private fun hexOrNull(b: ByteArray?) = b?.hex()

    /** Write my history to [out]; attachments up to [maxFiles] bytes. */
    fun export(storage: Storage, me: String, name: String, out: File, maxFiles: Long) {
        val peers = storage.loadPeers().map {
            mapOf("node_id" to it.nodeId, "pubkey" to it.pubkey.hex(), "name" to it.name, "name_ts" to it.nameTs,
                "override" to it.override, "first_seen" to it.lastSeen, "last_seen" to it.lastSeen, "replaced_by" to it.replacedBy)
        }
        val groupRows = storage.loadGroups()
        val groups = groupRows.map {
            mapOf("group_id" to it.groupId.hex(), "name" to it.name, "version" to it.version, "left" to if (it.left) 1 else 0, "updated_at" to null)
        }
        val members = groupRows.flatMap { g -> g.members.map { mapOf("group_id" to g.groupId.hex(), "node_id" to it) } }
        val messages = ArrayList<Map<String, Any?>>()
        val recipients = ArrayList<Map<String, Any?>>()
        val reactions = ArrayList<Map<String, Any?>>()
        for (conv in storage.lastMessages().keys) for (m in storage.loadHistory(conv, 1_000_000)) {
            messages.add(mapOf(
                "msg_id" to m.msgId.hex(), "conv" to m.conv, "sender" to m.sender, "ts" to m.ts, "kind" to m.kind,
                "text" to m.text, "reply_to" to hexOrNull(m.replyTo), "meta" to if (m.meta.isEmpty()) null else Json.write(m.meta),
                "edited_at" to if (m.edited) m.ts else null, "deleted" to if (m.deleted) 1 else 0, "read_local" to 1,
            ))
            for ((node, st) in m.status) recipients.add(mapOf(
                "msg_id" to m.msgId.hex(), "node_id" to node,
                "delivered_at" to if (st != "sent") m.ts else null, "read_at" to if (st == "read") m.ts else null,
            ))
            for ((emoji, nodes) in m.reactions) for (n in nodes)
                reactions.add(mapOf("msg_id" to m.msgId.hex(), "node_id" to n, "emoji" to emoji, "ts" to m.ts))
        }
        val files = storage.loadFiles().filter { !it.conv.startsWith("sync:") }
        val events = storage.listAppSessions(null).flatMap { storage.loadAppEvents(it) }.map {
            mapOf("event_id" to it.eventId.hex(), "session" to it.session, "app" to it.app, "conv" to it.conv, "sender" to it.sender,
                "seq" to it.seq, "type" to it.type, "body" to Json.write(it.body), "ts" to it.ts)
        }
        ZipOutputStream(out.outputStream()).use { z ->
            fun put(path: String, bytes: ByteArray) { z.putNextEntry(ZipEntry(path)); z.write(bytes); z.closeEntry() }
            put("manifest.json", Json.write(mapOf("format" to "muninn-backup", "version" to 1, "created" to P.now(),
                "from" to me, "name" to name, "identity" to null)).toByteArray())
            var budget = maxFiles
            val fileRows = files.map { f ->
                val src = File(f.path)
                if (f.state == "complete" && src.isFile && src.length() <= budget) {
                    budget -= src.length()
                    put("files/${f.fileId.hex()}/${src.name}", src.readBytes())
                }
                mapOf("file_id" to f.fileId.hex(), "msg_id" to f.msgId.hex(), "conv" to f.conv, "sender" to f.sender, "name" to f.name,
                    "mime" to f.mime, "size" to f.size, "sha256" to f.sha256, "chunk_size" to f.chunkSize, "chunks" to f.chunks,
                    "path" to src.name, "state" to f.state, "have" to f.have.hex())
            }
            for (m in messages) {
                val meta = (m["meta"] as? String)?.let { runCatching { Json.obj(it) }.getOrNull() } ?: continue
                val th = File(meta["thumb"] as? String ?: continue)
                if (th.isFile && meta["f"] != null) put("files/${meta["f"]}/thumb.jpg", th.readBytes())
            }
            put("tables.json", Json.write(mapOf(
                "peers" to peers, "groups" to groups, "group_members" to members, "messages" to messages,
                "recipients" to recipients, "reactions" to reactions, "files" to fileRows, "app_events" to events,
            )).toByteArray())
        }
    }

    /** Merge history from another of my devices. Returns messages added. */
    @Suppress("UNCHECKED_CAST")
    fun import(storage: Storage, zip: File, allowFrom: Set<String>): Int {
        ZipFile(zip).use { z ->
            fun read(name: String) = z.getEntry(name)?.let { e -> z.getInputStream(e).use { it.readBytes() } }
            val manifest = Json.obj(String(read("manifest.json") ?: error("not a Muninn backup")))
            if (manifest.str("format") != "muninn-backup") error("not a Muninn backup")
            if (manifest.str("from") !in allowFrom) error("not from one of my devices")
            val t = Json.obj(String(read("tables.json") ?: error("damaged backup")))
            fun rows(name: String) = (t[name] as? List<Map<String, Any?>>).orEmpty()
            fun blob(v: Any?) = (v as? String)?.takeIf { it.isNotEmpty() }?.unhex()
            fun lng(v: Any?) = (v as? Number)?.toLong()

            val havePeers = storage.loadPeers().associateBy { it.nodeId }
            for (r in rows("peers")) {
                val id = r.str("node_id")
                if (id in havePeers) continue
                storage.upsertPeer(id, blob(r["pubkey"]) ?: continue, r.str("name"), lng(r["name_ts"]) ?: 0)
                (r["override"] as? String)?.let { storage.setPeerOverride(id, it) }
            }
            val haveGroups = storage.loadGroups().map { it.groupId.hex() }.toSet()
            val members = rows("group_members").groupBy({ it.str("group_id") }, { it.str("node_id") })
            for (r in rows("groups")) {
                val gid = r.str("group_id")
                if (gid in haveGroups) continue
                storage.saveGroup(gid.unhex(), r.str("name"), (r["version"] as Number).toInt(), members[gid].orEmpty(),
                    (r["left"] as? Number)?.toInt() == 1)
            }
            val names = z.entries().toList().map { it.name }.toSet()
            for (r in rows("files")) {
                val fid = r.str("file_id")
                if (storage.loadFiles().any { it.fileId.hex() == fid }) continue
                val dir = File(storage.filesDir, fid)
                val fname = File(r.str("path").ifEmpty { "file" }).name
                val member = "files/$fid/$fname"
                val dest = File(dir, fname)
                var state = r.str("state")
                var have = blob(r["have"]) ?: ByteArray(0)
                if (member in names) { dir.mkdirs(); dest.writeBytes(read(member)!!) }
                else if (state == "complete") { state = "offered"; have = ByteArray(have.size) }
                storage.saveFile(FileRecord(fid.unhex(), r.str("msg_id").unhex(), r.str("conv"), r.str("sender"), r.str("name"),
                    r.str("mime"), lng(r["size"]) ?: 0, r.str("sha256"), (r["chunk_size"] as Number).toInt(),
                    (r["chunks"] as Number).toInt(), dest.path, state, have))
            }
            val recips = rows("recipients").groupBy { it.str("msg_id") }
            val reacts = rows("reactions").groupBy { it.str("msg_id") }
            var added = 0
            for (r in rows("messages")) {
                if (r.str("kind") == "system") continue // this device keeps its own
                var meta = (r["meta"] as? String)?.let { runCatching { Json.obj(it) }.getOrNull() } ?: emptyMap()
                val fid = meta["f"] as? String
                if (fid != null && "files/$fid/thumb.jpg" in names) {
                    val th = File(File(storage.filesDir, fid).apply { mkdirs() }, "thumb.jpg")
                    th.writeBytes(read("files/$fid/thumb.jpg")!!)
                    meta = meta + ("thumb" to th.path)
                }
                val id = r.str("msg_id")
                val m = StoredMessage(id.unhex(), r.str("conv"), r.str("sender"), lng(r["ts"]) ?: 0, r.str("kind"), r.str("text"),
                    blob(r["reply_to"]), meta, edited = r["edited_at"] != null, deleted = (r["deleted"] as? Number)?.toInt() == 1)
                val rs = recips[id].orEmpty()
                if (!storage.saveMessage(m, rs.map { it.str("node_id") }, read = (r["read_local"] as? Number)?.toInt() != 0)) continue
                added++
                for (x in rs) {
                    if (x["read_at"] != null) storage.markRead(m.msgId, x.str("node_id"))
                    else if (x["delivered_at"] != null) storage.markDelivered(m.msgId, x.str("node_id"))
                }
                for (x in reacts[id].orEmpty()) storage.setReaction(m.msgId, x.str("node_id"), x.str("emoji"), true)
            }
            for (r in rows("app_events")) {
                val body = (r["body"] as? String)?.let { runCatching { Json.parse(it) }.getOrNull() }
                storage.saveAppEvent(AppEvent(r.str("event_id").unhex(), r.str("session"), r.str("app"), r.str("conv"), r.str("sender"),
                    (r["seq"] as Number).toInt(), r.str("type"), body, lng(r["ts"]) ?: 0))
            }
            return added
        }
    }
}
