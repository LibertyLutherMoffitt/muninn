package com.muninn

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.muninn.core.AppEvent
import com.muninn.core.FileRecord
import com.muninn.core.GroupRow
import com.muninn.core.Identity
import com.muninn.core.Json
import com.muninn.core.OutboxEntry
import com.muninn.core.P
import com.muninn.core.PeerRow
import com.muninn.core.Storage
import com.muninn.core.StoredMessage
import java.io.File

/** SQLite [Storage] — same schema as python/src/muninn/storage.py (v2). */
class AndroidStorage(ctx: Context) : SQLiteOpenHelper(ctx, "muninn2.db", null, 3), Storage {
    override val filesDir: File = File(ctx.filesDir, "files").apply { mkdirs() }
    private val lock = Any()

    override fun onConfigure(db: SQLiteDatabase) {
        db.enableWriteAheadLogging()
        db.setForeignKeyConstraintsEnabled(true)
    }

    override fun onCreate(db: SQLiteDatabase) {
        SCHEMA.split(";").map { it.trim() }.filter { it.isNotEmpty() }.forEach(db::execSQL)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) db.execSQL("ALTER TABLE peers ADD COLUMN replaced_by TEXT")
        if (oldVersion < 3) db.execSQL("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    }

    private inline fun <T> w(f: (SQLiteDatabase) -> T): T = synchronized(lock) { f(writableDatabase) }
    private fun now() = P.now()

    private fun <T> query(sql: String, args: Array<Any?> = emptyArray(), row: (Cursor) -> T): List<T> = w { db ->
        db.rawQuery(sql, args.map { it?.toString() }.toTypedArray()).use { c ->
            val out = ArrayList<T>()
            while (c.moveToNext()) out.add(row(c))
            out
        }
    }

    // bind blobs properly (rawQuery only takes strings): use compiled statements for blob lookups
    private fun <T> queryBlob(sql: String, blob: ByteArray, row: (Cursor) -> T): List<T> = w { db ->
        db.rawQueryWithFactory({ _, masterQuery, editTable, q ->
            q.bindBlob(1, blob)
            android.database.sqlite.SQLiteCursor(masterQuery, editTable, q)
        }, sql, null, null).use { c ->
            val out = ArrayList<T>()
            while (c.moveToNext()) out.add(row(c))
            out
        }
    }

    override fun getSetting(key: String): String? =
        query("SELECT value FROM settings WHERE key = ?", arrayOf(key)) { it.getString(0) }.firstOrNull()

    override fun setSetting(key: String, value: String) = w { db ->
        db.execSQL("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            arrayOf(key, value))
    }

    // ---- identity ---------------------------------------------------------

    override fun getIdentity(): Identity? =
        query("SELECT privkey, node_id, display_name, name_ts FROM identity") {
            Identity(it.getBlob(0), it.getString(1), it.getString(2) ?: "", it.getLong(3))
        }.firstOrNull()

    override fun createIdentity(privkey: ByteArray, name: String): Identity {
        val id = Identity(privkey, P.randomNodeId(), name, if (name.isNotEmpty()) now() else 0)
        w { db ->
            db.insert("identity", null, ContentValues().apply {
                put("id", 0); put("privkey", privkey); put("node_id", id.nodeId)
                put("display_name", name); put("name_ts", id.nameTs); put("created_at", now())
            })
        }
        return id
    }

    override fun setDisplayName(name: String, nameTs: Long) {
        w { it.execSQL("UPDATE identity SET display_name = ?, name_ts = ? WHERE id = 0", arrayOf(name, nameTs)) }
    }

    // ---- peers --------------------------------------------------------------

    override fun upsertPeer(nodeId: String, pubkey: ByteArray, name: String, nameTs: Long) = w { db ->
        db.execSQL(
            "INSERT INTO peers (node_id, pubkey, name, name_ts, first_seen, last_seen) VALUES (?,?,?,?,?,?) " +
                "ON CONFLICT(node_id) DO UPDATE SET name = excluded.name, name_ts = excluded.name_ts",
            arrayOf(nodeId, pubkey, name, nameTs, now(), now()),
        )
    }

    override fun setPeerOverride(nodeId: String, name: String?) = w { db ->
        db.execSQL("UPDATE peers SET override = ? WHERE node_id = ?", arrayOf(name?.ifEmpty { null }, nodeId))
    }

    override fun touchPeer(nodeId: String) = w { db ->
        db.execSQL("UPDATE peers SET last_seen = ? WHERE node_id = ?", arrayOf(now(), nodeId))
    }

    override fun loadPeers() = query("SELECT node_id, pubkey, name, name_ts, override, last_seen, replaced_by FROM peers") {
        PeerRow(it.getString(0), it.getBlob(1), it.getString(2) ?: "", it.getLong(3), it.getString(4), it.getLong(5), it.getString(6))
    }

    override fun migratePeer(old: String, new: String): List<OutboxEntry> = w { db ->
        db.beginTransaction()
        try {
            db.execSQL("UPDATE messages SET conv = ? WHERE conv = ?", arrayOf("dm:$new", "dm:$old"))
            db.execSQL("UPDATE messages SET sender = ? WHERE sender = ?", arrayOf(new, old))
            db.execSQL("UPDATE OR IGNORE recipients SET node_id = ? WHERE node_id = ?", arrayOf(new, old))
            db.execSQL("UPDATE OR IGNORE reactions SET node_id = ? WHERE node_id = ?", arrayOf(new, old))
            db.execSQL("UPDATE OR IGNORE group_members SET node_id = ? WHERE node_id = ?", arrayOf(new, old))
            db.execSQL("DELETE FROM transports WHERE node_id = ?", arrayOf(old))
            db.execSQL("UPDATE peers SET replaced_by = ? WHERE node_id = ?", arrayOf(new, old))
            val out = db.rawQuery("SELECT packet_id, dst, body, ts, msg_id, attempts FROM outbox WHERE dst = ?", arrayOf(old)).use { c ->
                val l = ArrayList<OutboxEntry>()
                while (c.moveToNext()) l.add(OutboxEntry(c.getBlob(0), c.getString(1), c.getBlob(2), c.getLong(3),
                    if (c.isNull(4)) null else c.getBlob(4), c.getInt(5)))
                l
            }
            db.execSQL("DELETE FROM outbox WHERE dst = ?", arrayOf(old))
            db.setTransactionSuccessful()
            out
        } finally {
            db.endTransaction()
        }
    }

    // ---- transports ---------------------------------------------------------

    override fun saveTransport(nodeId: String, addr: String) = w { db ->
        db.execSQL(
            "INSERT INTO transports (node_id, addr, last_ok) VALUES (?,?,?) " +
                "ON CONFLICT(node_id, addr) DO UPDATE SET last_ok = excluded.last_ok",
            arrayOf(nodeId, addr, now()),
        )
    }

    override fun loadTransports() = query("SELECT node_id, addr, last_ok FROM transports ORDER BY last_ok DESC") {
        Triple(it.getString(0), it.getString(1), it.getLong(2))
    }

    // ---- groups ---------------------------------------------------------------

    override fun saveGroup(groupId: ByteArray, name: String, version: Int, members: List<String>, left: Boolean) = w { db ->
        db.beginTransaction()
        try {
            db.execSQL(
                "INSERT INTO groups (group_id, name, version, left_group, updated_at) VALUES (?,?,?,?,?) " +
                    "ON CONFLICT(group_id) DO UPDATE SET name = excluded.name, version = excluded.version, " +
                    "left_group = excluded.left_group, updated_at = excluded.updated_at",
                arrayOf(groupId, name, version, if (left) 1 else 0, now()),
            )
            db.execSQL("DELETE FROM group_members WHERE group_id = ?", arrayOf(groupId))
            for (m in members) db.execSQL("INSERT INTO group_members (group_id, node_id) VALUES (?,?)", arrayOf(groupId, m))
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    override fun loadGroups(): List<GroupRow> {
        val rows = query("SELECT group_id, name, version, left_group FROM groups") {
            listOf(it.getBlob(0), it.getString(1), it.getInt(2), it.getInt(3) != 0)
        }
        return rows.map { r ->
            val gid = r[0] as ByteArray
            val members = queryBlob("SELECT node_id FROM group_members WHERE group_id = ?", gid) { it.getString(0) }
            GroupRow(gid, r[1] as String, r[2] as Int, members, r[3] as Boolean)
        }
    }

    // ---- messages -------------------------------------------------------------

    override fun saveMessage(m: StoredMessage, recipients: List<String>): Boolean = w { db ->
        db.beginTransaction()
        try {
            val id = db.insertWithOnConflict("messages", null, ContentValues().apply {
                put("msg_id", m.msgId); put("conv", m.conv); put("sender", m.sender); put("ts", m.ts)
                put("kind", m.kind); put("text", m.text); put("reply_to", m.replyTo)
                put("meta", if (m.meta.isEmpty()) null else Json.write(m.meta))
                put("read_local", if (recipients.isNotEmpty()) 1 else 0)
            }, SQLiteDatabase.CONFLICT_IGNORE)
            if (id != -1L) for (r in recipients)
                db.execSQL("INSERT OR IGNORE INTO recipients (msg_id, node_id) VALUES (?,?)", arrayOf(m.msgId, r))
            db.setTransactionSuccessful()
            id != -1L
        } finally {
            db.endTransaction()
        }
    }

    private fun changes(db: SQLiteDatabase): Int = db.rawQuery("SELECT changes()", null).use { c -> c.moveToFirst(); c.getInt(0) }

    override fun markDelivered(msgId: ByteArray, nodeId: String) = w { db ->
        db.execSQL("UPDATE recipients SET delivered_at = ? WHERE msg_id = ? AND node_id = ? AND delivered_at IS NULL",
            arrayOf(now(), msgId, nodeId))
        changes(db) > 0
    }

    override fun markRead(msgId: ByteArray, nodeId: String) = w { db ->
        db.execSQL("UPDATE recipients SET read_at = ?, delivered_at = COALESCE(delivered_at, ?) " +
            "WHERE msg_id = ? AND node_id = ? AND read_at IS NULL", arrayOf(now(), now(), msgId, nodeId))
        changes(db) > 0
    }

    override fun markReadLocal(conv: String): List<Pair<ByteArray, String>> {
        val rows = query("SELECT msg_id, sender FROM messages WHERE conv = ? AND read_local = 0 AND kind IN ('text','file')",
            arrayOf(conv)) { it.getBlob(0) to it.getString(1) }
        w { it.execSQL("UPDATE messages SET read_local = 1 WHERE conv = ?", arrayOf(conv)) }
        return rows
    }

    override fun unreadCounts(): Map<String, Int> =
        query("SELECT conv, COUNT(*) FROM messages WHERE read_local = 0 AND kind IN ('text','file') GROUP BY conv") {
            it.getString(0) to it.getInt(1)
        }.toMap()

    override fun editMessage(msgId: ByteArray, sender: String, text: String) = w { db ->
        db.execSQL("UPDATE messages SET text = ?, edited_at = ? WHERE msg_id = ? AND sender = ? AND deleted = 0",
            arrayOf(text, now(), msgId, sender))
        changes(db) > 0
    }

    override fun deleteMessage(msgId: ByteArray, sender: String) = w { db ->
        db.execSQL("UPDATE messages SET text = '', deleted = 1 WHERE msg_id = ? AND sender = ?", arrayOf(msgId, sender))
        changes(db) > 0
    }

    override fun setReaction(msgId: ByteArray, nodeId: String, emoji: String, on: Boolean) = w { db ->
        if (on) db.execSQL("INSERT OR IGNORE INTO reactions (msg_id, node_id, emoji, ts) VALUES (?,?,?,?)",
            arrayOf(msgId, nodeId, emoji, now()))
        else db.execSQL("DELETE FROM reactions WHERE msg_id = ? AND node_id = ? AND emoji = ?", arrayOf(msgId, nodeId, emoji))
    }

    private fun msgRow(c: Cursor) = StoredMessage(
        msgId = c.getBlob(0), conv = c.getString(1), sender = c.getString(2), ts = c.getLong(3),
        kind = c.getString(4), text = c.getString(5) ?: "", replyTo = if (c.isNull(6)) null else c.getBlob(6),
        meta = if (c.isNull(7)) emptyMap() else Json.obj(c.getString(7)), edited = !c.isNull(8),
        deleted = c.getInt(9) != 0, readLocal = c.getInt(10) != 0,
    )

    private fun hydrate(m: StoredMessage): StoredMessage {
        val st = queryBlob("SELECT node_id, delivered_at, read_at FROM recipients WHERE msg_id = ?", m.msgId) {
            it.getString(0) to when { !it.isNull(2) -> "read"; !it.isNull(1) -> "delivered"; else -> "sent" }
        }.toMap()
        val re = LinkedHashMap<String, MutableList<String>>()
        queryBlob("SELECT node_id, emoji FROM reactions WHERE msg_id = ? ORDER BY ts", m.msgId) { it.getString(0) to it.getString(1) }
            .forEach { (n, e) -> re.getOrPut(e) { ArrayList() }.add(n) }
        return m.copy(status = st, reactions = re)
    }

    override fun getMessage(msgId: ByteArray): StoredMessage? =
        queryBlob("$MSG_SELECT WHERE msg_id = ?", msgId, ::msgRow).firstOrNull()?.let(::hydrate)

    override fun loadHistory(conv: String, limit: Int): List<StoredMessage> =
        query("$MSG_SELECT WHERE conv = ? ORDER BY ts DESC, rowid DESC LIMIT ?", arrayOf(conv, limit), ::msgRow)
            .reversed().map(::hydrate)

    override fun lastMessages(): Map<String, StoredMessage> =
        query("$MSG_SELECT WHERE rowid IN (SELECT MAX(rowid) FROM messages GROUP BY conv)", emptyArray(), ::msgRow)
            .associateBy { it.conv }

    // ---- outbox ------------------------------------------------------------------

    override fun outboxAdd(e: OutboxEntry) = w { db ->
        db.execSQL("INSERT OR IGNORE INTO outbox (packet_id, dst, body, ts, msg_id, attempts, created_at) VALUES (?,?,?,?,?,0,?)",
            arrayOf(e.packetId, e.dst, e.body, e.ts, e.msgId, now()))
    }

    override fun outboxAck(packetId: ByteArray): Pair<String, ByteArray?>? {
        val row = queryBlob("SELECT dst, msg_id FROM outbox WHERE packet_id = ?", packetId) {
            it.getString(0) to (if (it.isNull(1)) null else it.getBlob(1))
        }.firstOrNull() ?: return null
        w { it.execSQL("DELETE FROM outbox WHERE packet_id = ?", arrayOf(packetId)) }
        return row
    }

    override fun outboxLoad() = query("SELECT packet_id, dst, body, ts, msg_id, attempts FROM outbox ORDER BY created_at") {
        OutboxEntry(it.getBlob(0), it.getString(1), it.getBlob(2), it.getLong(3), if (it.isNull(4)) null else it.getBlob(4), it.getInt(5))
    }

    override fun claimSeen(packetId: ByteArray) = w { db ->
        db.insertWithOnConflict("seen", null, ContentValues().apply { put("packet_id", packetId); put("ts", now()) },
            SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    // ---- relay -----------------------------------------------------------------------

    override fun relayPut(packetId: ByteArray, dst: String, frame: ByteArray) = w { db ->
        db.execSQL("INSERT OR IGNORE INTO relay_store (packet_id, dst, frame, created_at) VALUES (?,?,?,?)",
            arrayOf(packetId, dst, frame, now()))
    }

    override fun relayTake(dst: String): List<ByteArray> = w { db ->
        val out = db.rawQuery("SELECT frame FROM relay_store WHERE dst = ? ORDER BY created_at", arrayOf(dst)).use { c ->
            val l = ArrayList<ByteArray>(); while (c.moveToNext()) l.add(c.getBlob(0)); l
        }
        db.execSQL("DELETE FROM relay_store WHERE dst = ?", arrayOf(dst))
        out
    }

    override fun relayExpire(maxAgeSec: Long, maxRows: Int) = w { db ->
        db.execSQL("DELETE FROM relay_store WHERE created_at < ?", arrayOf(now() - maxAgeSec))
        db.execSQL("DELETE FROM relay_store WHERE packet_id NOT IN (SELECT packet_id FROM relay_store ORDER BY created_at DESC LIMIT ?)",
            arrayOf(maxRows))
    }

    // ---- files -------------------------------------------------------------------------

    override fun saveFile(f: FileRecord) = w { db ->
        db.execSQL(
            "INSERT INTO files (file_id, msg_id, conv, sender, name, mime, size, sha256, chunk_size, chunks, path, state, have) " +
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET path = excluded.path, " +
                "state = excluded.state, have = excluded.have",
            arrayOf(f.fileId, f.msgId, f.conv, f.sender, f.name, f.mime, f.size, f.sha256, f.chunkSize, f.chunks, f.path, f.state, f.have),
        )
    }

    override fun loadFiles() = query("SELECT file_id, msg_id, conv, sender, name, mime, size, sha256, chunk_size, chunks, path, state, have FROM files") {
        FileRecord(it.getBlob(0), it.getBlob(1), it.getString(2), it.getString(3), it.getString(4), it.getString(5),
            it.getLong(6), it.getString(7), it.getInt(8), it.getInt(9), it.getString(10), it.getString(11),
            if (it.isNull(12)) ByteArray(0) else it.getBlob(12))
    }

    // ---- apps ----------------------------------------------------------------------------

    override fun saveAppEvent(ev: AppEvent) = w { db ->
        db.insertWithOnConflict("app_events", null, ContentValues().apply {
            put("event_id", ev.eventId); put("session", ev.session); put("app", ev.app); put("conv", ev.conv)
            put("sender", ev.sender); put("seq", ev.seq); put("type", ev.type); put("body", Json.write(ev.body)); put("ts", ev.ts)
        }, SQLiteDatabase.CONFLICT_IGNORE) != -1L
    }

    override fun loadAppEvents(session: String) =
        query("SELECT event_id, session, app, conv, sender, seq, type, body, ts FROM app_events WHERE session = ? ORDER BY seq, ts, rowid",
            arrayOf(session)) {
            AppEvent(it.getBlob(0), it.getString(1), it.getString(2), it.getString(3), it.getString(4), it.getInt(5),
                it.getString(6), Json.parse(it.getString(7)), it.getLong(8))
        }

    override fun listAppSessions(conv: String?) =
        if (conv == null) query("SELECT session FROM app_events GROUP BY session ORDER BY MAX(ts) DESC") { it.getString(0) }
        else query("SELECT session FROM app_events WHERE conv = ? GROUP BY session ORDER BY MAX(ts) DESC", arrayOf(conv)) { it.getString(0) }

    companion object {
        private const val MSG_SELECT =
            "SELECT msg_id, conv, sender, ts, kind, text, reply_to, meta, edited_at, deleted, read_local FROM messages"

        private val SCHEMA = """
            CREATE TABLE identity (id INTEGER PRIMARY KEY CHECK (id = 0), privkey BLOB NOT NULL, node_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '', name_ts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
            CREATE TABLE peers (node_id TEXT PRIMARY KEY, pubkey BLOB NOT NULL, name TEXT, name_ts INTEGER NOT NULL DEFAULT 0,
                override TEXT, first_seen INTEGER, last_seen INTEGER, replaced_by TEXT);
            CREATE TABLE transports (node_id TEXT NOT NULL, addr TEXT NOT NULL, last_ok INTEGER NOT NULL, PRIMARY KEY (node_id, addr));
            CREATE TABLE groups (group_id BLOB PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                left_group INTEGER NOT NULL DEFAULT 0, updated_at INTEGER);
            CREATE TABLE group_members (group_id BLOB NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                node_id TEXT NOT NULL, PRIMARY KEY (group_id, node_id));
            CREATE TABLE messages (msg_id BLOB PRIMARY KEY, conv TEXT NOT NULL, sender TEXT NOT NULL, ts INTEGER NOT NULL,
                kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', reply_to BLOB, meta TEXT, edited_at INTEGER,
                deleted INTEGER NOT NULL DEFAULT 0, read_local INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX idx_messages_conv ON messages(conv, ts);
            CREATE TABLE recipients (msg_id BLOB NOT NULL REFERENCES messages(msg_id) ON DELETE CASCADE, node_id TEXT NOT NULL,
                delivered_at INTEGER, read_at INTEGER, PRIMARY KEY (msg_id, node_id));
            CREATE TABLE reactions (msg_id BLOB NOT NULL, node_id TEXT NOT NULL, emoji TEXT NOT NULL, ts INTEGER NOT NULL,
                PRIMARY KEY (msg_id, node_id, emoji));
            CREATE TABLE outbox (packet_id BLOB PRIMARY KEY, dst TEXT NOT NULL, body BLOB NOT NULL, ts INTEGER NOT NULL,
                msg_id BLOB, attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
            CREATE TABLE seen (packet_id BLOB PRIMARY KEY, ts INTEGER NOT NULL);
            CREATE TABLE relay_store (packet_id BLOB PRIMARY KEY, dst TEXT NOT NULL, frame BLOB NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE files (file_id BLOB PRIMARY KEY, msg_id BLOB NOT NULL, conv TEXT NOT NULL, sender TEXT NOT NULL,
                name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, chunk_size INTEGER NOT NULL,
                chunks INTEGER NOT NULL, path TEXT NOT NULL, state TEXT NOT NULL, have BLOB);
            CREATE TABLE app_events (event_id BLOB PRIMARY KEY, session TEXT NOT NULL, app TEXT NOT NULL, conv TEXT NOT NULL,
                sender TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, body TEXT NOT NULL, ts INTEGER NOT NULL);
            CREATE INDEX idx_app_events_session ON app_events(session, seq);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)
        """.trimIndent()
    }
}
