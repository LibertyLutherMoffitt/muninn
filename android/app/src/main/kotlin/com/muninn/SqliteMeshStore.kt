package com.muninn

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/**
 * On-disk [HistoryStore]: keys, names, groups, messages and their ticks
 * survive the process being killed, which Android does to a background app
 * whenever it likes. Mirrors the tables of `storage.py` closely enough that
 * the two are easy to compare, without trying to share a file.
 *
 * Plain SQLiteOpenHelper rather than Room: eight small queries do not justify
 * an annotation processor in the build.
 */
class SqliteMeshStore(ctx: Context) :
    SQLiteOpenHelper(ctx, "muninn.db", null, VERSION),
    HistoryStore {

    override fun onConfigure(db: SQLiteDatabase) {
        db.enableWriteAheadLogging()
    }

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE peers (id TEXT PRIMARY KEY, pubkey BLOB, name TEXT, override TEXT)")
        db.execSQL("CREATE TABLE aliases (transport TEXT PRIMARY KEY, wire_id TEXT NOT NULL)")
        db.execSQL("CREATE TABLE groups (id BLOB PRIMARY KEY, name TEXT NOT NULL)")
        db.execSQL(
            "CREATE TABLE group_members (group_id BLOB NOT NULL, member TEXT NOT NULL, " +
                "pubkey BLOB NOT NULL, PRIMARY KEY (group_id, member))",
        )
        db.execSQL(
            "CREATE TABLE messages (msg_id BLOB PRIMARY KEY, group_id BLOB NOT NULL, " +
                "sender TEXT NOT NULL, body TEXT NOT NULL, ts INTEGER NOT NULL, " +
                "outgoing INTEGER NOT NULL, displayed INTEGER NOT NULL DEFAULT 0)",
        )
        db.execSQL(
            "CREATE TABLE recipients (msg_id BLOB NOT NULL, recipient TEXT NOT NULL, " +
                "acked INTEGER NOT NULL DEFAULT 0, read INTEGER NOT NULL DEFAULT 0, " +
                "PRIMARY KEY (msg_id, recipient))",
        )
        db.execSQL("CREATE TABLE seen (msg_id BLOB PRIMARY KEY)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Forward-only migrations go here, one `if (oldVersion < N)` per step.
    }

    // --- Peers ---

    override fun savePeerKey(wireId: String, pubkey: ByteArray, direct: Boolean) {
        val keep = if (direct) "excluded.pubkey" else "COALESCE(peers.pubkey, excluded.pubkey)"
        writableDatabase.execSQL(
            "INSERT INTO peers (id, pubkey) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET pubkey = $keep",
            arrayOf(wireId, pubkey),
        )
    }

    override fun savePeerName(wireId: String, name: String) = upsertPeer(wireId, "name", name)

    override fun saveOverride(wireId: String, name: String) = upsertPeer(wireId, "override", name)

    private fun upsertPeer(wireId: String, column: String, value: String) {
        writableDatabase.execSQL(
            "INSERT INTO peers (id, $column) VALUES (?, ?) " +
                "ON CONFLICT(id) DO UPDATE SET $column = excluded.$column",
            arrayOf<Any?>(wireId, value.ifEmpty { null }),
        )
    }

    override fun saveAlias(transport: String, wireId: String) {
        writableDatabase.insertWithOnConflict(
            "aliases",
            null,
            ContentValues().apply {
                put("transport", transport)
                put("wire_id", wireId)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    override fun loadPeers(): List<StoredPeer> =
        readableDatabase.rawQuery("SELECT id, pubkey, name, override FROM peers", null).use { c ->
            buildList {
                while (c.moveToNext()) {
                    add(
                        StoredPeer(
                            c.getString(0),
                            if (c.isNull(1)) null else c.getBlob(1),
                            if (c.isNull(2)) null else c.getString(2),
                            if (c.isNull(3)) null else c.getString(3),
                        ),
                    )
                }
            }
        }

    override fun loadAliases(): Map<String, String> =
        readableDatabase.rawQuery("SELECT transport, wire_id FROM aliases", null).use { c ->
            buildMap { while (c.moveToNext()) put(c.getString(0), c.getString(1)) }
        }

    // --- Groups ---

    override fun saveGroup(group: MeshGroup) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.insertWithOnConflict(
                "groups",
                null,
                ContentValues().apply {
                    put("id", group.id)
                    put("name", group.name)
                },
                SQLiteDatabase.CONFLICT_REPLACE,
            )
            for ((member, key) in group.members) {
                db.insertWithOnConflict(
                    "group_members",
                    null,
                    ContentValues().apply {
                        put("group_id", group.id)
                        put("member", member)
                        put("pubkey", key)
                    },
                    SQLiteDatabase.CONFLICT_REPLACE,
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    override fun loadGroups(): List<MeshGroup> {
        val db = readableDatabase
        // rawQuery binds only strings, so match BLOB ids in Kotlin: one pass
        // over the (small) members table rather than a query per group.
        val members = HashMap<String, LinkedHashMap<String, ByteArray>>()
        db.rawQuery("SELECT group_id, member, pubkey FROM group_members ORDER BY rowid", null).use { c ->
            while (c.moveToNext()) {
                members.getOrPut(c.getBlob(0).toHex()) { LinkedHashMap() }[c.getString(1)] = c.getBlob(2)
            }
        }
        return db.rawQuery("SELECT id, name FROM groups ORDER BY rowid", null).use { c ->
            buildList {
                while (c.moveToNext()) {
                    val id = c.getBlob(0)
                    add(MeshGroup(id, c.getString(1), members[id.toHex()].orEmpty()))
                }
            }
        }
    }

    // --- Messages ---

    override fun claimSeen(msgId: ByteArray): Boolean =
        writableDatabase.insertWithOnConflict(
            "seen",
            null,
            ContentValues().apply { put("msg_id", msgId) },
            SQLiteDatabase.CONFLICT_IGNORE,
        ) != -1L

    override fun releaseSeen(msgId: ByteArray) {
        writableDatabase.execSQL("DELETE FROM seen WHERE msg_id = ?", arrayOf(msgId))
    }

    override fun saveOutgoing(msg: StoredMessage, recipients: List<String>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            insertMessage(db, msg, outgoing = true)
            for (r in recipients) {
                db.insertWithOnConflict(
                    "recipients",
                    null,
                    ContentValues().apply {
                        put("msg_id", msg.msgId)
                        put("recipient", r)
                    },
                    SQLiteDatabase.CONFLICT_IGNORE,
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    override fun saveIncoming(msg: StoredMessage) = insertMessage(writableDatabase, msg, outgoing = false)

    private fun insertMessage(db: SQLiteDatabase, msg: StoredMessage, outgoing: Boolean) {
        db.insertWithOnConflict(
            "messages",
            null,
            ContentValues().apply {
                put("msg_id", msg.msgId)
                put("group_id", msg.groupId)
                put("sender", msg.sender)
                put("body", msg.text)
                put("ts", msg.timestamp)
                put("outgoing", if (outgoing) 1 else 0)
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        )
    }

    override fun markAcked(msgId: ByteArray, recipient: String) {
        writableDatabase.execSQL(
            "UPDATE recipients SET acked = 1 WHERE msg_id = ? AND recipient = ?",
            arrayOf<Any>(msgId, recipient),
        )
    }

    override fun markRead(msgId: ByteArray, recipient: String) {
        writableDatabase.execSQL(
            "UPDATE recipients SET read = 1, acked = 1 WHERE msg_id = ? AND recipient = ?",
            arrayOf<Any>(msgId, recipient),
        )
    }

    override fun loadUnacked(): List<PendingMessage> = history()
        .filter { it.outgoing && !it.acked.containsAll(it.recipients) }
        .map { row -> PendingMessage(row.message, row.recipients.filter { it !in row.acked }) }

    override fun loadHistory(): List<HistoryRow> = history()

    private fun history(): List<HistoryRow> {
        val db = readableDatabase
        val recipients = HashMap<String, MutableList<Triple<String, Boolean, Boolean>>>()
        db.rawQuery("SELECT msg_id, recipient, acked, read FROM recipients", null).use { c ->
            while (c.moveToNext()) {
                recipients.getOrPut(c.getBlob(0).toHex()) { ArrayList() } +=
                    Triple(c.getString(1), c.getInt(2) != 0, c.getInt(3) != 0)
            }
        }
        return db.rawQuery(
            "SELECT msg_id, group_id, sender, body, ts, outgoing, displayed FROM messages ORDER BY rowid",
            null,
        ).use { c ->
            buildList {
                while (c.moveToNext()) {
                    val id = c.getBlob(0)
                    val rs = recipients[id.toHex()].orEmpty()
                    add(
                        HistoryRow(
                            message = StoredMessage(id, c.getBlob(1), c.getString(2), c.getString(3), c.getLong(4)),
                            outgoing = c.getInt(5) != 0,
                            recipients = rs.map { it.first },
                            acked = rs.filter { it.second }.map { it.first }.toSet(),
                            read = rs.filter { it.third }.map { it.first }.toSet(),
                            displayed = c.getInt(6) != 0,
                        ),
                    )
                }
            }
        }
    }

    override fun markDisplayed(msgIds: List<ByteArray>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (id in msgIds) db.execSQL("UPDATE messages SET displayed = 1 WHERE msg_id = ?", arrayOf(id))
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    companion object {
        private const val VERSION = 1
    }
}
