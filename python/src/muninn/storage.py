"""SQLite persistence (schema v2).

One DB per device. WAL mode; every statement serialized through one
threading.Lock so mesh IO threads, the maintenance thread and the UI thread
can all share it.

A v1 (pre-mesh) database is wiped on open: v1 only ever held test data.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Sequence
from pathlib import Path

from muninn.protocol import random_node_id

SCHEMA_VERSION = 4


def data_dir() -> Path:
    if env := os.environ.get("MUNINN_DATA"):
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "muninn"


def default_db_path() -> Path:
    return data_dir() / "muninn.db"


@dataclass
class Identity:
    privkey: bytes
    node_id: str
    display_name: str
    created_at: int
    name_ts: int = 0


@dataclass
class OutboxEntry:
    packet_id: bytes
    dst: str
    body: bytes
    ts: int
    msg_id: bytes | None
    attempts: int


@dataclass
class StoredMessage:
    msg_id: bytes
    conv: str
    sender: str
    ts: int
    kind: str
    text: str
    reply_to: bytes | None = None
    meta: dict = field(default_factory=dict)
    edited: bool = False
    deleted: bool = False
    # per-recipient (outbound only): {node_id: "sent"|"delivered"|"read"}
    status: dict[str, str] = field(default_factory=dict)
    reactions: dict[str, list[str]] = field(default_factory=dict)  # emoji -> [ids]

    @property
    def state(self) -> str:
        """Aggregate outbound state: worst across recipients."""
        if not self.status:
            return "sent"
        vals = set(self.status.values())
        if "sent" in vals:
            return "sent"
        if "delivered" in vals:
            return "delivered"
        return "read"


@dataclass
class FileRecord:
    file_id: bytes
    msg_id: bytes
    conv: str
    sender: str
    name: str
    mime: str
    size: int
    sha256: str
    chunk_size: int
    chunks: int
    path: str
    state: str  # offered|downloading|complete|failed|sending
    have: bytearray

    @property
    def have_count(self) -> int:
        return sum(bin(b).count("1") for b in self.have)

    def has(self, i: int) -> bool:
        return bool(self.have[i >> 3] & (1 << (i & 7)))

    def mark(self, i: int) -> None:
        self.have[i >> 3] |= 1 << (i & 7)


@dataclass
class AppEvent:
    event_id: bytes
    session: str
    app: str
    conv: str
    sender: str
    seq: int
    type: str
    body: object
    ts: int

    def to_dict(self) -> dict:
        return {
            "eventId": self.event_id.hex(),
            "session": self.session,
            "app": self.app,
            "conv": self.conv,
            "sender": self.sender,
            "seq": self.seq,
            "type": self.type,
            "body": self.body,
            "ts": self.ts,
        }


class Storage:
    def __init__(self, db_path: Path | str | None = None):
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.files_dir = self.path.parent / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).rowcount

    def _migrate(self) -> None:
        with self._lock:
            (cur,) = self._conn.execute("PRAGMA user_version").fetchone()
            if cur == 1:
                # v1 (pre-mesh) data is test data only — start clean.
                tables = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ]
                self._conn.execute("PRAGMA foreign_keys=OFF")
                for t in tables:
                    self._conn.execute(f'DROP TABLE IF EXISTS "{t}"')
                self._conn.execute("PRAGMA foreign_keys=ON")
            if cur < 2:
                self._conn.executescript(_SCHEMA_V2)
            if cur < 3:
                self._conn.executescript(
                    "ALTER TABLE peers ADD COLUMN replaced_by TEXT; PRAGMA user_version = 3;"
                )
            if cur < 4:
                self._conn.executescript(
                    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);"
                    "PRAGMA user_version = 4;"
                )

    # --- identity -----------------------------------------------------------

    def get_identity(self) -> Identity | None:
        rows = self._q(
            "SELECT privkey, node_id, display_name, created_at, name_ts FROM identity"
        )
        if not rows:
            return None
        r = rows[0]
        return Identity(bytes(r[0]), r[1], r[2] or "", r[3], r[4] or 0)

    def create_identity(self, privkey: bytes, name: str = "") -> Identity:
        """Create the local identity (fresh random node id)."""
        now = int(time.time())
        ident = Identity(privkey, random_node_id(), name, now, now if name else 0)
        self._x(
            "INSERT INTO identity (id, privkey, node_id, display_name, created_at, "
            "name_ts) VALUES (0, ?, ?, ?, ?, ?)",
            (ident.privkey, ident.node_id, name, ident.created_at, ident.name_ts),
        )
        return ident

    def set_display_name(self, name: str, name_ts: int) -> None:
        self._x(
            "UPDATE identity SET display_name = ?, name_ts = ? WHERE id = 0",
            (name, name_ts),
        )

    # --- settings -------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self._x(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --- peers ----------------------------------------------------------------

    def upsert_peer(self, node_id: str, pubkey: bytes, name: str, name_ts: int) -> None:
        now = int(time.time())
        self._x(
            "INSERT INTO peers (node_id, pubkey, name, name_ts, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET "
            "name = excluded.name, name_ts = excluded.name_ts",
            (node_id, pubkey, name, name_ts, now, now),
        )

    def set_peer_override(self, node_id: str, name: str | None) -> None:
        self._x(
            "UPDATE peers SET override = ? WHERE node_id = ?", (name or None, node_id)
        )

    def touch_peer(self, node_id: str) -> None:
        self._x(
            "UPDATE peers SET last_seen = ? WHERE node_id = ?",
            (int(time.time()), node_id),
        )

    def load_peers(
        self,
    ) -> list[tuple[str, bytes, str, int, str | None, int, str | None]]:
        return [
            (r[0], bytes(r[1]), r[2] or "", r[3] or 0, r[4], r[5] or 0, r[6])
            for r in self._q(
                "SELECT node_id, pubkey, name, name_ts, override, last_seen, replaced_by "
                "FROM peers"
            )
        ]

    def migrate_peer(self, old: str, new: str) -> list[OutboxEntry]:
        """`old` reinstalled as `new`: move history, receipts, reactions, group
        membership and transports over. Returns old's pending outbox entries
        (removed) so the caller can reseal them for the new key."""
        with self._lock:
            c = self._conn
            c.execute("BEGIN")
            try:
                c.execute(
                    "UPDATE messages SET conv = ? WHERE conv = ?",
                    ("dm:" + new, "dm:" + old),
                )
                c.execute("UPDATE messages SET sender = ? WHERE sender = ?", (new, old))
                c.execute(
                    "UPDATE OR IGNORE recipients SET node_id = ? WHERE node_id = ?",
                    (new, old),
                )
                c.execute(
                    "UPDATE OR IGNORE reactions SET node_id = ? WHERE node_id = ?",
                    (new, old),
                )
                c.execute(
                    "UPDATE OR IGNORE group_members SET node_id = ? WHERE node_id = ?",
                    (new, old),
                )
                c.execute("DELETE FROM transports WHERE node_id = ?", (old,))
                c.execute(
                    "UPDATE peers SET replaced_by = ? WHERE node_id = ?", (new, old)
                )
                rows = c.execute(
                    "SELECT packet_id, dst, body, ts, msg_id, attempts FROM outbox WHERE dst = ?",
                    (old,),
                ).fetchall()
                c.execute("DELETE FROM outbox WHERE dst = ?", (old,))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
        return [
            OutboxEntry(
                bytes(r[0]),
                r[1],
                bytes(r[2]),
                r[3],
                bytes(r[4]) if r[4] else None,
                r[5],
            )
            for r in rows
        ]

    # --- transports -------------------------------------------------------------

    def save_transport(self, node_id: str, addr: str) -> None:
        self._x(
            "INSERT INTO transports (node_id, addr, last_ok) VALUES (?, ?, ?) "
            "ON CONFLICT(node_id, addr) DO UPDATE SET last_ok = excluded.last_ok",
            (node_id, addr, int(time.time())),
        )

    def load_transports(self) -> list[tuple[str, str, int]]:
        return [
            (r[0], r[1], r[2])
            for r in self._q(
                "SELECT node_id, addr, last_ok FROM transports ORDER BY last_ok DESC"
            )
        ]

    # --- groups -------------------------------------------------------------

    def save_group(
        self, group_id: bytes, name: str, version: int, members: list[str], left: bool
    ) -> None:
        with self._lock:
            c = self._conn
            c.execute("BEGIN")
            try:
                c.execute(
                    "INSERT INTO groups (group_id, name, version, left, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
                    "name = excluded.name, version = excluded.version, "
                    "left = excluded.left, updated_at = excluded.updated_at",
                    (group_id, name, version, int(left), int(time.time())),
                )
                c.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
                c.executemany(
                    "INSERT INTO group_members (group_id, node_id) VALUES (?, ?)",
                    [(group_id, m) for m in members],
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    def load_groups(self) -> list[tuple[bytes, str, int, list[str], bool]]:
        out = []
        for gid, name, version, left in self._q(
            "SELECT group_id, name, version, left FROM groups"
        ):
            members = [
                r[0]
                for r in self._q(
                    "SELECT node_id FROM group_members WHERE group_id = ?", (gid,)
                )
            ]
            out.append((bytes(gid), name, version, members, bool(left)))
        return out

    # --- messages -------------------------------------------------------------

    def save_message(self, m: StoredMessage, recipients: Sequence[str] = ()) -> bool:
        """Insert a message. Returns False if msg_id already existed."""
        with self._lock:
            c = self._conn
            c.execute("BEGIN")
            try:
                cur = c.execute(
                    "INSERT OR IGNORE INTO messages (msg_id, conv, sender, ts, kind, "
                    "text, reply_to, meta, read_local) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        m.msg_id,
                        m.conv,
                        m.sender,
                        m.ts,
                        m.kind,
                        m.text,
                        m.reply_to,
                        json.dumps(m.meta) if m.meta else None,
                        1 if recipients else 0,
                    ),
                )
                inserted = cur.rowcount > 0
                if inserted:
                    c.executemany(
                        "INSERT OR IGNORE INTO recipients (msg_id, node_id) "
                        "VALUES (?, ?)",
                        [(m.msg_id, r) for r in recipients],
                    )
                c.execute("COMMIT")
                return inserted
            except Exception:
                c.execute("ROLLBACK")
                raise

    def mark_delivered(self, msg_id: bytes, node_id: str) -> bool:
        return (
            self._x(
                "UPDATE recipients SET delivered_at = ? WHERE msg_id = ? AND "
                "node_id = ? AND delivered_at IS NULL",
                (int(time.time()), msg_id, node_id),
            )
            > 0
        )

    def mark_read(self, msg_id: bytes, node_id: str) -> bool:
        now = int(time.time())
        return (
            self._x(
                "UPDATE recipients SET read_at = ?, delivered_at = "
                "COALESCE(delivered_at, ?) WHERE msg_id = ? AND node_id = ? "
                "AND read_at IS NULL",
                (now, now, msg_id, node_id),
            )
            > 0
        )

    def mark_read_local(self, conv: str) -> list[tuple[bytes, str]]:
        """Mark inbound messages in conv read. Returns [(msg_id, sender)]."""
        rows = self._q(
            "SELECT msg_id, sender FROM messages WHERE conv = ? AND read_local = 0 "
            "AND kind IN ('text', 'file')",
            (conv,),
        )
        self._x("UPDATE messages SET read_local = 1 WHERE conv = ?", (conv,))
        return [(bytes(r[0]), r[1]) for r in rows]

    def unread_counts(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self._q(
                "SELECT conv, COUNT(*) FROM messages WHERE read_local = 0 "
                "AND kind IN ('text', 'file') GROUP BY conv"
            )
        }

    def edit_message(self, msg_id: bytes, sender: str, text: str) -> bool:
        return (
            self._x(
                "UPDATE messages SET text = ?, edited_at = ? WHERE msg_id = ? "
                "AND sender = ? AND deleted = 0",
                (text, int(time.time()), msg_id, sender),
            )
            > 0
        )

    def delete_message(self, msg_id: bytes, sender: str) -> bool:
        return (
            self._x(
                "UPDATE messages SET text = '', deleted = 1 WHERE msg_id = ? "
                "AND sender = ?",
                (msg_id, sender),
            )
            > 0
        )

    def set_reaction(self, msg_id: bytes, node_id: str, emoji: str, on: bool) -> None:
        if on:
            self._x(
                "INSERT OR IGNORE INTO reactions (msg_id, node_id, emoji, ts) "
                "VALUES (?, ?, ?, ?)",
                (msg_id, node_id, emoji, int(time.time())),
            )
        else:
            self._x(
                "DELETE FROM reactions WHERE msg_id = ? AND node_id = ? AND emoji = ?",
                (msg_id, node_id, emoji),
            )

    def get_message(self, msg_id: bytes) -> StoredMessage | None:
        rows = self._q(_MSG_SELECT + " WHERE msg_id = ?", (msg_id,))
        if not rows:
            return None
        return self._hydrate([rows[0]])[0]

    def load_history(
        self, conv: str, limit: int = 100, before_ts: int | None = None
    ) -> list[StoredMessage]:
        if before_ts is None:
            rows = self._q(
                _MSG_SELECT + " WHERE conv = ? ORDER BY ts DESC, rowid DESC LIMIT ?",
                (conv, limit),
            )
        else:
            rows = self._q(
                _MSG_SELECT + " WHERE conv = ? AND ts < ? "
                "ORDER BY ts DESC, rowid DESC LIMIT ?",
                (conv, before_ts, limit),
            )
        return self._hydrate(list(reversed(rows)))

    def last_messages(self) -> dict[str, StoredMessage]:
        rows = self._q(
            _MSG_SELECT + " WHERE rowid IN (SELECT MAX(rowid) FROM messages "
            "WHERE kind != 'app' GROUP BY conv)"
        )
        return {m.conv: m for m in self._hydrate(rows)}

    def _hydrate(self, rows: list[tuple]) -> list[StoredMessage]:
        out = []
        for r in rows:
            m = StoredMessage(
                msg_id=bytes(r[0]),
                conv=r[1],
                sender=r[2],
                ts=r[3],
                kind=r[4],
                text=r[5] or "",
                reply_to=bytes(r[6]) if r[6] else None,
                meta=json.loads(r[7]) if r[7] else {},
                edited=r[8] is not None,
                deleted=bool(r[9]),
            )
            for nid, d, rd in self._q(
                "SELECT node_id, delivered_at, read_at FROM recipients WHERE msg_id = ?",
                (m.msg_id,),
            ):
                m.status[nid] = "read" if rd else ("delivered" if d else "sent")
            for nid, emoji in self._q(
                "SELECT node_id, emoji FROM reactions WHERE msg_id = ? ORDER BY ts",
                (m.msg_id,),
            ):
                m.reactions.setdefault(emoji, []).append(nid)
            out.append(m)
        return out

    # --- outbox (reliable packets awaiting ACK) ---------------------------------

    def outbox_add(self, e: OutboxEntry) -> None:
        self._x(
            "INSERT OR IGNORE INTO outbox (packet_id, dst, body, ts, msg_id, attempts, "
            "created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (e.packet_id, e.dst, e.body, e.ts, e.msg_id, int(time.time())),
        )

    def outbox_ack(self, packet_id: bytes) -> tuple[str, bytes | None] | None:
        """Remove an outbox row. Returns (dst, msg_id) if it existed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT dst, msg_id FROM outbox WHERE packet_id = ?", (packet_id,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM outbox WHERE packet_id = ?", (packet_id,))
        return row[0], (bytes(row[1]) if row[1] else None)

    def outbox_load(self) -> list[OutboxEntry]:
        return [
            OutboxEntry(
                bytes(r[0]),
                r[1],
                bytes(r[2]),
                r[3],
                bytes(r[4]) if r[4] else None,
                r[5],
            )
            for r in self._q(
                "SELECT packet_id, dst, body, ts, msg_id, attempts FROM outbox "
                "ORDER BY created_at"
            )
        ]

    def outbox_drop(self, packet_id: bytes) -> None:
        self._x("DELETE FROM outbox WHERE packet_id = ?", (packet_id,))

    # --- dedup ------------------------------------------------------------------

    def claim_seen(self, packet_id: bytes) -> bool:
        return (
            self._x(
                "INSERT OR IGNORE INTO seen (packet_id, ts) VALUES (?, ?)",
                (packet_id, int(time.time())),
            )
            > 0
        )

    # --- store-and-forward (relay) ------------------------------------------------

    def relay_put(self, packet_id: bytes, dst: str, frame: bytes) -> None:
        self._x(
            "INSERT OR IGNORE INTO relay_store (packet_id, dst, frame, created_at) "
            "VALUES (?, ?, ?, ?)",
            (packet_id, dst, frame, int(time.time())),
        )

    def relay_take(self, dst: str) -> list[bytes]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT frame FROM relay_store WHERE dst = ? ORDER BY created_at",
                (dst,),
            ).fetchall()
            self._conn.execute("DELETE FROM relay_store WHERE dst = ?", (dst,))
        return [bytes(r[0]) for r in rows]

    def relay_dsts(self) -> list[str]:
        return [r[0] for r in self._q("SELECT DISTINCT dst FROM relay_store")]

    def relay_expire(self, max_age: int, max_rows: int) -> None:
        self._x(
            "DELETE FROM relay_store WHERE created_at < ?",
            (int(time.time()) - max_age,),
        )
        self._x(
            "DELETE FROM relay_store WHERE packet_id NOT IN (SELECT packet_id FROM "
            "relay_store ORDER BY created_at DESC LIMIT ?)",
            (max_rows,),
        )

    # --- files ----------------------------------------------------------------

    def save_file(self, f: FileRecord) -> None:
        self._x(
            "INSERT INTO files (file_id, msg_id, conv, sender, name, mime, size, "
            "sha256, chunk_size, chunks, path, state, have) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET "
            "path = excluded.path, state = excluded.state, have = excluded.have",
            (
                f.file_id,
                f.msg_id,
                f.conv,
                f.sender,
                f.name,
                f.mime,
                f.size,
                f.sha256,
                f.chunk_size,
                f.chunks,
                f.path,
                f.state,
                bytes(f.have),
            ),
        )

    def load_files(self) -> list[FileRecord]:
        return [
            FileRecord(
                bytes(r[0]),
                bytes(r[1]),
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                r[7],
                r[8],
                r[9],
                r[10],
                r[11],
                bytearray(r[12] or b""),
            )
            for r in self._q(
                "SELECT file_id, msg_id, conv, sender, name, mime, size, sha256, "
                "chunk_size, chunks, path, state, have FROM files"
            )
        ]

    # --- apps / games ---------------------------------------------------------

    def save_app_event(self, ev: "AppEvent") -> bool:
        return (
            self._x(
                "INSERT OR IGNORE INTO app_events (event_id, session, app, conv, "
                "sender, seq, type, body, ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ev.event_id,
                    ev.session,
                    ev.app,
                    ev.conv,
                    ev.sender,
                    ev.seq,
                    ev.type,
                    json.dumps(ev.body),
                    ev.ts,
                ),
            )
            > 0
        )

    def load_app_events(self, session: str) -> list["AppEvent"]:
        return [
            AppEvent(
                bytes(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]), r[8]
            )
            for r in self._q(
                "SELECT event_id, session, app, conv, sender, seq, type, body, ts "
                "FROM app_events WHERE session = ? ORDER BY seq, ts, rowid",
                (session,),
            )
        ]

    def list_app_sessions(
        self, conv: str | None = None
    ) -> list[tuple[str, str, str, int]]:
        """[(session, app, conv, last_ts)] newest first."""
        sql = (
            "SELECT session, app, conv, MAX(ts) FROM app_events {} "
            "GROUP BY session ORDER BY MAX(ts) DESC"
        )
        if conv is None:
            return [tuple(r) for r in self._q(sql.format(""))]
        return [tuple(r) for r in self._q(sql.format("WHERE conv = ?"), (conv,))]


