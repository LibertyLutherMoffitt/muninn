"""Qt list models: conversations (left pane) and messages (chat pane)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QUrl

from muninn.mesh import conv_group_id, dm_conv

if TYPE_CHECKING:
    from muninn.mesh import Mesh
    from muninn.storage import StoredMessage

BASE = Qt.UserRole + 1


def _roles(*names: str) -> dict[int, bytes]:
    return {BASE + i: n.encode() for i, n in enumerate(names)}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

_CONV_ROLES = _roles(
    "mac",
    "displayName",
    "convId",
    "convType",
    "lastMessage",
    "lastTs",
    "unreadCount",
    "status",
    "via",
    "hops",
    "typing",
    "pending",
)


class PeerListModel(QAbstractListModel):
    def __init__(self, mesh: Mesh, parent=None):
        super().__init__(parent)
        self.mesh = mesh
        self._items: list[dict] = []

    def roleNames(self):
        return _CONV_ROLES

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        return self._items[index.row()].get(_CONV_ROLES.get(role, b"").decode())

    def refresh(self) -> None:
        self.beginResetModel()
        self._items = self._build()
        self.endResetModel()

    def update_conv(self, conv: str) -> None:
        """Cheap path: rebuild everything (lists are tiny) but keep a reset-free
        update when order is unchanged."""
        new = self._build()
        if [i["convId"] for i in new] == [i["convId"] for i in self._items]:
            self._items = new
            if new:
                self.dataChanged.emit(self.index(0), self.index(len(new) - 1))
        else:
            self.beginResetModel()
            self._items = new
            self.endResetModel()

    def _build(self) -> list[dict]:
        m = self.mesh
        last = m.storage.last_messages()
        unread = m.storage.unread_counts()
        items = []

        def preview(conv):
            msg = last.get(conv)
            if msg is None:
                return "", 0
            if msg.deleted:
                return "(deleted)", msg.ts
            if msg.kind == "file":
                return "📎 " + (msg.text or msg.meta.get("n", "file")), msg.ts
            who = "you: " if msg.sender == m.node_id and msg.kind != "system" else ""
            return who + msg.text.replace("\n", " "), msg.ts

        peers = m.active_peers()
        with m.lock:
            groups = list(m.groups.values())
        for p in peers:
            conv = dm_conv(p.node_id)
            h = m.hops(p.node_id)
            status = "direct" if h == 1 else ("relay" if h else "offline")
            text, ts = preview(conv)
            if not ts and status == "offline" and not unread.get(conv):
                # Known-but-never-talked-to and not around: keep the list tidy.
                continue
            items.append(
                {
                    "mac": p.node_id,
                    "displayName": p.display,
                    "convId": conv,
                    "convType": "dm",
                    "lastMessage": text,
                    "lastTs": ts or p.last_seen,
                    "unreadCount": unread.get(conv, 0),
                    "status": status,
                    "via": m.next_hop(p.node_id) or "",
                    "hops": h or 0,
                    "typing": bool(m.typing_in(conv)),
                    "pending": m.pending_count(p.node_id),
                }
            )
        for g in groups:
            conv = g.conv
            text, ts = preview(conv)
            online = sum(1 for x in g.members if x != m.node_id and m.reachable(x))
            items.append(
                {
                    "mac": g.group_id.hex(),
                    "displayName": g.name + (" (left)" if g.left else ""),
                    "convId": conv,
                    "convType": "group",
                    "lastMessage": text,
                    "lastTs": ts,
                    "unreadCount": unread.get(conv, 0),
                    "status": "group",
                    "via": f"{online}/{len(g.members) - 1} here",
                    "hops": 0,
                    "typing": bool(m.typing_in(conv)),
                    "pending": 0,
                }
            )
        items.sort(key=lambda it: it["lastTs"], reverse=True)
        return items


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

_MSG_ROLES = _roles(
    "msgId",
    "senderMac",
    "senderName",
    "text",
    "timestamp",
    "isOutbound",
    "ackState",
    "kind",
    "replyName",
    "replyText",
    "replyId",
    "reactions",
    "edited",
    "deleted",
    "fileName",
    "fileSize",
    "fileState",
    "fileProgress",
    "fileUrl",
    "isImage",
    "isGif",
    "thumbUrl",
    "showSender",
    "dayLabel",
    "selected",
    "appInfo",
)
_R = {v.decode(): k for k, v in _MSG_ROLES.items()}


def _day(ts: int) -> str:
    t = time.localtime(ts)
    today = time.localtime()
    if t[:3] == today[:3]:
        return "Today"
    if time.localtime(time.time() - 86400)[:3] == t[:3]:
        return "Yesterday"
    return time.strftime("%a %d %b %Y", t)


def human_size(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


class MessageListModel(QAbstractListModel):
    def __init__(self, mesh: Mesh, parent=None):
        super().__init__(parent)
        self.mesh = mesh
        self.conv = ""
        self._rows: list[dict] = []
        self.selected = ""

    def roleNames(self):
        return _MSG_ROLES

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        return self._rows[index.row()].get(_MSG_ROLES.get(role, b"").decode())

    # --- building -------------------------------------------------------------

    def _row(self, m: StoredMessage, prev: StoredMessage | None) -> dict:
        mesh = self.mesh
        me = mesh.node_id
        reply_name = reply_text = ""
        if m.reply_to:
            o = mesh.storage.get_message(m.reply_to)
            if o:
                reply_name = "You" if o.sender == me else mesh.display_name(o.sender)
                reply_text = (
                    "(deleted)"
                    if o.deleted
                    else (
                        o.text
                        or ("📎 " + o.meta.get("n", "file") if o.kind == "file" else "")
                    )
                )
            else:
                reply_text = "(message not available)"
        reactions = [
            {
                "emoji": e,
                "count": len(who),
                "mine": me in who,
                "who": ", ".join(mesh.display_name(w) for w in who),
            }
            for e, who in m.reactions.items()
        ]
        row = {
            "msgId": m.msg_id.hex(),
            "senderMac": m.sender,
            "senderName": mesh.display_name(m.sender),
            "text": m.text,
            "timestamp": m.ts,
            "isOutbound": m.sender == me,
            "ackState": m.state,
            "kind": m.kind,
            "replyName": reply_name,
            "replyText": reply_text,
            "replyId": m.reply_to.hex() if m.reply_to else "",
            "reactions": reactions,
            "edited": m.edited,
            "deleted": m.deleted,
            "fileName": "",
            "fileSize": "",
            "fileState": "",
            "fileProgress": 0.0,
            "fileUrl": "",
            "isImage": False,
            "isGif": False,
            "thumbUrl": "",
            "appInfo": "",
            "showSender": m.sender != me
            and conv_group_id(m.conv) is not None
            and (
                prev is None
                or prev.sender != m.sender
                or prev.kind == "system"
                or m.ts - prev.ts > 300
            ),
            "dayLabel": _day(m.ts)
            if prev is None or _day(prev.ts) != _day(m.ts)
            else "",
            "selected": m.msg_id.hex() == self.selected,
        }
        if m.kind == "file":
            fid = bytes.fromhex(m.meta.get("f", "00"))
            rec = mesh.files.get(fid)
            mime = m.meta.get("mt", "")
            have, total, state = mesh.file_progress(fid)
            row.update(
                fileName=m.meta.get("n", "file"),
                fileSize=human_size(int(m.meta.get("s", 0))),
                fileState=state,
                fileProgress=(have / total) if total else 0.0,
                fileUrl=QUrl.fromLocalFile(rec.path).toString()
                if rec and state == "complete"
                else "",
                isImage=mime.startswith("image/"),
                isGif=mime == "image/gif",
                thumbUrl=QUrl.fromLocalFile(m.meta["thumb"]).toString()
                if m.meta.get("thumb")
                else "",
            )
        return row

    def load(self, conv: str, limit: int = 200) -> None:
        self.conv = conv
        self.beginResetModel()
        msgs = self.mesh.storage.load_history(conv, limit) if conv else []
        self._msgs = msgs
        self._rows = []
        prev = None
        for m in msgs:
            self._rows.append(self._row(m, prev))
            prev = m
        self.endResetModel()

    def append(self, m: StoredMessage) -> None:
        if m.conv != self.conv:
            return
        if any(r["msgId"] == m.msg_id.hex() for r in self._rows[-50:]):
            return self.refresh_msg(m.msg_id)
        prev = self._msgs[-1] if self._msgs else None
        n = len(self._rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self._msgs.append(m)
        self._rows.append(self._row(m, prev))
        self.endInsertRows()

    def refresh_msg(self, msg_id: bytes) -> None:
        h = msg_id.hex()
        for i, r in enumerate(self._rows):
            if r["msgId"] == h or r["replyId"] == h:
                m = self.mesh.storage.get_message(bytes.fromhex(r["msgId"]))
                if m is None:
                    continue
                self._msgs[i] = m
                self._rows[i] = self._row(m, self._msgs[i - 1] if i else None)
                idx = self.index(i)
                self.dataChanged.emit(idx, idx)

    def refresh_file(self, msg_id: bytes) -> None:
        self.refresh_msg(msg_id)

    def select(self, msg_id: str) -> None:
        old = self.selected
        self.selected = msg_id
        for i, r in enumerate(self._rows):
            if r["msgId"] in (old, msg_id):
                r["selected"] = r["msgId"] == msg_id
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [_R["selected"]])

    def ids(self) -> list[str]:
        return [r["msgId"] for r in self._rows if r["kind"] != "system"]

    def row_of(self, msg_id: str) -> int:
        for i, r in enumerate(self._rows):
            if r["msgId"] == msg_id:
                return i
        return -1

    def get(self, msg_id: str) -> dict | None:
        i = self.row_of(msg_id)
        return self._rows[i] if i >= 0 else None

    def clear(self) -> None:
        self.load("")
