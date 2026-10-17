package com.muninn

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.net.Uri
import com.muninn.core.Json
import com.muninn.core.Nacl
import com.muninn.core.hex
import com.muninn.core.int
import com.muninn.core.long
import com.muninn.core.str
import com.muninn.core.unhex
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/**
 * Chat backup, same zip as python/src/muninn/backup.py (desktop ↔ phone):
 * manifest.json, tables.json (rows, blobs as hex), files/<file id>/<name>.
 * The identity key is only included with a password (PBKDF2-SHA256 →
 * XSalsa20-Poly1305) and only restored into a fresh install.
 */
object Backup {
    private const val FORMAT = "muninn-backup"
    private const val VERSION = 1
    private const val ITERS = 200_000

    // python column names; Android's groups table calls `left` "left_group"
    private val TABLES: List<Triple<String, List<String>, Set<String>>> = listOf(
        Triple("peers", listOf("node_id", "pubkey", "name", "name_ts", "override", "first_seen", "last_seen", "replaced_by"), setOf("pubkey")),
        Triple("groups", listOf("group_id", "name", "version", "left", "updated_at"), setOf("group_id")),
        Triple("group_members", listOf("group_id", "node_id"), setOf("group_id")),
        Triple("messages", listOf("msg_id", "conv", "sender", "ts", "kind", "text", "reply_to", "meta", "edited_at", "deleted", "read_local"),
            setOf("msg_id", "reply_to")),
        Triple("recipients", listOf("msg_id", "node_id", "delivered_at", "read_at"), setOf("msg_id")),
        Triple("reactions", listOf("msg_id", "node_id", "emoji", "ts"), setOf("msg_id")),
        Triple("files", listOf("file_id", "msg_id", "conv", "sender", "name", "mime", "size", "sha256", "chunk_size", "chunks", "path", "state", "have"),
            setOf("file_id", "msg_id", "have")),
        Triple("app_events", listOf("event_id", "session", "app", "conv", "sender", "seq", "type", "body", "ts"), setOf("event_id")),
    )
    private fun col(c: String) = if (c == "left") "left_group" else c