_MSG_SELECT = (
    "SELECT msg_id, conv, sender, ts, kind, text, reply_to, meta, edited_at, "
    "deleted FROM messages"
)

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS identity (
    id INTEGER PRIMARY KEY CHECK (id = 0),
    privkey BLOB NOT NULL,
    node_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    name_ts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS peers (
    node_id TEXT PRIMARY KEY,
    pubkey BLOB NOT NULL,
    name TEXT,
    name_ts INTEGER NOT NULL DEFAULT 0,
    override TEXT,
    first_seen INTEGER,
    last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS transports (
    node_id TEXT NOT NULL,
    addr TEXT NOT NULL,
    last_ok INTEGER NOT NULL,
    PRIMARY KEY (node_id, addr)
);
CREATE TABLE IF NOT EXISTS groups (
    group_id BLOB PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    left INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id BLOB NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    PRIMARY KEY (group_id, node_id)
);
CREATE TABLE IF NOT EXISTS messages (
    msg_id BLOB PRIMARY KEY,
    conv TEXT NOT NULL,
    sender TEXT NOT NULL,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    reply_to BLOB,
    meta TEXT,
    edited_at INTEGER,
    deleted INTEGER NOT NULL DEFAULT 0,
    read_local INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv, ts);
CREATE TABLE IF NOT EXISTS recipients (
    msg_id BLOB NOT NULL REFERENCES messages(msg_id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    delivered_at INTEGER,
    read_at INTEGER,
    PRIMARY KEY (msg_id, node_id)
);
CREATE TABLE IF NOT EXISTS reactions (
    msg_id BLOB NOT NULL,
    node_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (msg_id, node_id, emoji)
);
CREATE TABLE IF NOT EXISTS outbox (
    packet_id BLOB PRIMARY KEY,
    dst TEXT NOT NULL,
    body BLOB NOT NULL,
    ts INTEGER NOT NULL,
    msg_id BLOB,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    packet_id BLOB PRIMARY KEY,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS relay_store (
    packet_id BLOB PRIMARY KEY,
    dst TEXT NOT NULL,
    frame BLOB NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    file_id BLOB PRIMARY KEY,
    msg_id BLOB NOT NULL,
    conv TEXT NOT NULL,
    sender TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    chunk_size INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    path TEXT NOT NULL,
    state TEXT NOT NULL,
    have BLOB
);
CREATE TABLE IF NOT EXISTS app_events (
    event_id BLOB PRIMARY KEY,
    session TEXT NOT NULL,
    app TEXT NOT NULL,
    conv TEXT NOT NULL,
    sender TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    body TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_events_session ON app_events(session, seq);
PRAGMA user_version = 2;
"""
