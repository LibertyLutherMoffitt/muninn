"""Qt list models for the GUI: PeerListModel and MessageListModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

import datetime as _dt

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from muninn import presence

if TYPE_CHECKING:
    from muninn.groups import GroupStore
    from muninn.peers import ConnectionManager
    from muninn.storage import Storage


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
    _R.BASE + 9: b"presenceText",
}
_PEER_ROLE_BY_NAME = {v: k for k, v in _PEER_ROLES.items()}


class PeerListModel(QAbstractListModel):
    def __init__(
        self,
        group_store: GroupStore,
        conn_mgr: ConnectionManager,
        storage: Storage | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._gs = group_store
        self._cm = conn_mgr
        self._storage = storage
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

        # Pull historical previews so chats show their last message even
        # before any new traffic this session.
        dm_last: dict[str, tuple[str, int]] = {}
        grp_last: dict[bytes, tuple[str, int]] = {}
        if self._storage is not None:
            try:
                dm_last = self._storage.last_message_per_dm(self._cm.local_mac)
                grp_last = self._storage.last_message_per_group()
            except Exception:
                dm_last = {}
                grp_last = {}

        # Presence is the source of truth for the row's dot and subtitle: it
        # knows the difference between "gone" and "the radio can see it but no
        # session will form", which peers_lock alone cannot tell us.
        tracker = self._cm.presence
        tracker.sync_from_manager(self._cm)
        statuses = tracker.all_statuses()

        # Devices seen in a scan that we hold no key for still belong in the
        # list — that is a peer who has not been paired yet, not a non-event.
        known = [a for a in self._gs.pubkeys if a != self._cm.local_mac]
        # Only devices that have actually advertised Muninn. The scanner
        # probes every radio it can hear, so without this filter the sidebar
        # fills with other passengers' headsets.
        seen_only = [
            a
            for a, st in statuses.items()
            if a not in self._gs.pubkeys
            and st.advertises_muninn
            and st.state != presence.OFFLINE
        ]

        for addr in known + seen_only:
            conv_id = "dm:" + addr
            status_obj = statuses.get(addr) or tracker.status(addr)
            if addr in direct:
                status = "direct"
            elif status_obj.state == presence.RELAY or addr in self._cm.indirect_via:
                status = "relay"
            elif status_obj.unreachable_nearby:
                status = "unreachable"
            elif status_obj.state == presence.NEARBY:
                status = "nearby"
            else:
                status = "offline"
            preview, last_ts = dm_last.get(addr, ("", 0))
            items.append(
                {
                    "mac": addr,
                    "displayName": self._gs.display_name(addr),
                    "convId": conv_id,
                    "convType": "dm",
                    "lastMessage": preview,
                    "lastTs": last_ts,
                    "unreadCount": self._unread.get(conv_id, 0),
                    "status": status,
                    "via": self._cm.indirect_via.get(addr, ""),
                    "presenceText": status_obj.describe(),
                }
            )

        for gid, group in self._gs.groups.items():
            conv_id = "group:" + gid.hex()
            preview, last_ts = grp_last.get(gid, ("", 0))
            items.append(
                {
                    "mac": gid.hex(),
                    "displayName": group.name,
                    "convId": conv_id,
                    "convType": "group",
                    "lastMessage": preview,
                    "lastTs": last_ts,
                    "unreadCount": self._unread.get(conv_id, 0),
                    "status": "group",
                    "via": "",
                    "presenceText": f"{len(group.members)} members",
                }
            )

        # Last-activity-first sort (peer rows already bubble on send/recv via
        # set_last_message; this puts historical state in the same order on
        # startup).
        items.sort(key=lambda it: it["lastTs"], reverse=True)
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
    _R.BASE + 7: b"daySection",
    _R.BASE + 8: b"showSender",
}
_MSG_ROLE_BY_NAME = {v: k for k, v in _MSG_ROLES.items()}

ACK_SENT = "sent"
ACK_ACKED = "acked"
ACK_READ = "read"

# Messages closer together than this from the same sender are drawn as one
# run: the name is shown once, at the top.
RUN_GAP_SECONDS = 300


def _day_section(ts: int) -> str:
    """Label for the day divider: Today, Yesterday, or an explicit date."""
    day = _dt.date.fromtimestamp(ts)
    today = _dt.date.today()
    delta = (today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return day.strftime("%A")
    if day.year == today.year:
        return day.strftime("%a %d %b")
    return day.strftime("%d %b %Y")


def _starts_run(msg: dict, prev: dict | None) -> bool:
    """True when this message should carry a sender label.

    A run is broken by a different sender, a day boundary, or a long enough
    pause that the two no longer read as one burst.
    """
    if prev is None:
        return True
    if prev["senderMac"] != msg["senderMac"]:
        return True
    if prev["daySection"] != msg["daySection"]:
        return True
    return msg["timestamp"] - prev["timestamp"] > RUN_GAP_SECONDS


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
        entry = {
            "msgId": msg_id,
            "senderMac": sender_mac,
            "senderName": sender_name,
            "text": text,
            "timestamp": ts,
            "isOutbound": is_outbound,
            "ackState": ACK_SENT,
            "daySection": _day_section(ts),
        }
        prev = self._messages[-1] if self._messages else None
        entry["showSender"] = _starts_run(entry, prev)
        self.beginInsertRows(QModelIndex(), row, row)
        self._messages.append(entry)
        self.endInsertRows()

    def update_ack(self, msg_id: str, state: str) -> None:
        for i, msg in enumerate(self._messages):
            if msg["msgId"] == msg_id:
                msg["ackState"] = state
                idx = self.index(i)
                self.dataChanged.emit(idx, idx, [_MSG_ROLE_BY_NAME[b"ackState"]])
                break

    def load_history(
        self,
        rows: list[tuple[bytes, str, str, int, str]],
        local_mac: str,
        name_fn,
    ) -> None:
        """Replace messages with stored history.

        Each row is `(msg_id, sender, body, ts, ack_state)` — `ack_state`
        comes from `Storage`, which derives it from per-recipient
        `acked_at` / `read_at` timestamps. Reusing the canonical state
        avoids the old bug where every reload flipped unacked outbound
        messages to "read".
        """
        self.beginResetModel()
        self._messages = [
            {
                "msgId": mid.hex(),
                "senderMac": sender,
                "senderName": name_fn(sender),
                "text": body,
                "timestamp": ts,
                "isOutbound": sender == local_mac,
                "ackState": ack,
                "daySection": _day_section(ts),
            }
            for mid, sender, body, ts, ack in rows
        ]
        prev = None
        for msg in self._messages:
            msg["showSender"] = _starts_run(msg, prev)
            prev = msg
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._messages = []
        self.endResetModel()
