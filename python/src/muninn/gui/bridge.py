"""ChatBridge — Qt/QML ↔ core glue layer.

All ConnectionManager callbacks fire from BT threads. We re-emit them as Qt
signals; because the GUI thread is the consumer, Qt's cross-thread signal
delivery (QueuedConnection) handles the hop safely.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, Signal, Slot

from muninn.peers import GROUP_ZERO
from muninn.protocol import FrameTooLarge

from .models import ACK_ACKED, ACK_READ, MessageListModel, PeerListModel

if TYPE_CHECKING:
    from muninn.groups import Group, GroupStore
    from muninn.peers import ConnectionManager
    from muninn.storage import Storage


class ChatBridge(QObject):
    # --- signals (emitted from BT threads, delivered on GUI thread) ---

    messageReceived = Signal(str, str, str, str, int)
    # conv_id, sender_mac, text, msg_id_hex, timestamp

    peerChanged = Signal(str, bool)  # addr, connected
    ackReceived = Signal(str, str)  # msg_id_hex, from_mac
    readReceived = Signal(str, str)  # msg_id_hex, from_mac
    profileUpdated = Signal(str, str)  # addr, name
    groupAdded = Signal(str, str)  # group_id_hex, name

    activeConvChanged = Signal(str)  # new conv_id
    scanResultsReady = Signal(list)  # [{mac, name}, ...]
    errorOccurred = Signal(str)  # human-readable error

    _isWriterChanged = Signal(bool)
    _connCountChanged = Signal(int)

    def __init__(
        self,
        conn_mgr: ConnectionManager,
        group_store: GroupStore,
        storage: Storage,
        local_mac: str,
        is_writer: bool,
        peer_model: PeerListModel,
        msg_model: MessageListModel,
        parent=None,
    ):
        super().__init__(parent)
        self._cm = conn_mgr
        self._gs = group_store
        self._storage = storage
        self._local_mac = local_mac
        self._is_writer = is_writer
        self._peer_model = peer_model
        self._msg_model = msg_model
        self._active_conv_id: str = ""
        self._conn_count = 0

        # Initial peer list population
        peer_model.refresh()

        # Wire ConnectionManager callbacks
        conn_mgr.on_message = self._cb_message
        conn_mgr.on_peer_change = self._cb_peer_change
        conn_mgr.on_ack = self._cb_ack
        conn_mgr.on_read = self._cb_read
        conn_mgr.on_profile = self._cb_profile
        conn_mgr.on_group_setup = self._cb_group_setup

        # Connect own signals to model update slots
        self.messageReceived.connect(self._on_message_received)
        self.peerChanged.connect(self._on_peer_changed)
        self.ackReceived.connect(self._on_ack_received)
        self.readReceived.connect(self._on_read_received)
        self.profileUpdated.connect(self._on_profile_updated)
        self.groupAdded.connect(self._on_group_added)

    # ------------------------------------------------------------------
    # ConnectionManager callbacks (called from BT threads)
    # ------------------------------------------------------------------

    def _cb_message(
        self, group_id: bytes, sender_mac: str, text: str, msg_id: bytes
    ) -> None:
        conv_id = (
            "dm:" + sender_mac if group_id == GROUP_ZERO else "group:" + group_id.hex()
        )
        self.messageReceived.emit(
            conv_id, sender_mac, text, msg_id.hex(), int(time.time())
        )

    def _cb_peer_change(self, addr: str, connected: bool) -> None:
        self.peerChanged.emit(addr, connected)

    def _cb_ack(self, msg_id: bytes, from_mac: str) -> None:
        self.ackReceived.emit(msg_id.hex(), from_mac)

    def _cb_read(self, msg_id: bytes, from_mac: str) -> None:
        self.readReceived.emit(msg_id.hex(), from_mac)

    def _cb_profile(self, addr: str, name: str) -> None:
        self.profileUpdated.emit(addr, name)

    def _cb_group_setup(self, group: Group) -> None:
        self.groupAdded.emit(group.group_id.hex(), group.name)

    # ------------------------------------------------------------------
    # GUI-thread model update slots
    # ------------------------------------------------------------------

    def _on_message_received(
        self, conv_id: str, sender_mac: str, text: str, msg_id: str, ts: int
    ) -> None:
        self._peer_model.set_last_message(conv_id, text, ts)
        if conv_id == self._active_conv_id:
            self._msg_model.add_message(
                msg_id,
                sender_mac,
                self._gs.display_name(sender_mac),
                text,
                ts,
                is_outbound=False,
            )
        else:
            self._peer_model.increment_unread(conv_id)

    def _on_peer_changed(self, addr: str, connected: bool) -> None:
        self._peer_model.refresh()
        with self._cm.peers_lock:
            self._conn_count = len(self._cm.peers)
        self._connCountChanged.emit(self._conn_count)

    def _on_ack_received(self, msg_id: str, from_mac: str) -> None:
        self._msg_model.update_ack(msg_id, ACK_ACKED)

    def _on_read_received(self, msg_id: str, from_mac: str) -> None:
        self._msg_model.update_ack(msg_id, ACK_READ)

    def _on_profile_updated(self, addr: str, name: str) -> None:
        self._peer_model.refresh()

    def _on_group_added(self, group_id: str, name: str) -> None:
        self._peer_model.refresh()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @Property(str, constant=True)
    def localMac(self) -> str:
        return self._local_mac

    @Property(str, constant=True)
    def localName(self) -> str:
        return self._gs.display_name(self._local_mac)

    @Property(bool, constant=True)
    def isWriter(self) -> bool:
        return self._is_writer

    @Property(int, notify=_connCountChanged)
    def connectedPeerCount(self) -> int:
        return self._conn_count

    @Property(str, notify=activeConvChanged)
    def activeConvId(self) -> str:
        return self._active_conv_id

    # ------------------------------------------------------------------
    # Slots (callable from QML)
    # ------------------------------------------------------------------

    @Slot(str)
    def setActiveConv(self, conv_id: str) -> None:
        if conv_id == self._active_conv_id:
            return
        self._active_conv_id = conv_id
        self._peer_model.clear_unread(conv_id)
        self._load_history(conv_id)
        self.activeConvChanged.emit(conv_id)

    @Slot(str, str)
    def sendMessage(self, conv_id: str, text: str) -> None:
        if not self._is_writer or not text.strip():
            return
        text = text.strip()
        if conv_id.startswith("dm:"):
            addr = conv_id[3:]
            dests = [addr]
            gid = GROUP_ZERO
        elif conv_id.startswith("group:"):
            gid = bytes.fromhex(conv_id[6:])
            group = self._gs.groups.get(gid)
            if not group:
                return
            dests = [a for a in group.members if a != self._local_mac]
            if not dests:
                return
        else:
            return

        try:
            result = self._cm.send_message(gid, text, dests)
        except FrameTooLarge as e:
            self.errorOccurred.emit(f"Message too large: {e}")
            return
        except Exception as e:
            self.errorOccurred.emit(str(e))
            return

        msg_id, sent, skipped = result
        if not sent:
            self.errorOccurred.emit("No reachable recipient (no pubkey)")
        elif skipped:
            self.errorOccurred.emit(f"Skipped {len(skipped)} recipients (no pubkey)")

        ts = int(time.time())
        self._msg_model.add_message(
            msg_id.hex(),
            self._local_mac,
            self._gs.display_name(self._local_mac),
            text,
            ts,
            is_outbound=True,
        )
        self._peer_model.set_last_message(conv_id, text, ts)

    @Slot(str, result=str)
    def displayName(self, addr: str) -> str:
        return self._gs.display_name(addr)

    @Slot(str)
    def setDisplayName(self, name: str) -> None:
        if self._is_writer:
            self._cm.set_display_name(name)

    @Slot(str, str)
    def setOverride(self, addr: str, name: str) -> None:
        if name:
            self._gs.set_override(addr, name)
        else:
            self._gs.clear_override(addr)
        self._peer_model.refresh()

    @Slot()
    def startScan(self) -> None:
        threading.Thread(target=self._do_scan, daemon=True).start()

    @Slot(str)
    def pairDevice(self, mac: str) -> None:
        def _pair():
            try:
                from muninn import bt

                bt.ensure_paired(mac)
            except Exception as e:
                self.errorOccurred.emit(f"Pairing failed: {e}")

        threading.Thread(target=_pair, daemon=True).start()

    @Slot(str, str, list)
    def createGroup(self, name: str, _unused: str, addrs: list) -> None:
        if not self._is_writer:
            return
        try:
            self._cm.create_group(name, list(addrs))
        except ValueError as e:
            self.errorOccurred.emit(str(e))

    @Slot(str, result=str)
    def resolveAddr(self, name_or_mac: str) -> str:
        r = self._gs.resolve(name_or_mac)
        return r or ""

    @Slot(result=list)
    def knownPeers(self) -> list:
        result = []
        with self._cm.peers_lock:
            direct = set(self._cm.peers.keys())
        for addr in self._gs.pubkeys:
            if addr == self._local_mac:
                continue
            result.append(
                {
                    "mac": addr,
                    "name": self._gs.display_name(addr),
                    "status": "direct"
                    if addr in direct
                    else ("relay" if addr in self._cm.indirect_via else "offline"),
                }
            )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_scan(self) -> None:
        try:
            from muninn import bt

            bt.scan_devices(duration=5, quiet=True)
            results = bt.discover()
            self.scanResultsReady.emit([{"mac": r[0], "name": r[1]} for r in results])
        except Exception as e:
            self.errorOccurred.emit(f"Scan error: {e}")
            self.scanResultsReady.emit([])

    def _load_history(self, conv_id: str) -> None:
        if self._storage is None:
            self._msg_model.clear()
            return
        if conv_id.startswith("dm:"):
            addr = conv_id[3:]
            rows = self._storage.load_dm_history(self._local_mac, addr, 50)
        elif conv_id.startswith("group:"):
            gid = bytes.fromhex(conv_id[6:])
            rows = self._storage.load_group_history(gid, 50)
        else:
            self._msg_model.clear()
            return
        self._msg_model.load_history(rows, self._local_mac, self._gs.display_name)
