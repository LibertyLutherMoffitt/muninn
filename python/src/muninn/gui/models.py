"""Qt list models for the GUI: PeerListModel and MessageListModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

if TYPE_CHECKING:
    from muninn.groups import GroupStore
    from muninn.peers import ConnectionManager


class _R:
    """Role constants shared between models."""

    BASE = Qt.UserRole + 1


# ---------------------------------------------------------------------------
# PeerListModel
# ---------------------------------------------------------------------------

_PEER_ROLES = {
    _R.BASE + 0: b"mac",
    _R.BASE + 1: b"displayName",
    _R.BASE + 2: b"convId",
    _R.BASE + 3: b"convType",
    _R.BASE + 4: b"lastMessage",
    _R.BASE + 5: b"lastTs",
    _R.BASE + 6: b"unreadCount",
    _R.BASE + 7: b"status",
    _R.BASE + 8: b"via",
}
_PEER_ROLE_BY_NAME = {v: k for k, v in _PEER_ROLES.items()}


class PeerListModel(QAbstractListModel):
    def __init__(
        self,
        group_store: GroupStore,
        conn_mgr: ConnectionManager,
        parent=None,
    ):
        super().__init__(parent)
        self._gs = group_store
        self._cm = conn_mgr
        self._items: list[dict] = []
        self._unread: dict[str, int] = {}

    def roleNames(self):
        return _PEER_ROLES

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None
        key = _PEER_ROLES.get(role, b"").decode()
        return self._items[index.row()].get(key)

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self.beginResetModel()
        self._items = self._build()
        self.endResetModel()

    def increment_unread(self, conv_id: str) -> None:
        self._unread[conv_id] = self._unread.get(conv_id, 0) + 1
        for i, item in enumerate(self._items):
            if item["convId"] == conv_id:
                item["unreadCount"] = self._unread[conv_id]
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [_PEER_ROLE_BY_NAME[b"unreadCount"]])
                break

    def clear_unread(self, conv_id: str) -> None:
        self._unread.pop(conv_id, None)
        for i, item in enumerate(self._items):
            if item["convId"] == conv_id:
                item["unreadCount"] = 0
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [_PEER_ROLE_BY_NAME[b"unreadCount"]])
                break

    def set_last_message(self, conv_id: str, text: str, ts: int) -> None:
        for i, item in enumerate(self._items):
            if item["convId"] == conv_id:
                item["lastMessage"] = text
                item["lastTs"] = ts
                idx = self.index(i)
                self.dataChanged.emit(
                    idx,
                    idx,
                    [
                        _PEER_ROLE_BY_NAME[b"lastMessage"],
                        _PEER_ROLE_BY_NAME[b"lastTs"],
                    ],
                )
                # Bubble to top (last-activity sort). Qt rejects a move where
                # destinationRow equals source or source+1 (no-op move) — skip
                # when item is already at row 0.
                if i > 0:
                    self.beginMoveRows(QModelIndex(), i, i, QModelIndex(), 0)
                    self._items.insert(0, self._items.pop(i))
                    self.endMoveRows()
                return

    def _build(self) -> list[dict]:
        items: list[dict] = []
        with self._cm.peers_lock:
            direct: set[str] = set(self._cm.peers.keys())

        for addr in self._gs.pubkeys:
            if addr == self._cm.local_mac:
                continue
            conv_id = "dm:" + addr
            if addr in direct:
                status = "direct"
            elif addr in self._cm.indirect_via:
                status = "relay"
            else:
                status = "offline"
            items.append(
                {
                    "mac": addr,
                    "displayName": self._gs.display_name(addr),
                    "convId": conv_id,
                    "convType": "dm",
                    "lastMessage": "",
                    "lastTs": 0,
                    "unreadCount": self._unread.get(conv_id, 0),
                    "status": status,
                    "via": self._cm.indirect_via.get(addr, ""),
                }
            )

        for gid, group in self._gs.groups.items():
            conv_id = "group:" + gid.hex()
            items.append(
                {
                    "mac": gid.hex(),
                    "displayName": group.name,
                    "convId": conv_id,
                    "convType": "group",
                    "lastMessage": "",
                    "lastTs": 0,
                    "unreadCount": self._unread.get(conv_id, 0),
                    "status": "group",
                    "via": "",
                }
            )

        return items


# ---------------------------------------------------------------------------
# MessageListModel
# ---------------------------------------------------------------------------

_MSG_ROLES = {
    _R.BASE + 0: b"msgId",
    _R.BASE + 1: b"senderMac",
    _R.BASE + 2: b"senderName",
    _R.BASE + 3: b"text",
    _R.BASE + 4: b"timestamp",
    _R.BASE + 5: b"isOutbound",
    _R.BASE + 6: b"ackState",
}
_MSG_ROLE_BY_NAME = {v: k for k, v in _MSG_ROLES.items()}

ACK_SENT = "sent"
ACK_ACKED = "acked"
ACK_READ = "read"


class MessageListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[dict] = []

    def roleNames(self):
        return _MSG_ROLES

    def rowCount(self, parent=QModelIndex()):
        return len(self._messages)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._messages):
            return None
        key = _MSG_ROLES.get(role, b"").decode()
        return self._messages[index.row()].get(key)

    # ------------------------------------------------------------------

    def add_message(
        self,
        msg_id: str,
        sender_mac: str,
        sender_name: str,
        text: str,
        ts: int,
        is_outbound: bool,
    ) -> None:
        row = len(self._messages)
        self.beginInsertRows(QModelIndex(), row, row)
        self._messages.append(
            {
                "msgId": msg_id,
                "senderMac": sender_mac,
                "senderName": sender_name,
                "text": text,
                "timestamp": ts,
                "isOutbound": is_outbound,
                "ackState": ACK_SENT,
            }
        )
        self.endInsertRows()

    def update_ack(self, msg_id: str, state: str) -> None:
        for i, msg in enumerate(self._messages):
            if msg["msgId"] == msg_id:
                msg["ackState"] = state
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [_MSG_ROLE_BY_NAME[b"ackState"]])
                break

    def load_history(
        self, rows: list[tuple[bytes, str, str, int]], local_mac: str, name_fn
    ) -> None:
        self.beginResetModel()
        self._messages = [
            {
                "msgId": mid.hex(),
                "senderMac": sender,
                "senderName": name_fn(sender),
                "text": body,
                "timestamp": ts,
                "isOutbound": sender == local_mac,
                "ackState": ACK_READ,
            }
            for mid, sender, body, ts in rows
        ]
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._messages = []
        self.endResetModel()