    private fun key(password: String, salt: ByteArray, iters: Int): ByteArray =
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec(password.toCharArray(), salt, iters, 256)).encoded

    private fun db(): SQLiteDatabase = (Runtime.node!!.storage as AndroidStorage).writableDatabase

    /** Write a backup to [out]; returns (messages, files). */
    fun export(ctx: Context, out: Uri, password: String?): Pair<Int, Int> {
        val n = Runtime.node ?: error("Muninn isn't running")
        val db = db()
        val tables = LinkedHashMap<String, List<MutableMap<String, Any?>>>()
        for ((t, cols, blobs) in TABLES) {
            val rows = ArrayList<MutableMap<String, Any?>>()
            db.rawQuery("SELECT ${cols.joinToString { col(it) }} FROM $t", null).use { c ->
                while (c.moveToNext()) {
                    val r = LinkedHashMap<String, Any?>()
                    cols.forEachIndexed { i, name ->
                        r[name] = when {
                            c.isNull(i) -> null
                            name in blobs -> c.getBlob(i).hex()
                            c.getType(i) == android.database.Cursor.FIELD_TYPE_INTEGER -> c.getLong(i)
                            else -> c.getString(i)
                        }
                    }
                    rows.add(r)
                }
            }
            tables[t] = rows
        }
        val id = n.identity
        val identity = if (password.isNullOrEmpty()) null else {
            val salt = Nacl.randomBytes(16)
            val plain = Json.write(mapOf("privkey" to id.privkey.hex(), "node_id" to id.nodeId,
                "display_name" to n.mesh.name, "name_ts" to n.mesh.nameTs)).toByteArray()
            mapOf("kdf" to "pbkdf2-sha256", "iters" to ITERS, "salt" to salt.hex(),
                "box" to Nacl.seal(plain, key(password, salt, ITERS)).hex())
        }
        val manifest = mapOf("format" to FORMAT, "version" to VERSION, "created" to System.currentTimeMillis() / 1000,
            "from" to id.nodeId, "name" to n.mesh.name, "identity" to identity)
        ctx.contentResolver.openOutputStream(out)!!.use { os ->
            ZipOutputStream(os).use { z ->
                fun put(name: String, bytes: ByteArray) { z.putNextEntry(ZipEntry(name)); z.write(bytes); z.closeEntry() }
                put("manifest.json", Json.write(manifest).toByteArray())
                for (r in tables["files"]!!) {
                    val f = File(r["path"] as? String ?: "")
                    if (r["state"] == "complete" && f.isFile) put("files/${r["file_id"]}/${f.name}", f.readBytes())
                    r["path"] = f.name
                }
                for (r in tables["messages"]!!) {
                    val meta = runCatching { Json.obj(r["meta"] as? String ?: "{}") }.getOrDefault(emptyMap())
                    val th = File(meta["thumb"] as? String ?: "")
                    if (th.isFile && meta["f"] != null) put("files/${meta["f"]}/thumb.jpg", th.readBytes())
                }
                put("tables.json", Json.write(tables).toByteArray())
            }
        }
        return tables["messages"]!!.size to tables["files"]!!.size
    }

    data class Result(val messages: Int, val people: Int, val rooms: Int, val identity: Boolean)

    /** Merge a backup; restarts the radio so everything (or a restored identity) takes effect. */
    @Suppress("UNCHECKED_CAST")
    fun import(ctx: Context, src: Uri, password: String?): Result {
        val entries = HashMap<String, ByteArray>()
        ctx.contentResolver.openInputStream(src)!!.use { ins ->
            ZipInputStream(ins).use { z ->
                while (true) {
                    val e = z.nextEntry ?: break
                    if (!e.isDirectory) entries[e.name] = z.readBytes()
                }
            }
        }
        val manifest = Json.obj(String(entries["manifest.json"] ?: error("not a Muninn backup")))
        if (manifest.str("format") != FORMAT) error("not a Muninn backup")
        if (manifest.int("version") > VERSION) error("backup from a newer Muninn — update first")
        val node = Runtime.node ?: error("Muninn isn't running (Bluetooth off or permission missing)")
        val storage = node.storage as AndroidStorage
        val db = db()
        val fresh = db.rawQuery("SELECT 1 FROM messages LIMIT 1", null).use { !it.moveToFirst() } &&
            db.rawQuery("SELECT 1 FROM peers LIMIT 1", null).use { !it.moveToFirst() }
        var restored = false
        val sealed = manifest["identity"] as? Map<String, Any?>
        if (sealed != null && !password.isNullOrEmpty()) {
            val k = key(password, sealed.str("salt").unhex(), sealed.int("iters"))
            val plain = runCatching { Nacl.open(sealed.str("box").unhex(), k) }
                .getOrElse { error("wrong password (or a damaged backup)") }
            val d = Json.obj(String(plain))
            if (!fresh && d.str("node_id") != Runtime.node!!.nodeId)
                error("this phone already has chats as someone else — restore your identity into a fresh install")
            if (fresh) {
                db.execSQL("DELETE FROM identity")
                db.execSQL("INSERT INTO identity (id, privkey, node_id, display_name, name_ts, created_at) VALUES (0,?,?,?,?,?)",
                    arrayOf(d.str("privkey").unhex(), d.str("node_id"), d.str("display_name"), d.long("name_ts"), System.currentTimeMillis() / 1000))
                Runtime.prefs(ctx).edit().putString("name", d.str("display_name")).apply()
                restored = true
            }
        }
        if (!restored && manifest.str("from").isNotEmpty() && manifest.str("from") != Runtime.node!!.nodeId)
            error("this is ${manifest.str("name").ifEmpty { "someone else" }}'s backup — it only goes back into their own Muninn " +
                "(or a fresh install, with the password)")
        val tables = Json.obj(String(entries["tables.json"] ?: error("damaged backup")))
        val counts = HashMap<String, Int>()
        db.beginTransaction()
        try {
            for ((t, cols, blobs) in TABLES) {
                var added = 0
                for (raw in (tables[t] as? List<Map<String, Any?>>).orEmpty()) {
                    val r = raw.toMutableMap()
                    if (t == "files") {
                        val fid = r.str("file_id")
                        if (!Regex("^[0-9a-f]{32}$").matches(fid)) continue // ids become directory names
                        val dir = File(storage.filesDir, fid)
                        val bytes = entries["files/$fid/${r["path"]}"]
                        val dest = File(dir, com.muninn.core.Mesh.safeName(File(r["path"] as? String ?: "file").name))
                        if (bytes != null) { dir.mkdirs(); dest.writeBytes(bytes) }
                        else if (r["state"] == "complete") { r["state"] = "offered"; r["have"] = "" }
                        r["path"] = dest.path
                    }
                    if (t == "messages") {
                        val meta = runCatching { Json.obj(r["meta"] as? String ?: "{}") }.getOrDefault(emptyMap()).toMutableMap()
                        if (meta.remove("thumb") != null) r["meta"] = Json.write(meta) // a path on the other device
                        val fid = (meta["f"] as? String)?.takeIf { Regex("^[0-9a-f]{32}$").matches(it) }
                        val th = fid?.let { entries["files/$it/thumb.jpg"] }
                        if (th != null) {
                            val f = File(File(storage.filesDir, fid).apply { mkdirs() }, "thumb.jpg"); f.writeBytes(th)
                            meta["thumb"] = f.path; r["meta"] = Json.write(meta)
                        }
                    }
                    val cv = ContentValues()
                    for (c in cols) {
                        val v = r[c]
                        when {
                            v == null -> cv.putNull(col(c))
                            c in blobs -> cv.put(col(c), (v as String).unhex())
                            v is Number -> cv.put(col(c), v.toLong())
                            v is Boolean -> cv.put(col(c), if (v) 1 else 0)
                            else -> cv.put(col(c), v.toString())
                        }
                    }
                    if (db.insertWithOnConflict(t, null, cv, SQLiteDatabase.CONFLICT_IGNORE) != -1L) added++
                }
                counts[t] = added
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        // reload everything (and the restored identity) with a fresh node
        Runtime.stop()
        Runtime.start(ctx)
        Runtime.bump()
        return Result(counts["messages"] ?: 0, counts["peers"] ?: 0, counts["groups"] ?: 0, restored)
    }
}
