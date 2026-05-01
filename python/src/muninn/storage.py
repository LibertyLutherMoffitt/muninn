"""SQLite-backed persistence for Muninn.

One DB file per device, opened by any Muninn client (CLI, GUI, future TUI).
WAL mode allows concurrent readers while serializing writes via an internal
threading.Lock — safe across ConnectionManager's recv/scanner/main threads.

Schema version is tracked in PRAGMA user_version. Migrations are forward-only:
increment SCHEMA_VERSION and append to _migrate().
"""

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from muninn.groups import Group

SCHEMA_VERSION = 1

# Duplicated from peers.GROUP_ZERO. Importing would create a cycle, and this
# byte pattern is a protocol constant unlikely to churn.
_GROUP_ZERO = b"\x00" * 16


def _row_ack_state(
    sender: str,
    local_mac: str,
    acked_at: int | None,
    read_at: int | None,
) -> str:
    """Map (sender, local_mac, acked_at, read_at) to a UI ack-state label."""
    if sender != local_mac:
        return "read"  # inbound — local user is reading it now
    if read_at is not None:
        return "read"
    if acked_at is not None:
        return "acked"
    return "sent"


def default_db_path() -> Path:
    """XDG-compliant on Linux; APPDATA on Windows; $HOME/.muninn fallback."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "muninn" / "muninn.db"


@dataclass
class Identity:
    privkey: bytes  # 32 bytes
    display_name: str  # "" = unset
    created_at: int


@dataclass
class UnackedMessage:
    msg_id: bytes
    group_id: bytes
    body: str
    recipients: list[str]  # recipients still missing an ACK


class Storage:
    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = default_db_path()
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,  # we serialize with self._lock
            isolation_level=None,  # autocommit; explicit BEGIN/COMMIT when needed
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()

        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Schema ---

    def _migrate(self) -> None:
        with self._lock:
            (current,) = self._conn.execute("PRAGMA user_version").fetchone()
            if current >= SCHEMA_VERSION:
                return
            if current < 1:
                self._conn.executescript(_SCHEMA_V1)

    # --- Identity ---

    def get_identity(self) -> Identity | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT privkey, display_name, created_at FROM identity WHERE id = 0"
            ).fetchone()
        if row is None:
            return None
        return Identity(
            privkey=bytes(row[0]),
            display_name=row[1] or "",
            created_at=row[2],
        )

    def create_identity(self, privkey: bytes, display_name: str = "") -> Identity:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO identity (id, privkey, display_name, created_at) "
                "VALUES (0, ?, ?, ?)",
                (privkey, display_name or None, now),
            )
        return Identity(privkey=privkey, display_name=display_name, created_at=now)

    def set_display_name(self, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE identity SET display_name = ? WHERE id = 0",
                (name or None,),
            )

    # --- Peers ---

    def save_peer_pubkey(self, mac: str, pubkey: bytes) -> None:
        """Authoritative insert — from direct handshake. Overwrites."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO peers (mac, pubkey) VALUES (?, ?) "
                "ON CONFLICT(mac) DO UPDATE SET pubkey = excluded.pubkey",
                (mac, pubkey),
            )

    def save_peer_pubkey_if_missing(self, mac: str, pubkey: bytes) -> None:
        """Provisional insert — from a group_setup or peer_annc relay.
        Direct-handshake pubkeys always win (never overwritten)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO peers (mac, pubkey) VALUES (?, ?)",
                (mac, pubkey),
            )

    def set_peer_name(self, mac: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE peers SET self_chosen_name = ? WHERE mac = ?",
                (name or None, mac),
            )

    def set_peer_override(self, mac: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE peers SET local_override = ? WHERE mac = ?",
                (name, mac),
            )

    def clear_peer_override(self, mac: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE peers SET local_override = NULL WHERE mac = ?",
                (mac,),
            )

    def load_peers(self) -> list[tuple[str, bytes, str | None, str | None]]:
        """Return [(mac, pubkey, self_chosen_name, local_override), ...]."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT mac, pubkey, self_chosen_name, local_override FROM peers"
            ).fetchall()
        return [(r[0], bytes(r[1]), r[2], r[3]) for r in rows]

    # --- Groups ---

    def save_group(self, group: Group) -> None:
        """Insert group + members atomically. No-op if group_id already present."""
        now = int(time.time())
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO groups (group_id, name, created_at) "
                    "VALUES (?, ?, ?)",
                    (group.group_id, group.name, now),
                )
                if cursor.rowcount > 0:
                    self._conn.executemany(
                        "INSERT OR IGNORE INTO group_members (group_id, mac) "
                        "VALUES (?, ?)",
                        [(group.group_id, m) for m in group.members],
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_groups(self) -> list[Group]:
        """Return list of Group objects with members populated from peers table."""
        with self._lock:
            group_rows = self._conn.execute(
                "SELECT group_id, name FROM groups"
            ).fetchall()
            result = []
            for gid, name in group_rows:
                member_rows = self._conn.execute(
                    "SELECT gm.mac, p.pubkey FROM group_members gm "
                    "JOIN peers p ON gm.mac = p.mac WHERE gm.group_id = ?",
                    (gid,),
                ).fetchall()
                members = {m[0]: bytes(m[1]) for m in member_rows}
                result.append(Group(group_id=bytes(gid), members=members, name=name))
        return result

    # --- Messages & delivery state ---

    def save_outgoing_message(
        self,
        msg_id: bytes,
        group_id: bytes,
        sender: str,
        body: str,
        ts: int,
        recipients: list[str],
    ) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "INSERT INTO messages (msg_id, group_id, sender, body, ts) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (msg_id, group_id, sender, body, ts),
                )
                self._conn.executemany(
                    "INSERT INTO message_recipients (msg_id, recipient) VALUES (?, ?)",
                    [(msg_id, r) for r in recipients],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def claim_seen(self, msg_id: bytes) -> bool:
        """Atomic dedup claim. Returns True if this is the first claim."""
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO seen (msg_id) VALUES (?)", (msg_id,)
            )
            return cursor.rowcount > 0

    def release_seen(self, msg_id: bytes) -> None:
        """Release a previous claim — call when decrypt fails so a future
        retransmit (e.g. once sender's pubkey reaches us) won't be silently
        dropped by the dedup check."""
        with self._lock:
            self._conn.execute("DELETE FROM seen WHERE msg_id = ?", (msg_id,))

    def save_incoming_body(
        self,
        msg_id: bytes,
        group_id: bytes,
        sender: str,
        body: str,
        ts: int,
    ) -> None:
        """Persist the decrypted plaintext of an incoming message.
        Caller must have already called claim_seen() successfully."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(msg_id, group_id, sender, body, ts) VALUES (?, ?, ?, ?, ?)",
                (msg_id, group_id, sender, body, ts),
            )

    def mark_acked(self, msg_id: bytes, recipient: str) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE message_recipients SET acked_at = ? "
                "WHERE msg_id = ? AND recipient = ? AND acked_at IS NULL",
                (now, msg_id, recipient),
            )

    def mark_read(self, msg_id: bytes, recipient: str) -> None:
        # AND read_at IS NULL keeps the first read timestamp. In-memory
        # seen_reads short-circuits dup READ frames within a session, but
        # that set is lost on restart — without this guard, a re-received
        # READ would bump read_at to a much later time than the actual read.
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE message_recipients SET read_at = ? "
                "WHERE msg_id = ? AND recipient = ? AND read_at IS NULL",
                (now, msg_id, recipient),
            )

    def load_dm_history(
        self, local_mac: str, peer: str, limit: int
    ) -> list[tuple[bytes, str, str, int, str]]:
        """Recent DM history with `peer`, oldest-first.

        Each row is `(msg_id, sender, body, ts, ack_state)`. For outbound
        rows, `ack_state` reflects the per-recipient `acked_at` / `read_at`
        timestamps. Inbound rows are always `"read"` — the local user is
        looking at them right now.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.msg_id, m.sender, m.body, m.ts, "
                "       r.acked_at, r.read_at "
                "FROM messages m "
                "LEFT JOIN message_recipients r "
                "  ON r.msg_id = m.msg_id AND r.recipient = ? "
                "WHERE m.msg_id IN ("
                "  SELECT m2.msg_id FROM messages m2 "
                "  WHERE m2.group_id = ? AND ("
                "    m2.sender = ? OR "
                "    (m2.sender = ? AND m2.msg_id IN "
                "      (SELECT msg_id FROM message_recipients WHERE recipient = ?))"
                "  ) ORDER BY m2.ts DESC LIMIT ?"
                ") ORDER BY m.ts ASC",
                (peer, _GROUP_ZERO, peer, local_mac, peer, limit),
            ).fetchall()
        return [
            (
                bytes(r[0]),
                r[1],
                r[2],
                r[3],
                _row_ack_state(r[1], local_mac, r[4], r[5]),
            )
            for r in rows
        ]

    def load_group_history(
        self, group_id: bytes, local_mac: str, limit: int
    ) -> list[tuple[bytes, str, str, int, str]]:
        """Recent group history, oldest-first.

        Each row is `(msg_id, sender, body, ts, ack_state)`. For outbound
        group messages, `ack_state` is the worst across recipients: `"sent"`
        if any recipient hasn't acked, `"acked"` if all acked but not all
        read, `"read"` if every recipient has read.
        """
        out: list[tuple[bytes, str, str, int, str]] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT msg_id, sender, body, ts FROM ("
                "  SELECT msg_id, sender, body, ts FROM messages "
                "  WHERE group_id = ? ORDER BY ts DESC LIMIT ?"
                ") ORDER BY ts ASC",
                (group_id, limit),
            ).fetchall()
            for r in rows:
                msg_id = bytes(r[0])
                sender = r[1]
                if sender != local_mac:
                    state = "read"
                else:
                    state = self._aggregate_recipient_state(msg_id)
                out.append((msg_id, sender, r[2], r[3], state))
        return out

    def last_message_per_dm(self, local_mac: str) -> dict[str, tuple[str, int]]:
        """Most recent DM body+ts keyed by peer MAC.

        Used to populate the peer-list previews on startup so users see the
        last thing they exchanged with each peer without having to send or
        receive while the GUI is open.
        """
        with self._lock:
            rows = self._conn.execute(
                # Inbound DMs — peer = sender.
                "SELECT sender AS peer, body, ts "
                "FROM messages WHERE group_id = ? AND sender != ? "
                "UNION ALL "
                # Outbound DMs — peer = recipient (per message_recipients).
                "SELECT r.recipient AS peer, m.body, m.ts "
                "FROM messages m JOIN message_recipients r "
                "  ON r.msg_id = m.msg_id "
                "WHERE m.group_id = ? AND m.sender = ? "
                "ORDER BY ts DESC",
                (_GROUP_ZERO, local_mac, _GROUP_ZERO, local_mac),
            ).fetchall()
        out: dict[str, tuple[str, int]] = {}
        for peer, body, ts in rows:
            if peer not in out:
                out[peer] = (body, ts)
        return out

    def last_message_per_group(self) -> dict[bytes, tuple[str, int]]:
        """Most recent body+ts for each group_id (excluding the DM zero-id)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.group_id, m.body, m.ts FROM messages m "
                "WHERE m.group_id != ? AND m.ts = ("
                "  SELECT MAX(ts) FROM messages WHERE group_id = m.group_id"
                ")",
                (_GROUP_ZERO,),
            ).fetchall()
        return {bytes(gid): (body, ts) for gid, body, ts in rows}

    def _aggregate_recipient_state(self, msg_id: bytes) -> str:
        rows = self._conn.execute(
            "SELECT acked_at, read_at FROM message_recipients WHERE msg_id = ?",
            (msg_id,),
        ).fetchall()
        if not rows:
            return "sent"
        all_acked = True
        all_read = True
        for acked_at, read_at in rows:
            if acked_at is None:
                all_acked = False
                all_read = False
            if read_at is None:
                all_read = False
        if all_read:
            return "read"
        if all_acked:
            return "acked"
        return "sent"

    def load_unacked_outbound(self, local_mac: str) -> list[UnackedMessage]:
        """Messages we sent that still have recipients without an ACK."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.msg_id, m.group_id, m.body, r.recipient "
                "FROM messages m JOIN message_recipients r ON m.msg_id = r.msg_id "
                "WHERE m.sender = ? AND r.acked_at IS NULL "
                "ORDER BY m.ts",
                (local_mac,),
            ).fetchall()
        by_msg: dict[bytes, UnackedMessage] = {}
        for msg_id, group_id, body, recipient in rows:
            msg_id_b = bytes(msg_id)
            if msg_id_b not in by_msg:
                by_msg[msg_id_b] = UnackedMessage(
                    msg_id=msg_id_b,
                    group_id=bytes(group_id),
                    body=body,
                    recipients=[],
                )
            by_msg[msg_id_b].recipients.append(recipient)
        return list(by_msg.values())


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS identity (
    id           INTEGER PRIMARY KEY CHECK (id = 0),
    privkey      BLOB NOT NULL,
    display_name TEXT,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS peers (
    mac               TEXT PRIMARY KEY,
    pubkey            BLOB NOT NULL,
    self_chosen_name  TEXT,
    local_override    TEXT
);

CREATE TABLE IF NOT EXISTS groups (
    group_id   BLOB PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    group_id BLOB NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    mac      TEXT NOT NULL,
    PRIMARY KEY (group_id, mac)
);

CREATE TABLE IF NOT EXISTS messages (
    msg_id   BLOB PRIMARY KEY,
    group_id BLOB NOT NULL,
    sender   TEXT NOT NULL,
    body     TEXT NOT NULL,
    ts       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS message_recipients (
    msg_id    BLOB NOT NULL REFERENCES messages(msg_id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    acked_at  INTEGER,
    read_at   INTEGER,
    PRIMARY KEY (msg_id, recipient)
);

CREATE TABLE IF NOT EXISTS seen (
    msg_id BLOB PRIMARY KEY
);

CREATE INDEX IF NOT EXISTS idx_messages_group_ts ON messages(group_id, ts);
CREATE INDEX IF NOT EXISTS idx_recipients_unacked
    ON message_recipients(recipient) WHERE acked_at IS NULL;

PRAGMA user_version = 1;
"""
