"""Export / import chat history ("muninn-backup" zip, same format on Android).

    manifest.json   {format: "muninn-backup", version: 1, created, from, name,
                     identity: null | {kdf: "pbkdf2-sha256", iters, salt, box}}
    tables.json     {table: [ {column: value} ]}   blobs as hex strings
    files/<file id hex>/<name>                      complete attachments
    files/<file id hex>/thumb.jpg                   picture previews

Device-specific state is left out (radio addresses, outbox, relay store,
dedup, settings). The identity key is only included when a password is
given — it is sealed with XSalsa20-Poly1305 under a PBKDF2-SHA256 key — and
importing it is only allowed into a fresh install (taking over who you are
on a new phone). Everything else merges: rows already present are skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

FORMAT = "muninn-backup"
VERSION = 1
ITERS = 200_000

# table -> (columns, blob columns). Column names as in python storage.py; the
# Android app maps groups.left <-> left_group.
TABLES: dict[str, tuple[list[str], set[str]]] = {
    "peers": (
        [
            "node_id",
            "pubkey",
            "name",
            "name_ts",
            "override",
            "first_seen",
            "last_seen",
            "replaced_by",
        ],
        {"pubkey"},
    ),
    "groups": (["group_id", "name", "version", "left", "updated_at"], {"group_id"}),
    "group_members": (["group_id", "node_id"], {"group_id"}),
    "messages": (
        [
            "msg_id",
            "conv",
            "sender",
            "ts",
            "kind",
            "text",
            "reply_to",
            "meta",
            "edited_at",
            "deleted",
            "read_local",
        ],
        {"msg_id", "reply_to"},
    ),
    "recipients": (["msg_id", "node_id", "delivered_at", "read_at"], {"msg_id"}),
    "reactions": (["msg_id", "node_id", "emoji", "ts"], {"msg_id"}),
    "files": (
        [
            "file_id",
            "msg_id",
            "conv",
            "sender",
            "name",
            "mime",
            "size",
            "sha256",
            "chunk_size",
            "chunks",
            "path",
            "state",
            "have",
        ],
        {"file_id", "msg_id", "have"},
    ),
    "app_events": (
        ["event_id", "session", "app", "conv", "sender", "seq", "type", "body", "ts"],
        {"event_id"},
    ),
}


def _key(password: str, salt: bytes, iters: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters, 32)


def _seal(data: bytes, password: str) -> dict:
    from nacl.secret import SecretBox

    salt = os.urandom(16)
    box = SecretBox(_key(password, salt, ITERS)).encrypt(data)
    return {
        "kdf": "pbkdf2-sha256",
        "iters": ITERS,
        "salt": salt.hex(),
        "box": bytes(box).hex(),
    }


def _open(sealed: dict, password: str) -> bytes:
    from nacl.exceptions import CryptoError
    from nacl.secret import SecretBox

    key = _key(password, bytes.fromhex(sealed["salt"]), int(sealed["iters"]))
    try:
        return SecretBox(key).decrypt(bytes.fromhex(sealed["box"]))
    except CryptoError as e:
        raise ValueError("wrong password (or a damaged backup)") from e


def export(
    storage, path: str | Path, password: str | None = None, max_files: int | None = None
) -> dict:
    """Write a backup zip. Returns counts per table. `max_files` caps the
    attachment bytes carried (the rest can be fetched again later)."""
    ident = storage.get_identity()
    tables = {t: storage.export_rows(t, cols) for t, (cols, _) in TABLES.items()}
    tables["files"] = [  # history syncs between linked devices aren't history
        r for r in tables["files"] if not str(r.get("conv", "")).startswith("sync:")
    ]
    counts = {t: len(rows) for t, rows in tables.items()}
    for t, (_, blobs) in TABLES.items():
        for row in tables[t]:
            for c in blobs:
                if isinstance(row.get(c), (bytes, bytearray, memoryview)):
                    row[c] = bytes(row[c]).hex()
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "created": int(time.time()),
        "from": ident.node_id if ident else "",
        "name": ident.display_name if ident else "",
        "identity": None,
    }
    if password and ident:
        manifest["identity"] = _seal(
            json.dumps(
                {
                    "privkey": bytes(ident.privkey).hex(),
                    "node_id": ident.node_id,
                    "display_name": ident.display_name,
                    "name_ts": ident.name_ts,
                }
            ).encode(),
            password,
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        files = tables["files"]
        budget = max_files if max_files is not None else float("inf")
        for row in files:
            p = Path(row.get("path") or "")
            if row.get("state") == "complete" and p.is_file():
                size = p.stat().st_size
                if size <= budget:
                    budget -= size
                    z.write(p, f"files/{row['file_id']}/{p.name}", zipfile.ZIP_STORED)
            row["path"] = p.name
        for row in tables["messages"]:
            meta = json.loads(row.get("meta") or "{}")
            thumb = Path(meta.get("thumb", ""))
            if meta.get("thumb") and thumb.is_file() and meta.get("f"):
                z.write(thumb, f"files/{meta['f']}/thumb.jpg", zipfile.ZIP_STORED)
        z.writestr("tables.json", json.dumps(tables))
    return counts


def inspect(path: str | Path) -> dict:
    with zipfile.ZipFile(path) as z:
        m = json.loads(z.read("manifest.json"))
    if m.get("format") != FORMAT:
        raise ValueError("not a Muninn backup")
    return m


def import_(
    storage,
    path: str | Path,
    password: str | None = None,
    allow_from: set[str] | None = None,
    skip_system: bool = False,
) -> dict:
    """Merge a backup into this install. Returns {"added": {table: n},
    "identity": restored?} — when the identity was restored the app must be
    restarted (it is now that person). `allow_from`: other devices of mine
    whose history may merge in (linked devices); `skip_system` leaves out
    "X created the group" lines this device has its own copy of."""
    m = inspect(path)
    if int(m.get("version", 0)) > VERSION:
        raise ValueError("backup from a newer Muninn — update first")
    restored = False
    if m.get("identity") and password:
        data = json.loads(_open(m["identity"], password))
        if not storage.is_fresh():
            ident = storage.get_identity()
            if not ident or ident.node_id != data["node_id"]:
                raise ValueError(
                    "this install already has chats as someone else — your identity can "
                    "only be restored into a fresh install (import without the password "
                    "to just add the history)"
                )
        else:
            storage.replace_identity(
                bytes.fromhex(data["privkey"]),
                data["node_id"],
                data["display_name"],
                int(data["name_ts"]),
            )
            restored = True
    ident = storage.get_identity()
    if (
        not restored
        and ident
        and m.get("from")
        and m["from"] != ident.node_id
        and m["from"] not in (allow_from or set())
    ):
        raise ValueError(
            f"this is {m.get('name') or 'someone else'}'s backup — chats are stored from "
            "their point of view, so it can only go back into their own Muninn "
            "(or into a fresh install, with the password)"
        )
    added = {}
    with zipfile.ZipFile(path) as z:
        tables = json.loads(z.read("tables.json"))
        names = set(z.namelist())
        for row in tables.get("files", []):
            fid = row["file_id"]
            member = f"files/{fid}/{row.get('path', '')}"
            dest_dir = storage.files_dir / fid
            if member in names and row.get("path"):
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / Path(row["path"]).name
                dest.write_bytes(z.read(member))
                row["path"] = str(dest)
            else:
                row["path"] = str(dest_dir / Path(row.get("path") or "file").name)
                if row.get("state") == "complete":
                    row["state"] = "offered"  # can be downloaded again from a peer
                    row["have"] = ""
        for row in tables.get("messages", []):
            meta = json.loads(row.get("meta") or "{}")
            fid = meta.get("f")
            if fid and f"files/{fid}/thumb.jpg" in names:
                d = storage.files_dir / fid
                d.mkdir(parents=True, exist_ok=True)
                (d / "thumb.jpg").write_bytes(z.read(f"files/{fid}/thumb.jpg"))
                meta["thumb"] = str(d / "thumb.jpg")
                row["meta"] = json.dumps(meta)
        if skip_system:
            tables["messages"] = [
                r for r in tables.get("messages", []) if r.get("kind") != "system"
            ]
        for t, (cols, blobs) in TABLES.items():
            rows = tables.get(t, [])
            for row in rows:
                for c in blobs:
                    v = row.get(c)
                    row[c] = bytes.fromhex(v) if isinstance(v, str) else v
            added[t] = storage.import_rows(t, cols, rows)
    return {"added": added, "identity": restored}
