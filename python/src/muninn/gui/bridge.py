"""ChatBridge — Qt/QML ↔ core glue layer.

All ConnectionManager callbacks fire from BT threads. We re-emit them as Qt
signals; because the GUI thread is the consumer, Qt's cross-thread signal
delivery (QueuedConnection) handles the hop safely.
"""

from __future__ import annotations

import shlex
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from muninn.peers import GROUP_ZERO
from muninn.protocol import FrameTooLarge

from . import notifications
from .models import ACK_ACKED, ACK_READ, MessageListModel, PeerListModel

if TYPE_CHECKING:
    from muninn.groups import Group, GroupStore
    from muninn.peers import ConnectionManager
    from muninn.storage import Storage


def _shquote(s: str) -> str:
    """Quote a token only if it contains whitespace."""
    if not s:
        return '""'
    if any(ch.isspace() for ch in s):
        return shlex.quote(s)
    return s


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
    notify = Signal(str)  # neutral info toast
    quitRequested = Signal()
    scanRequested = Signal()
    paletteRequested = Signal()
    # title, items: each item = {label, sub, convId?, action?}
    infoMenuRequested = Signal(str, list)

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
        is_active = conv_id == self._active_conv_id
        if is_active:
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

        # Desktop notification when the user can't see this message — either
        # they're on another conv or the Muninn window isn't focused. Read
        # receipts still fire only when the conv is opened, so this mirrors
        # the same "did the user actually see it" criterion.
        if not (is_active and self._window_focused()):
            sender = self._gs.display_name(sender_mac)
            if conv_id.startswith("group:"):
                gid = bytes.fromhex(conv_id[6:])
                grp = self._gs.groups.get(gid)
                title = f"{sender} · {grp.name}" if grp else sender
            else:
                title = sender
            notifications.notify(title, text)

    @staticmethod
    def _window_focused() -> bool:
        app = QGuiApplication.instance()
        if app is None:
            return False
        return app.focusWindow() is not None

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

    # ------------------------------------------------------------------
    # Tab completion (used by both cmdline and palette)
    # ------------------------------------------------------------------

    _COMMANDS = (
        "dm",
        "group",
        "new",
        "nick",
        "list",
        "peers",
        "known",
        "history",
        "scan",
        "clear",
        "help",
        "about",
        "next",
        "prev",
        "palette",
        "find",
        "q",
        "qa",
        "quit",
        "w",
        "wq",
        "x",
    )

    # Command help shown in :help info menu and palette.
    _HELP = (
        (":dm <peer>", "switch to a DM"),
        (":group <name>", "switch to a group"),
        (":new <name> <peer1> [peer2…]", "create a group"),
        (":nick <name>", "set your display name"),
        (":nick <peer> <name>", "local override for a peer"),
        (':nick <peer> ""', "clear a local override"),
        (":list", "list conversations"),
        (":peers", "show direct + relay peers"),
        (":known", "show every known peer"),
        (":history [N]", "reload last N messages"),
        (":scan", "open the bluetooth scan dialog"),
        (":clear", "clear the visible message buffer"),
        (":next  /  :prev", "cycle conversations (Ctrl-N / Ctrl-P)"),
        (":palette", "open the command palette (also <space>f)"),
        (":about", "show version + identity info"),
        (":w", "no-op"),
        (":q  /  :wq  /  :x", "quit (wq/x sends pending buffer first)"),
    )

    def _candidates_for(self, head: str, arg_index: int) -> list[str]:
        if head in ("dm", "nick") and arg_index == 1:
            return self._peer_candidates()
        if head == "group" and arg_index == 1:
            return [g.name for g in self._gs.groups.values()]
        if head == "new" and arg_index >= 2:
            return self._peer_candidates()
        return []

    def _peer_candidates(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for addr in self._gs.pubkeys:
            if addr == self._local_mac:
                continue
            n = self._gs.display_name(addr)
            if n and n not in seen:
                seen.add(n)
                names.append(n)
            if addr not in seen:
                seen.add(addr)
                names.append(addr)
        return names

    @staticmethod
    def _common_prefix(strings: list[str]) -> str:
        if not strings:
            return ""
        s = strings[0]
        for other in strings[1:]:
            i = 0
            while i < len(s) and i < len(other) and s[i] == other[i]:
                i += 1
            s = s[:i]
            if not s:
                break
        return s

    @Slot(str, result=str)
    def completeCommand(self, buf: str) -> str:
        """Tab-complete a command line. Returns the new buffer.

        - On the head: picks longest common prefix of matching commands.
        - On an arg: picks longest common prefix of matching candidates.
        - If buf already matches uniquely, appends a trailing space.
        """
        leading_colon = buf.startswith(":")
        body = buf[1:] if leading_colon else buf
        # Tokenize while preserving trailing-space awareness.
        ends_with_space = body.endswith(" ")
        try:
            tokens = shlex.split(body) if body.strip() else []
        except ValueError:
            return buf
        if not tokens:
            return buf

        if len(tokens) == 1 and not ends_with_space:
            head = tokens[0].lower()
            matches = [c for c in self._COMMANDS if c.startswith(head)]
            if not matches:
                return buf
            common = self._common_prefix(matches)
            if len(matches) == 1:
                completed = matches[0] + " "
            else:
                completed = common
            return (":" if leading_colon else "") + completed

        head = tokens[0].lower()
        if ends_with_space:
            arg_index = len(tokens)
            partial = ""
        else:
            arg_index = len(tokens) - 1
            partial = tokens[-1]
        cands = self._candidates_for(head, arg_index)
        if not cands:
            return buf
        low = partial.lower()
        matches = [c for c in cands if c.lower().startswith(low)]
        if not matches:
            return buf
        if len(matches) == 1:
            completed_arg = matches[0] + " "
        else:
            common_low = self._common_prefix([m.lower() for m in matches])
            if len(common_low) > len(low):
                # Take case from the first match for the matched prefix.
                completed_arg = matches[0][: len(common_low)]
            else:
                completed_arg = partial
        # Reassemble.
        prefix_tokens = tokens[:arg_index]
        rebuilt = " ".join(_shquote(t) for t in prefix_tokens)
        if rebuilt:
            rebuilt += " "
        if completed_arg:
            rebuilt += _shquote(completed_arg.rstrip(" "))
            if completed_arg.endswith(" "):
                rebuilt += " "
        return (":" if leading_colon else "") + rebuilt

    @Slot(str)
    def runCommand(self, cmd: str) -> None:
        """Execute a colon command (without leading ':'). Single dispatch path
        used by both vim cmdline (`:foo`) and the palette (`<space>f` raw mode).

        Action commands emit `notify` (toast) on success.
        Data commands emit `infoMenuRequested(title, items)` so QML can show
        them in a structured popup that can also drill into a conv.
        """
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            self.errorOccurred.emit(f"parse error: {e}")
            return
        if not parts:
            return
        head = parts[0].lower()
        args = parts[1:]

        # --- meta / lifecycle ---
        if head in ("q", "qa", "q!", "qa!", "quit", "wq", "x"):
            self.quitRequested.emit()
            return
        if head == "w":
            self.notify.emit("nothing to save")
            return
        if head == "scan":
            self.scanRequested.emit()
            return
        if head in ("palette", "pal", "find", "f"):
            self.paletteRequested.emit()
            return

        # --- conversation switching ---
        if head == "dm":
            if not args:
                self.errorOccurred.emit("usage: :dm <name|addr>")
                return
            resolved = self._gs.resolve(args[0])
            if resolved is None:
                self.errorOccurred.emit(f"unknown peer: {args[0]}")
                return
            self.setActiveConv("dm:" + resolved)
            return
        if head == "group":
            if not args:
                self.errorOccurred.emit("usage: :group <name>")
                return
            for gid, group in self._gs.groups.items():
                if group.name == args[0]:
                    self.setActiveConv("group:" + gid.hex())
                    return
            self.errorOccurred.emit(f"group not found: {args[0]}")
            return
        if head == "new":
            if not self._is_writer:
                self.errorOccurred.emit(
                    "read-only: another instance holds the writer lock"
                )
                return
            if len(args) < 2:
                self.errorOccurred.emit("usage: :new <name> <peer1> [peer2] ...")
                return
            name = args[0]
            addrs: list[str] = []
            for p in args[1:]:
                resolved = self._gs.resolve(p)
                if resolved is None:
                    self.errorOccurred.emit(f"unknown peer: {p}")
                    return
                addrs.append(resolved)
            try:
                group = self._cm.create_group(name, addrs)
                self.setActiveConv("group:" + group.group_id.hex())
                self.notify.emit(f"created group '{name}'")
            except ValueError as e:
                self.errorOccurred.emit(str(e))
            return
        if head == "nick":
            if len(args) == 1:
                if not self._is_writer:
                    self.errorOccurred.emit("read-only: cannot broadcast nick")
                    return
                self._cm.set_display_name(args[0])
                self.notify.emit(f"nick: {args[0] or '(cleared)'}")
            elif len(args) == 2:
                resolved = self._gs.resolve(args[0])
                if resolved is None:
                    self.errorOccurred.emit(f"unknown peer: {args[0]}")
                    return
                if args[1] == "":
                    self._gs.clear_override(resolved)
                    self.notify.emit(f"cleared override for {resolved}")
                else:
                    self._gs.set_override(resolved, args[1])
                    self.notify.emit(f"override: {resolved} -> {args[1]}")
                self._peer_model.refresh()
            else:
                self.errorOccurred.emit("usage: :nick <name>  |  :nick <peer> <name>")
            return
        if head in ("next", "bn"):
            self.cycleConv(1)
            return
        if head in ("prev", "bp"):
            self.cycleConv(-1)
            return
        if head in ("clear", "cls"):
            self._msg_model.clear()
            self.notify.emit("buffer cleared")
            return
        if head == "history":
            n = 50
            if args:
                try:
                    n = max(1, min(500, int(args[0])))
                except ValueError:
                    self.errorOccurred.emit("usage: :history [N]")
                    return
            self._reload_history(self._active_conv_id, n)
            self.notify.emit(f"loaded {n} messages")
            return

        # --- data commands: pop an info menu ---
        if head == "list":
            items = []
            for it in self._peer_model._items:
                active = it["convId"] == self._active_conv_id
                items.append(
                    {
                        "label": it["displayName"],
                        "sub": it["convType"] + (" · active" if active else ""),
                        "convId": it["convId"],
                        "action": "",
                    }
                )
            if not items:
                items = [
                    {
                        "label": "(no conversations)",
                        "sub": "",
                        "convId": "",
                        "action": "",
                    }
                ]
            self.infoMenuRequested.emit("conversations", items)
            return
        if head == "peers":
            with self._cm.peers_lock:
                direct = list(self._cm.peers.keys())
            direct_set = set(direct)
            relay = [
                a
                for a in self._gs.pubkeys
                if a != self._local_mac and a not in direct_set
            ]
            items = []
            for a in direct:
                items.append(
                    {
                        "label": self._gs.display_name(a),
                        "sub": f"direct · {a}",
                        "convId": "dm:" + a,
                        "action": "",
                    }
                )
            for a in relay:
                via = self._cm.indirect_via.get(a, "")
                via_str = self._gs.display_name(via) if via else "?"
                items.append(
                    {
                        "label": self._gs.display_name(a),
                        "sub": f"relay via {via_str} · {a}",
                        "convId": "dm:" + a,
                        "action": "",
                    }
                )
            if not items:
                items = [{"label": "(no peers)", "sub": "", "convId": "", "action": ""}]
            self.infoMenuRequested.emit("peers", items)
            return
        if head == "known":
            with self._cm.peers_lock:
                direct_set = set(self._cm.peers.keys())
            items = []
            for a in sorted(self._gs.pubkeys):
                if a == self._local_mac:
                    continue
                if a in direct_set:
                    status = "connected"
                elif a in self._cm.indirect_via:
                    status = (
                        f"relay via {self._gs.display_name(self._cm.indirect_via[a])}"
                    )
                else:
                    status = "offline"
                items.append(
                    {
                        "label": self._gs.display_name(a),
                        "sub": f"{status} · {a}",
                        "convId": "dm:" + a,
                        "action": "",
                    }
                )
            if not items:
                items = [
                    {"label": "(no known peers)", "sub": "", "convId": "", "action": ""}
                ]
            self.infoMenuRequested.emit("known peers", items)
            return
        if head in ("help", "h", "?"):
            items = [
                {"label": label, "sub": desc, "convId": "", "action": ""}
                for label, desc in self._HELP
            ]
            self.infoMenuRequested.emit("commands", items)
            return
        if head == "about":
            try:
                from importlib.metadata import version as _ver

                ver = _ver("muninn")
            except Exception:
                ver = "unknown"
            with self._cm.peers_lock:
                conn = len(self._cm.peers)
            items = [
                {
                    "label": "Muninn",
                    "sub": f"v{ver} — encrypted bluetooth chat",
                    "convId": "",
                    "action": "",
                },
                {
                    "label": "you",
                    "sub": f"{self._gs.display_name(self._local_mac)} · {self._local_mac}",
                    "convId": "",
                    "action": "",
                },
                {
                    "label": "mode",
                    "sub": "writer (sending enabled)"
                    if self._is_writer
                    else "reader (another instance holds the writer lock)",
                    "convId": "",
                    "action": "",
                },
                {
                    "label": "peers",
                    "sub": f"{conn} connected · {max(0, len(self._gs.pubkeys) - 1)} known",
                    "convId": "",
                    "action": "",
                },
                {
                    "label": "license",
                    "sub": "MIT — see LICENSE / THIRD_PARTY_LICENSES.md",
                    "convId": "",
                    "action": "",
                },
                {
                    "label": "Qt 6 / PySide6",
                    "sub": "LGPL-3.0 — © The Qt Company",
                    "convId": "",
                    "action": "url",
                    "url": "https://www.qt.io/licensing",
                },
                {
                    "label": "github",
                    "sub": "github.com/LibertyLutherMoffitt/muninn",
                    "convId": "",
                    "action": "url",
                    "url": "https://github.com/LibertyLutherMoffitt/muninn",
                },
            ]
            self.infoMenuRequested.emit("about", items)
            return

        self.errorOccurred.emit(f"unknown command: :{head}")

    def _reload_history(self, conv_id: str, n: int) -> None:
        if not conv_id or self._storage is None:
            return
        if conv_id.startswith("dm:"):
            rows = self._storage.load_dm_history(self._local_mac, conv_id[3:], n)
        elif conv_id.startswith("group:"):
            rows = self._storage.load_group_history(
                bytes.fromhex(conv_id[6:]), self._local_mac, n
            )
        else:
            return
        self._msg_model.load_history(rows, self._local_mac, self._gs.display_name)

    @Slot(int)
    def cycleConv(self, delta: int) -> None:
        """Move active conv by delta in the peer-list order (wraps)."""
        items = self._peer_model._items
        if not items:
            return
        ids = [it["convId"] for it in items]
        try:
            idx = ids.index(self._active_conv_id)
        except ValueError:
            idx = -1
        new_idx = (idx + delta) % len(ids)
        self.setActiveConv(ids[new_idx])

    @Slot(result=str)
    def firstConvId(self) -> str:
        items = self._peer_model._items
        return items[0]["convId"] if items else ""

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
            rows = self._storage.load_group_history(gid, self._local_mac, 50)
        else:
            self._msg_model.clear()
            return
        self._msg_model.load_history(rows, self._local_mac, self._gs.display_name)
