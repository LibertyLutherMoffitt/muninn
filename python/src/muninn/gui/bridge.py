"""ChatBridge — Qt/QML ↔ Mesh glue, command dispatcher and tab completion.

Mesh events arrive on the mesh event thread; they are re-emitted as Qt
signals so all model mutation happens on the GUI thread.
"""

from __future__ import annotations

import mimetypes
import os
import shlex
import threading
import time
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QDesktopServices, QGuiApplication

from muninn import emoji
from muninn.mesh import conv_group_id, dm_conv, group_conv
from muninn.protocol import FrameTooLarge

from muninn import gamehost

from . import notifications
from .models import MessageListModel, PeerListModel

if TYPE_CHECKING:
    from muninn.node import Node
    from muninn.storage import AppEvent, StoredMessage


QVariant = cast(type, "QVariant")  # PySide accepts the type name


def _shquote(s: str) -> str:
    if not s:
        return '""'
    return shlex.quote(s) if any(c.isspace() for c in s) else s


class ChatBridge(QObject):
    # internal hops from mesh thread -> GUI thread
    _sigMessage = Signal(object)
    _sigUpdate = Signal(object)
    _sigPeers = Signal()
    _sigGroup = Signal(object)
    _sigFile = Signal(object)
    _sigTyping = Signal(str, str)
    _sigApp = Signal(object)
    _sigRt = Signal(str, str, str, QVariant)

    peerChanged = Signal(str, bool)
    activeConvChanged = Signal(str)
    scanResultsReady = Signal(list)
    errorOccurred = Signal(str)
    notify = Signal(str)
    quitRequested = Signal()
    scanRequested = Signal()
    paletteRequested = Signal()
    infoMenuRequested = Signal(str, list)
    replyTargetChanged = Signal()
    editTargetChanged = Signal(str)  # text to load into composer
    selectionChanged = Signal(int)  # row index to scroll to
    messageSent = Signal()  # the chat view jumps to the bottom
    removeRequested = Signal(list, str)  # convs, how — QML asks to confirm
    trayShowRequested = Signal()
    gameImportRequested = Signal()
    keepRunningChanged = Signal()
    typingChanged = Signal()
    filePickRequested = Signal()
    emojiPickerRequested = Signal(str)  # mode: "insert" | "react"
    imageViewRequested = Signal(str)
    gameOpenRequested = Signal(str)  # session id
    gameEvent = Signal(str)  # session id (state changed)
    gameRt = Signal(str, str, QVariant)  # session, sender, body
    statusChanged = Signal()

    def __init__(
        self,
        node: Node,
        peer_model: PeerListModel,
        msg_model: MessageListModel,
        parent=None,
    ):
        super().__init__(parent)
        self.node = node
        self.mesh = node.mesh
        self._peers = peer_model
        self._msgs = msg_model
        self._active = ""
        self._reply_to = ""
        self._edit_id = ""
        self._last_typing_sent = 0.0
        self._connected: set[str] = set()
        self.games = gamehost.GameRegistry(node)

        m = self.mesh
        m.on("message", self._sigMessage.emit)
        m.on("update", self._sigUpdate.emit)
        m.on("peers", self._sigPeers.emit)
        m.on("group", self._sigGroup.emit)
        m.on("file", self._sigFile.emit)
        m.on("typing", self._sigTyping.emit)
        m.on("app", self._sigApp.emit)
        m.on(
            "rt",
            lambda src, app, session, body, blob: self._sigRt.emit(
                src, app, session, body
            ),
        )
        self._sigMessage.connect(self._on_message)
        self._sigUpdate.connect(self._on_update)
        self._sigPeers.connect(self._on_peers)
        self._sigGroup.connect(lambda gid: self._peers.refresh())
        self._sigFile.connect(self._on_file)
        self._sigTyping.connect(self._on_typing)
        self._sigApp.connect(self._on_app)
        self._sigRt.connect(self._on_rt)
        self._peers.refresh()

    # ------------------------------------------------------------------ events

    def _on_message(self, m: StoredMessage) -> None:
        self._peers.update_conv(m.conv)
        if m.conv == self._active:
            self._msgs.append(m)
            if m.sender == self.mesh.node_id:  # text, file or sticker we sent
                self.messageSent.emit()
            if m.sender != self.mesh.node_id and self._window_focused():
                self.mesh.mark_read(m.conv)
                self._peers.update_conv(m.conv)
        if (
            m.sender != self.mesh.node_id
            and m.kind in ("text", "file")
            and not self.mesh.is_muted(m.conv)
        ):
            if not (m.conv == self._active and self._window_focused()):
                who = self.mesh.display_name(m.sender)
                gid = conv_group_id(m.conv)
                title = (
                    f"{who} · {self.mesh.groups[gid].name}"
                    if gid and gid in self.mesh.groups
                    else who
                )
                body = m.text if m.kind == "text" else "📎 " + m.meta.get("n", "file")
                notifications.notify(title, body)

    def _on_update(self, msg_id: bytes) -> None:
        self._msgs.refresh_msg(msg_id)
        m = self.mesh.storage.get_message(msg_id)
        if m:
            self._peers.update_conv(m.conv)

    def _on_peers(self) -> None:
        now = set(self.mesh.connected())
        for n in now - self._connected:
            self.peerChanged.emit(n, True)
        for n in self._connected - now:
            self.peerChanged.emit(n, False)
        self._connected = now
        self._peers.update_conv("")
        self.statusChanged.emit()

    def _on_file(self, file_id: bytes) -> None:
        rec = self.mesh.files.get(file_id)
        if rec is not None:
            self._msgs.refresh_file(rec.msg_id)

    def _on_typing(self, conv: str, node: str) -> None:
        self._peers.update_conv(conv)
        self.typingChanged.emit()

    def _on_app(self, ev: AppEvent) -> None:
        self.games.on_event(ev)
        self.gameEvent.emit(ev.session)
        if ev.type == "invite" and ev.sender != self.mesh.node_id:
            info = self.games.session_info(ev.session)
            who = self.mesh.display_name(ev.sender)
            notifications.notify(who, f"🎮 invites you to {info.get('name', ev.app)}")
            self.notify.emit(
                f"{who} invited you to {info.get('name', ev.app)} — :games"
            )
        self._peers.update_conv(ev.conv)

    def _on_rt(self, src: str, app: str, session: str, body) -> None:
        self.gameRt.emit(session, src, body)

    @staticmethod
    def _window_focused() -> bool:
        app = QGuiApplication.instance()
        return app is not None and app.focusWindow() is not None

    # ------------------------------------------------------------------ properties

    @Property(str, constant=True)
    def localMac(self) -> str:
        return self.mesh.node_id

    @Property(str, notify=statusChanged)
    def localName(self) -> str:
        return self.mesh.name

    @Property(str, constant=True)
    def suggestedName(self) -> str:
        import getpass

        try:
            return getpass.getuser().capitalize()
        except Exception:
            return ""

    @Property(bool, constant=True)
    def isWriter(self) -> bool:
        return True

    @Property(int, notify=statusChanged)
    def connectedPeerCount(self) -> int:
        return len(self.mesh.connected())

    @Property(int, notify=statusChanged)
    def reachablePeerCount(self) -> int:
        return len(
            [p for p in self.mesh.active_peers() if self.mesh.reachable(p.node_id)]
        )

    @Property(str, constant=True)
    def transportName(self) -> str:
        return "SIM" if os.environ.get("MUNINN_SIM") else "BT"

    @Property(str, notify=activeConvChanged)
    def activeConvId(self) -> str:
        return self._active

    @Property(str, notify=activeConvChanged)
    def activeTitle(self) -> str:
        return self._conv_title(self._active)

    @Property(str, notify=statusChanged)
    def activeSubtitle(self) -> str:
        c = self._active
        if c.startswith("dm:"):
            nid = c[3:]
            pres = self.node.presence(nid)
            n = self.mesh.pending_count(nid)
            return pres["text"] + (f" · {n} waiting to send" if n else "")
        gid = conv_group_id(c)
        if gid and gid in self.mesh.groups:
            g = self.mesh.groups[gid]
            names = [
                self.mesh.display_name(x)
                for x in sorted(g.members)
                if x != self.mesh.node_id
            ]
            here = sum(
                1
                for x in g.members
                if x != self.mesh.node_id and self.mesh.reachable(x)
            )
            return f"{here}/{len(names)} in range · " + ", ".join(names)
        return ""

    @Property(str, notify=typingChanged)
    def typingText(self) -> str:
        who = [self.mesh.display_name(n) for n in self.mesh.typing_in(self._active)]
        if not who:
            return ""
        return ", ".join(who) + (" is" if len(who) == 1 else " are") + " typing…"

    @Property(str, notify=replyTargetChanged)
    def replyPreview(self) -> str:
        if not self._reply_to:
            return ""
        r = self._msgs.get(self._reply_to)
        if r is None:
            return ""
        who = "yourself" if r["isOutbound"] else r["senderName"]
        return f"Replying to {who}: {(r['text'] or r['fileName'])[:80]}"

    @Property(str, notify=replyTargetChanged)
    def editPreview(self) -> str:
        return "Editing message" if self._edit_id else ""

    @Slot(str, result=str)
    def convTitle(self, conv: str) -> str:
        return self._conv_title(conv)

    def _conv_title(self, conv: str) -> str:
        if conv.startswith("dm:"):
            return self.mesh.display_name(conv[3:])
        gid = conv_group_id(conv)
        if gid and gid in self.mesh.groups:
            return self.mesh.groups[gid].name
        return ""

    # ------------------------------------------------------------------ conv / messages

    @Slot(str)
    def setActiveConv(self, conv_id: str) -> None:
        if conv_id == self._active:
            return
        self._active = conv_id
        self._reply_to = self._edit_id = ""
        self._msgs.selected = ""
        self._msgs.load(conv_id)
        if conv_id:
            self.mesh.mark_read(conv_id)
        self._peers.update_conv(conv_id)
        self.activeConvChanged.emit(conv_id)
        self.replyTargetChanged.emit()
        self.statusChanged.emit()
        self.typingChanged.emit()

    @Slot()
    def windowActivated(self) -> None:
        if self._active:
            self.mesh.mark_read(self._active)
            self._peers.update_conv(self._active)

    @Slot(str, str)
    def sendMessage(self, conv_id: str, text: str) -> None:
        text = emoji.expand(text.strip())
        if not text or not conv_id:
            return
        try:
            if self._edit_id:
                self.mesh.edit(conv_id, bytes.fromhex(self._edit_id), text)
                self._edit_id = ""
            else:
                reply = bytes.fromhex(self._reply_to) if self._reply_to else None
                self.mesh.send_text(conv_id, text, reply)
                self._reply_to = ""
        except FrameTooLarge:
            self.errorOccurred.emit("Message too long — send it as a file instead")
            return
        except ValueError as e:
            self.errorOccurred.emit(str(e))
            return
        self.replyTargetChanged.emit()
        if not (
            self.mesh.conv_members(conv_id)
            and any(self.mesh.reachable(n) for n in self.mesh.conv_members(conv_id))
        ):
            self.notify.emit("Out of range — will send automatically when they're back")

    @Slot()
    def composerActivity(self) -> None:
        now = time.monotonic()
        if self._active and now - self._last_typing_sent > 3.0:
            self._last_typing_sent = now
            try:
                self.mesh.send_typing(self._active)
            except Exception:
                pass

    # ------------------------------------------------------------------ forgetting

    @Slot(list, str)
    def removeConvs(self, convs: list, how: str) -> None:
        """how: "delete" (chats only) | "forget" | "block" (people; rooms in
        the selection are always just deleted = left)."""
        names = []
        for conv in [str(c) for c in convs]:
            names.append(self._conv_title(conv))
            if conv.startswith("dm:") and how in ("forget", "block"):
                self.node.forget(conv[3:], block=how == "block")
            else:
                self.mesh.delete_conversation(conv)
            if conv == self._active:
                self.setActiveConv("")
        self._peers.refresh()
        verb = {"delete": "deleted", "forget": "forgot", "block": "blocked"}[how]
        self.notify.emit(
            f"{verb} {', '.join(names)}"
            if len(names) < 4
            else f"{verb} {len(names)} chats"
        )

    @Slot(result=list)
    def blockedPeople(self) -> list:
        return sorted(self.mesh.blocked)

    def _conv_title(self, conv: str) -> str:
        if conv.startswith("dm:"):
            return self.mesh.display_name(conv[3:])
        g = self.mesh.groups.get(conv_group_id(conv) or b"")
        return g.name if g else "room"

    @Slot(str)
    def selectMessage(self, msg_id: str) -> None:
        self._msgs.select(msg_id)
        self.selectionChanged.emit(self._msgs.row_of(msg_id))

    @Slot(int, result=bool)
    def moveSelection(self, delta: int) -> bool:
        """Move the message selection; delta 0 = newest. False when moving
        down past the newest message (the caller returns to the composer)."""
        ids = self._msgs.ids()
        if not ids:
            return False
        cur = self._msgs.selected
        if delta == 0 or cur not in ids:
            i = len(ids) - 1
        else:
            i = ids.index(cur) + delta
            if i >= len(ids):
                self.selectMessage("")
                return False
            i = max(0, i)
        self.selectMessage(ids[i])
        return True

    @Slot(str)
    def messageAction(self, action: str) -> None:
        """Vim MESSAGES-mode verbs on the selected message."""
        mid = self._msgs.selected
        ids = self._msgs.ids()
        if action == "clear":
            if not (self._reply_to or self._edit_id):
                self.selectMessage("")
            return
        if action in ("first", "last") and ids:
            self.selectMessage(ids[0] if action == "first" else ids[-1])
            return
        if not mid:
            return
        if action == "reply":
            self.replyTo(mid)
        elif action == "react":
            self.emojiPickerRequested.emit("react")
        elif action.startswith("quick:"):
            self.toggleReaction(mid, emoji.QUICK_REACTIONS[int(action[6:]) - 1])
        elif action == "edit":
            self.startEdit(mid)
        elif action == "delete":
            self.deleteMessage(mid)
        elif action == "copy":
            self.copyText(mid)
            self.notify.emit("copied")
        elif action == "open":
            self.openFile(mid)

    def _target(self) -> str:
        """Selected message, or the newest message from someone else."""
        if self._msgs.selected:
            return self._msgs.selected
        for mid in reversed(self._msgs.ids()):
            r = self._msgs.get(mid)
            if r and not r["isOutbound"]:
                return mid
        ids = self._msgs.ids()
        return ids[-1] if ids else ""

    @Slot(str)
    def replyTo(self, msg_id: str) -> None:
        self._reply_to = msg_id or self._target()
        self._edit_id = ""
        self.replyTargetChanged.emit()

    @Slot()
    def cancelReply(self) -> None:
        self._reply_to = self._edit_id = ""
        self._msgs.select("")
        self.replyTargetChanged.emit()

    @Slot(str, str)
    def toggleReaction(self, msg_id: str, emo: str) -> None:
        msg_id = msg_id or self._target()
        if not msg_id or not self._active:
            return
        emo = emoji.expand(emo)
        m = self.mesh.storage.get_message(bytes.fromhex(msg_id))
        if m is None:
            return
        mine = self.mesh.node_id in m.reactions.get(emo, [])
        self.mesh.react(self._active, m.msg_id, emo, remove=mine)

    @Slot(str)
    def startEdit(self, msg_id: str) -> None:
        msg_id = msg_id or self._msgs.selected
        r = self._msgs.get(msg_id) if msg_id else None
        if r is None:
            # newest own message
            for mid in reversed(self._msgs.ids()):
                rr = self._msgs.get(mid)
                if (
                    rr
                    and rr["isOutbound"]
                    and rr["kind"] == "text"
                    and not rr["deleted"]
                ):
                    r = rr
                    break
        if r is None or not r["isOutbound"] or r["kind"] != "text":
            self.errorOccurred.emit("You can only edit your own text messages")
            return
        self._edit_id = r["msgId"]
        self._reply_to = ""
        self.replyTargetChanged.emit()
        self.editTargetChanged.emit(r["text"])

    @Slot(str)
    def deleteMessage(self, msg_id: str) -> None:
        msg_id = msg_id or self._msgs.selected
        r = self._msgs.get(msg_id) if msg_id else None
        if r is None or not r["isOutbound"]:
            self.errorOccurred.emit("Select one of your own messages to delete (Alt+↑)")
            return
        self.mesh.delete(self._active, bytes.fromhex(msg_id))

    @Slot(str)
    def copyText(self, msg_id: str) -> None:
        r = self._msgs.get(msg_id or self._target())
        if r:
            QGuiApplication.clipboard().setText(r["text"])
            self.notify.emit("copied")

    @Slot(str)
    def openFile(self, msg_id: str) -> None:
        r = self._msgs.get(msg_id or self._target())
        if r is None or r["kind"] != "file":
            return
        if r["fileState"] == "complete":
            if r["isImage"]:
                self.imageViewRequested.emit(r["fileUrl"])
            elif r["fileName"].endswith(".js"):  # a game: clicking installs it
                self.installGame(r["msgId"])
            else:
                QDesktopServices.openUrl(QUrl(r["fileUrl"]))
        elif r["fileState"] in ("offered", "failed"):
            m = self.mesh.storage.get_message(bytes.fromhex(r["msgId"]))
            if m:
                self.mesh.download(bytes.fromhex(m.meta["f"]))
                self.notify.emit("downloading…")

    @Slot(str)
    def openFolder(self, msg_id: str) -> None:
        r = self._msgs.get(msg_id or self._target())
        if r and r["fileUrl"]:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.dirname(QUrl(r["fileUrl"]).toLocalFile()))
            )

    @Slot(list)
    def sendFiles(self, urls: list) -> None:
        if not self._active:
            self.errorOccurred.emit("Open a conversation first")
            return
        for u in urls:
            path = QUrl(u).toLocalFile() if "://" in str(u) else str(u)
            self._send_path(path)

    def _send_path(self, path: str, caption: str = "") -> None:
        if not os.path.isfile(path):
            self.errorOccurred.emit(f"not a file: {path}")
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        thumb = _thumbnail(path) if mime.startswith("image/") else b""
        reply = bytes.fromhex(self._reply_to) if self._reply_to else None
        try:
            self.mesh.send_file(
                self._active, path, mime, caption=caption, thumb=thumb, reply_to=reply
            )
        except (OSError, ValueError) as e:
            self.errorOccurred.emit(str(e))
            return
        self._reply_to = ""
        self.replyTargetChanged.emit()

    @Slot(str, result=str)
    def displayName(self, addr: str) -> str:
        return self.mesh.display_name(addr)

    @Slot(str)
    def setDisplayName(self, name: str) -> None:
        self.mesh.set_name(name)
        self.statusChanged.emit()

    @Slot(str, result=str)
    def resolveAddr(self, name_or_id: str) -> str:
        return self.mesh.resolve(name_or_id) or ""

    @Slot(result=list)
    def emojiCategories(self) -> list:
        return [
            {"name": cat, "items": [{"code": c, "emoji": e} for c, e in items]}
            for cat, items in emoji.CATEGORIES.items()
        ]

    def _sticker_dirs(self) -> list[tuple[str, str]]:
        from pathlib import Path

        user = Path(self.node.storage.path).parent / "stickers"
        user.mkdir(parents=True, exist_ok=True)
        bundled = Path(emoji.__file__).parent / "assets" / "stickers"
        return [("saved", str(user)), ("pack", str(bundled))]

    @Slot(result=list)
    def stickers(self) -> list:
        out = []
        for kind, d in self._sticker_dirs():
            for p in sorted(os.listdir(d)):
                if p.lower().endswith((".gif", ".png", ".webp", ".jpg")):
                    full = os.path.join(d, p)
                    out.append(
                        {
                            "name": os.path.splitext(p)[0],
                            "kind": kind,
                            "url": QUrl.fromLocalFile(full).toString(),
                            "path": full,
                        }
                    )
        return out

    @Slot(str)
    def sendSticker(self, path: str) -> None:
        if not self._active:
            return self.errorOccurred.emit("Open a conversation first")
        self._send_path(path)

    @Slot(str)
    def saveSticker(self, msg_id: str) -> None:
        r = self._msgs.get(msg_id or self._target())
        if not r or r["fileState"] != "complete" or not r["isImage"]:
            return self.errorOccurred.emit("Only downloaded pictures/GIFs can be saved")
        import shutil

        src = QUrl(r["fileUrl"]).toLocalFile()
        dst = os.path.join(self._sticker_dirs()[0][1], os.path.basename(src))
        shutil.copyfile(src, dst)
        self.notify.emit("saved to your stickers")

    @Slot(result=list)
    def quickReactions(self) -> list:
        return list(emoji.QUICK_REACTIONS)

    @Slot(str, result=str)
    def expandEmoji(self, text: str) -> str:
        return emoji.expand(text)

    # ------------------------------------------------------------------ scanning

    @Slot()
    def startScan(self) -> None:
        def run():
            try:
                res = self.node.scan_now(6.0)
                known = {a for _, a, _ in self.node.storage.load_transports()}
                out = sorted(
                    (
                        {
                            "mac": r.addr,
                            "name": r.name or r.addr,
                            "muninn": bool(r.muninn),
                            "maybe": r.probeable,
                            "kind": r.kind,
                            "known": r.addr in known,
                            "rssi": r.rssi if r.rssi is not None else -127,
                            "state": self.node.device_state(r.addr),
                        }
                        for r in res
                    ),
                    key=lambda d: (not d["muninn"], not d["maybe"], d["name"].lower()),
                )
                self.scanResultsReady.emit(out)
            except Exception as e:
                self.errorOccurred.emit(f"Scan error: {e}")
                self.scanResultsReady.emit([])

        threading.Thread(target=run, daemon=True).start()

    _keep_running = False

    def _get_keep(self) -> bool:
        return self._keep_running

    def _set_keep(self, v: bool) -> None:
        self._keep_running = bool(v)
        self.keepRunningChanged.emit()

    keepRunning = Property(bool, _get_keep, _set_keep, notify=keepRunningChanged)

    @Slot()
    def wentToBackground(self) -> None:
        if self.node.storage.get_setting("bg_notice") != "1":
            self.node.storage.set_setting("bg_notice", "1")
            notifications.notify(
                "Muninn keeps running",
                "Messages still arrive and relay in the background. "
                "Quit from the tray icon (or :background off).",
            )

    @Slot(result=str)
    def findingStatus(self) -> str:
        if not self.node.finding:
            return ""
        left = self.node.find_left()
        return (
            f"finding people {left // 60}:{left % 60:02d}" if left else "finding people"
        )

    @Slot(list, result=dict)
    def deviceStates(self, addrs: list) -> dict:
        """Live per-device state for the scan dialog (probes finish later)."""
        return {a: self.node.device_state(a) for a in addrs}

    @Slot(str)
    def pairDevice(self, addr: str) -> None:
        def run():
            try:
                r = self.node.sightings.get(addr.upper())
                nid = self.node.connect_to(addr, probe=bool(r and not r.muninn))
                if nid:
                    self.notify.emit(f"connected to {self.mesh.display_name(nid)}")
                else:
                    self.errorOccurred.emit("connected, but it isn't speaking Muninn")
            except Exception as e:
                self.errorOccurred.emit(f"Could not connect: {e}")

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ games

    @Slot(result=list)
    def availableGames(self) -> list:
        return self.games.catalog()

    @Slot(str, result=list)
    def gameSessions(self, conv: str) -> list:
        return self.games.sessions(conv or None)

    @Slot(str, result=QVariant)
    def gameSession(self, session: str):
        return self.games.session_info(session)

    @Slot(str, result=str)
    def gameCode(self, session: str) -> str:
        return self.games.code_for(session)

    @Slot(str, str, result=str)
    def startGame(self, game_id: str, conv: str, opts: dict | None = None) -> str:
        try:
            sid = self.games.invite(conv or self._active, game_id, opts)
        except ValueError as e:
            self.errorOccurred.emit(str(e))
            return ""
        self.gameOpenRequested.emit(sid)
        return sid

    @staticmethod
    def _plain(v):
        """QML hands JS objects over as QJSValue; turn them into JSON-able data."""
        from PySide6.QtQml import QJSValue

        if isinstance(v, QJSValue):
            v = v.toVariant()
        if isinstance(v, dict):
            return {str(k): ChatBridge._plain(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [ChatBridge._plain(x) for x in v]
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v

    @Slot(str, str, QVariant)
    def gameAction(self, session: str, type_: str, body) -> None:
        try:
            self.games.act(session, type_, self._plain(body))
        except ValueError as e:
            self.errorOccurred.emit(str(e))

    @Slot(str)
    def gameWithdraw(self, session: str) -> None:
        try:
            self.games.withdraw(session)
            self.notify.emit("invite unsent")
        except ValueError as e:
            self.errorOccurred.emit(str(e))

    @Slot(str, str, result=str)
    def gameScoreLine(self, conv: str, app: str) -> str:
        """'bob 3 · you 1 · 1 draw' for one game in one conversation."""
        for g in self.games.scores(conv or None):
            if g["app"] == app:
                return self._score_text(g)
        return ""

    def _score_text(self, g: dict) -> str:
        me = self.mesh.node_id
        parts = [
            f"{'you' if n == me else self.mesh.display_name(n)} {w}"
            for n, w in sorted(g["wins"].items(), key=lambda kv: -kv[1])
        ]
        if g["draws"]:
            parts.append(f"{g['draws']} draw{'s' if g['draws'] > 1 else ''}")
        return " · ".join(parts) or "no winner yet"

    @Slot(str, QVariant)
    def gameRtSend(self, session: str, body) -> None:
        self.games.rt(session, self._plain(body))

    @Slot(str)
    def installGame(self, msg_id: str) -> None:
        r = self._msgs.get(msg_id or self._target())
        if not r or r["fileState"] != "complete":
            self.errorOccurred.emit("Download the game file first")
            return
        try:
            self._install_game_file(QUrl(r["fileUrl"]).toLocalFile())
        except ValueError as e:
            self.errorOccurred.emit(str(e))

    def _install_game_file(self, path: str) -> None:
        gid = self.games.install(path)
        name = next((g["name"] for g in self.games.catalog() if g["id"] == gid), gid)
        self.notify.emit(f"installed {name} — :game {gid} to play")

    @Slot(str)
    def importGame(self, path: str) -> None:
        try:
            self._install_game_file(
                QUrl(path).toLocalFile() if path.startswith("file:") else path
            )
        except (OSError, ValueError) as e:
            self.errorOccurred.emit(f"not a Muninn game: {e}")

    # ------------------------------------------------------------------ commands

    _COMMANDS = (
        "dm",
        "group",
        "new",
        "add",
        "kick",
        "rename",
        "leave",
        "nick",
        "list",
        "peers",
        "known",
        "status",
        "history",
        "scan",
        "connect",
        "reconnect",
        "reply",
        "react",
        "edit",
        "delete",
        "copy",
        "file",
        "get",
        "open",
        "emoji",
        "game",
        "games",
        "scores",
        "importgame",
        "sharegame",
        "find",
        "background",
        "autostart",
        "whoami",
        "members",
        "mute",
        "clear",
        "forget",
        "block",
        "unblock",
        "blocked",
        "unmute",
        "search",
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

    _HELP = (
        (":dm <peer>", "switch to a direct conversation"),
        (":group <name>", "switch to a group"),
        (":new <name> <peer1> [peer2…]", "create a group"),
        (":add <peer…> / :kick <peer>", "change members of this group"),
        (":rename <name> / :leave", "rename or leave this group"),
        (":members", "who's in this group and who's in range"),
        (":clear", "delete this chat on this device (a room is also left)"),
        (
            ":forget <peer…> / :block <peer…>",
            "forget people (chat, addresses, pairing) / also refuse them",
        ),
        (":unblock <id> / :blocked", "allow someone again / list blocked people"),
        (":mute / :unmute", "silence notifications for this chat"),
        (":search <words>", "find messages in all chats"),
        (":nick <name>", "set your display name"),
        (":nick <peer> <name>", 'local nickname for a peer ("" clears)'),
        (
            "k  (normal mode)",
            "pick a message: j/k move, Enter/r reply, e edit, + react, 1-8 quick react, dd delete, y copy, o open, Esc back",
        ),
        (":reply [text]", "reply to selected message (or Alt+↑/↓ then Alt+R)"),
        (":react <emoji>", "react to selected message, e.g. :react :fire: (Alt+E)"),
        (":edit / :delete", "edit or delete your selected/last message"),
        (":file [path]", "send a file or picture (or drag & drop)"),
        (":get / :open", "download / open the selected file"),
        (":emoji", "emoji picker (Ctrl+E). :shortcodes: expand on send"),
        (":game [name] [opt=value]", "start a game in this conversation"),
        (":games", "games in this conversation"),
        (
            ":importgame [path]",
            "install a game file (.js) — or click a game someone sent",
        ),
        (":scores", "who won how many of each game (this chat, or all chats)"),
        (":sharegame <name>", "send a game's code to this conversation"),
        (":list", "conversations"),
        (":peers / :known", "who's in range (direct/relay) / everyone you know"),
        (":status", "links, routes and queues"),
        (":scan / :connect <addr>", "find / dial a nearby device"),
        (":reconnect", "retry every known device now"),
        (":find [minutes|off]", "look for new people for a while (default 5 min)"),
        (":background [on|off]", "keep running in the tray when the window is closed"),
        (":autostart [on|off]", "start Muninn (hidden, in the tray) when you log in"),
        (
            ":reconnect [fast|saver]",
            "retry known people now / how eagerly to redial them",
        ),
        (":whoami", "your name, id, radio and scan mode"),
        (":history [N]", "load more history"),
        (":next / :prev", "cycle conversations (Ctrl-N / Ctrl-P)"),
        (":palette", "command palette (<space>f)"),
        (":about", "version + identity"),
        (":q", "quit"),
    )

    def _candidates_for(self, head: str, arg_index: int) -> list[str]:
        if head in ("dm", "nick", "kick") and arg_index == 1:
            return self._peer_candidates()
        if head in ("new",) and arg_index >= 2 or head == "add":
            return self._peer_candidates()
        if head == "group" and arg_index == 1:
            return [g.name for g in self.mesh.groups.values()]
        if head in ("game", "sharegame") and arg_index == 1:
            return [g["id"] for g in self.games.catalog()]
        if head == "react" and arg_index == 1:
            return [f":{c}:" for c in emoji.SHORTCODES]
        return []

    def _peer_candidates(self) -> list[str]:
        out = []
        for p in self.mesh.active_peers():
            out.append(p.display)
        return out

    @staticmethod
    def _common_prefix(strings: list[str]) -> str:
        if not strings:
            return ""
        s = strings[0]
        for o in strings[1:]:
            i = 0
            while i < len(s) and i < len(o) and s[i] == o[i]:
                i += 1
            s = s[:i]
        return s

    @Slot(str, result=str)
    def completeCommand(self, buf: str) -> str:
        colon = buf.startswith(":")
        body = buf[1:] if colon else buf
        trailing = body.endswith(" ")
        try:
            tokens = shlex.split(body) if body.strip() else []
        except ValueError:
            return buf
        if not tokens:
            return buf
        pre = ":" if colon else ""
        if len(tokens) == 1 and not trailing:
            matches = [c for c in self._COMMANDS if c.startswith(tokens[0].lower())]
            if not matches:
                return buf
            return pre + (
                matches[0] + " " if len(matches) == 1 else self._common_prefix(matches)
            )
        head = tokens[0].lower()
        idx = len(tokens) if trailing else len(tokens) - 1
        partial = "" if trailing else tokens[-1]
        matches = [
            c
            for c in self._candidates_for(head, idx)
            if c.lower().startswith(partial.lower())
        ]
        if not matches:
            return buf
        if len(matches) == 1:
            arg = matches[0] + " "
        else:
            cp = self._common_prefix([m.lower() for m in matches])
            arg = matches[0][: len(cp)] if len(cp) > len(partial) else partial
        rebuilt = " ".join(_shquote(t) for t in tokens[:idx])
        rebuilt = (rebuilt + " " if rebuilt else "") + _shquote(arg.rstrip(" "))
        return pre + rebuilt + (" " if arg.endswith(" ") else "")

    @staticmethod
    def _item(label, sub="", conv="", action="", session="", cmd="", url="") -> dict:
        return {
            "label": label,
            "sub": sub,
            "convId": conv,
            "action": action,
            "session": session,
            "cmd": cmd,
            "url": url,
        }

    def _info(self, title: str, items: list) -> None:
        rows = [self._item(*it) if isinstance(it, tuple) else it for it in items]
        self.infoMenuRequested.emit(title, rows or [self._item("(nothing here)")])

    def _resolve_or_err(self, s: str) -> str | None:
        r = self.mesh.resolve(s)
        if r is None:
            self.errorOccurred.emit(f"unknown peer: {s}")
        return r

    def _resolve_all(self, names: list[str]) -> list[str] | None:
        out = []
        for n in names:
            r = self._resolve_or_err(n)
            if r is None:
                return None
            out.append(r)
        return out

    @Slot(str)
    def runCommand(self, cmd: str) -> None:
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            self.errorOccurred.emit(f"parse error: {e}")
            return
        if not parts:
            return
        head, args = parts[0].lower(), parts[1:]
        mesh = self.mesh
        gid = conv_group_id(self._active)

        if head in ("q", "qa", "q!", "qa!", "quit", "wq", "x"):
            self.quitRequested.emit()
        elif head == "w":
            self.notify.emit("nothing to save — messages are saved automatically")
        elif head == "scan":
            self.scanRequested.emit()
        elif head in ("palette", "pal", "find", "f"):
            self.paletteRequested.emit()
        elif head == "dm":
            if not args:
                return self.errorOccurred.emit("usage: :dm <name>")
            if r := self._resolve_or_err(args[0]):
                self.setActiveConv(dm_conv(r))
        elif head == "group":
            if not args:
                return self.errorOccurred.emit("usage: :group <name>")
            for g in mesh.groups.values():
                if g.name.lower() == args[0].lower():
                    return self.setActiveConv(g.conv)
            self.errorOccurred.emit(f"group not found: {args[0]}")
        elif head == "new":
            if len(args) < 2:
                return self.errorOccurred.emit("usage: :new <name> <peer1> [peer2] …")
            peers = self._resolve_all(args[1:])
            if peers is None:
                return
            try:
                g = mesh.create_group(args[0], peers)
            except ValueError as e:
                return self.errorOccurred.emit(str(e))
            self.setActiveConv(group_conv(g))
            self.notify.emit(f"created group “{args[0]}”")
        elif head in ("add", "kick", "rename", "leave"):
            if gid is None:
                return self.errorOccurred.emit("open a group first")
            if head == "add" and args:
                peers = self._resolve_all(args)
                if peers is not None:
                    mesh.update_group(gid, add=peers)
            elif head == "kick" and args:
                if r := self._resolve_or_err(args[0]):
                    mesh.update_group(gid, remove=[r])
            elif head == "rename" and args:
                mesh.update_group(gid, name=" ".join(args))
            elif head == "leave":
                mesh.leave_group(gid)
                self.notify.emit("left the group")
            else:
                self.errorOccurred.emit(f"usage: :{head} …")
        elif head == "nick":
            if len(args) == 1:
                self.setDisplayName(args[0])
                self.notify.emit(f"you are now “{args[0]}”")
            elif len(args) == 2:
                if r := self._resolve_or_err(args[0]):
                    mesh.set_override(r, args[1] or None)
                    self.notify.emit(
                        "nickname cleared" if not args[1] else f"{r} → {args[1]}"
                    )
            else:
                self.errorOccurred.emit("usage: :nick <name> | :nick <peer> <name>")
        elif head in ("next", "bn"):
            self.cycleConv(1)
        elif head in ("prev", "bp"):
            self.cycleConv(-1)
        elif head in ("clear", "cls"):
            self._msgs.clear()
        elif head == "history":
            n = int(args[0]) if args and args[0].isdigit() else 500
            self._msgs.load(self._active, n)
        elif head == "reply":
            self.replyTo("")
            if args:
                self.sendMessage(self._active, " ".join(args))
        elif head == "react":
            self.toggleReaction(
                "", args[0] if args else "👍"
            ) if args else self.emojiPickerRequested.emit("react")
        elif head == "edit":
            self.startEdit("")
        elif head in ("delete", "del"):
            self.deleteMessage("")
        elif head == "copy":
            self.copyText("")
        elif head == "file":
            if args:
                self._send_path(os.path.expanduser(args[0]), " ".join(args[1:]))
            else:
                self.filePickRequested.emit()
        elif head in ("get", "open"):
            self.openFile("")
        elif head == "emoji":
            self.emojiPickerRequested.emit("insert")
        elif head == "game":
            if not self._active:
                return self.errorOccurred.emit("open a conversation first")
            if not args:
                return self._info(
                    "start a game",
                    [
                        self._item(
                            f"{g['icon']}  {g['name']}",
                            f"{g['players']}" + (" · installed" if g["user"] else ""),
                            action="cmd",
                            cmd=f"game {g['id']}",
                        )
                        for g in self.games.catalog()
                    ],
                )
            # options as key=value; ask for missing ones one menu at a time
            game = next((g for g in self.games.catalog() if g["id"] == args[0]), None)
            opts = dict(a.split("=", 1) for a in args[1:] if "=" in a)
            missing = [
                o for o in (game or {}).get("options", []) if o["id"] not in opts
            ]
            if game and missing:
                o = missing[0]
                base = "game " + " ".join(args)
                return self._info(
                    f"{game['icon']}  {game['name']} — {o['label']}",
                    [
                        self._item(
                            str(c),
                            "default" if c == o.get("default") else "",
                            action="cmd",
                            cmd=f"{base} {o['id']}={c}",
                        )
                        for c in o.get("choices", [])
                    ],
                )
            self.startGame(args[0], self._active, opts)
        elif head == "importgame":
            if args:
                self.importGame(os.path.expanduser(" ".join(args)))
            else:
                self.gameImportRequested.emit()
        elif head == "scores":
            board = self.games.scores(self._active or None)
            where = self._conv_title(self._active) if self._active else "all chats"
            self._info(
                f"scores — {where}",
                [
                    self._item(
                        f"{g['icon']}  {g['name']}",
                        f"{self._score_text(g)}  ({g['played']} played)",
                    )
                    for g in board
                ]
                or [("no finished games yet", "start one with :game")],
            )
        elif head == "games":
            sess = self.games.sessions(self._active or None)
            self._info(
                "games",
                [
                    self._item(
                        f"{s['icon']} {s['name']}",
                        ("invites you · " if s["invitedMe"] else "") + s["status"],
                        action="game",
                        session=s["session"],
                    )
                    for s in sess
                ]
                or [
                    self._item(
                        "(no games yet)",
                        "start one with :game",
                        action="cmd",
                        cmd="game",
                    )
                ],
            )
        elif head == "sharegame":
            if not args or not self._active:
                return self.errorOccurred.emit(
                    "usage: :sharegame <game> (in a conversation)"
                )
            try:
                path = self.games.path_of(args[0])
                mesh.send_file(
                    self._active,
                    path,
                    "application/x-muninn-game",
                    name=os.path.basename(path),
                )
            except (OSError, ValueError, KeyError) as e:
                self.errorOccurred.emit(f"can't share: {e}")
        elif head == "find":
            if args and args[0] == "off":
                self.node.stop_finding()
                self.notify.emit("stopped looking for new people")
            else:
                mins = (
                    float(args[0]) if args and args[0].replace(".", "").isdigit() else 5
                )
                self.node.find_people(mins * 60)
                self.notify.emit(f"looking for new people for {mins:g} min")
            self.statusChanged.emit()
        elif head == "background":
            on = not args or args[0] != "off"
            self.node.storage.set_setting("background", "on" if on else "off")
            from . import tray

            if on and not tray.available():
                return self.errorOccurred.emit(
                    "no system tray here — Muninn quits when closed"
                )
            self.keepRunning = on and tray.available()
            QApplication.instance().setQuitOnLastWindowClosed(not self.keepRunning)
            self.notify.emit(
                "closing the window keeps Muninn running (tray)"
                if on
                else "closing the window quits Muninn"
            )
        elif head == "autostart":
            from . import tray

            on = not args or args[0] != "off"
            try:
                where = tray.set_autostart(on)
            except OSError as e:
                return self.errorOccurred.emit(f"autostart: {e}")
            self.notify.emit(f"start at login {'on' if on else 'off'} ({where})")
        elif head == "scanmode":
            self.notify.emit(
                ":find looks for new people for a while · :reconnect fast|saver"
            )
        elif head == "search":
            if not args:
                return self.errorOccurred.emit("usage: :search <words>")
            q = " ".join(args)
            rows = []
            for m in mesh.storage.search(q, 60):
                who = "you" if m.sender == mesh.node_id else mesh.display_name(m.sender)
                when = time.strftime("%d %b %H:%M", time.localtime(m.ts))
                text = m.text or m.meta.get("n", "")
                rows.append(
                    (
                        f"{who}: {text[:70]}",
                        f"{self._conv_title(m.conv)} · {when}",
                        m.conv,
                    )
                )
            self._info(f"search “{q}”", rows)
        elif head in ("clear", "forget", "block"):
            if head == "clear" or not args:
                if not self._active:
                    return self.errorOccurred.emit("open a conversation first")
                convs = [self._active]
            else:
                ids = [self._resolve_or_err(a) for a in args]
                if None in ids:
                    return
                convs = [dm_conv(i) for i in ids if i]
            if head != "clear" and not all(c.startswith("dm:") for c in convs):
                return self.errorOccurred.emit(
                    f":{head} is for people — use :clear for a room"
                )
            self.removeRequested.emit(convs, "delete" if head == "clear" else head)
        elif head == "unblock":
            hit = [
                b
                for b in mesh.blocked
                if args and (b == args[0].upper() or b.endswith(args[0].upper()))
            ]
            for b in hit:
                mesh.unblock(b)
            if hit:
                self.notify.emit("unblocked " + ", ".join(hit))
            else:
                self.errorOccurred.emit("usage: :unblock <id> (see :blocked)")
        elif head == "blocked":
            self._info(
                "blocked",
                [
                    self._item(b, "Enter to unblock", action="cmd", cmd=f"unblock {b}")
                    for b in sorted(mesh.blocked)
                ]
                or [("nobody is blocked", "")],
            )
        elif head in ("mute", "unmute"):
            if not self._active:
                return self.errorOccurred.emit("open a conversation first")
            self.mesh.set_muted(self._active, head == "mute")
            self.notify.emit(
                "notifications off for this chat"
                if head == "mute"
                else "notifications on"
            )
        elif head == "members":
            if gid is None or gid not in mesh.groups:
                return self.errorOccurred.emit("open a group first")
            g = mesh.groups[gid]
            rows = []
            for nid in sorted(g.members, key=lambda n: mesh.display_name(n).lower()):
                if nid == mesh.node_id:
                    rows.append(("you", "member", ""))
                    continue
                rows.append(
                    (
                        mesh.display_name(nid),
                        self.node.presence(nid)["text"],
                        dm_conv(nid),
                    )
                )
            rows.append(
                self._item("+ add people", ":add <name…>", action="cmd", cmd="add ")
            )
            self._info(f"{g.name} — {len(g.members)} members", rows)
        elif head == "whoami":
            st = self.node.status()
            self._info(
                "you",
                [
                    ("name", mesh.name or "(not set — :nick <name>)", ""),
                    ("id", mesh.node_id, ""),
                    (
                        "radio",
                        f"{self.transportName} {self.node.transport.local_addr()}",
                        "",
                    ),
                    (
                        "nearby",
                        f"{len(mesh.connected())} connected · {st['sighted']} devices seen",
                        "",
                    ),
                    (
                        "reconnect",
                        f"{self.node.reconnect.label} — {self.node.reconnect.description}",
                        "",
                    ),
                    (
                        "finding new people",
                        f"yes, {st['find_left']}s left"
                        if st["find_left"]
                        else (
                            "yes (you know nobody yet)"
                            if st["finding"]
                            else "no — :find"
                        ),
                        "",
                    ),
                ],
            )
        elif head == "reconnect":
            if args:
                try:
                    m = self.node.set_reconnect_mode(args[0].lower())
                except ValueError as e:
                    return self.errorOccurred.emit(str(e))
                return self.notify.emit(f"reconnect: {m.label} — {m.description}")
            self.node.poke()
            self.notify.emit("retrying all known devices")
        elif head == "connect":
            if args:
                self.pairDevice(args[0])
        elif head == "list":
            self._info(
                "conversations",
                [
                    (
                        it["displayName"],
                        f"{it['convType']} · {it['unreadCount']} unread"
                        if it["unreadCount"]
                        else it["convType"],
                        it["convId"],
                    )
                    for it in self._peers._items
                ],
            )
        elif head in ("peers", "known"):
            rows = []
            for p in sorted(mesh.active_peers(), key=lambda p: p.display.lower()):
                pres = self.node.presence(p.node_id)
                if head == "peers" and pres["state"] not in ("direct", "relay"):
                    continue
                rows.append(
                    (p.display, f"{pres['text']} · {p.node_id}", dm_conv(p.node_id))
                )
            if head == "known":
                for r in self.node.scan_results:
                    if r.muninn is False or r.addr in {
                        a for _, a, _ in self.node.storage.load_transports()
                    }:
                        continue
                    rows.append(
                        (
                            r.name or r.addr,
                            f"seen nearby ({r.kind}) · not introduced yet · {r.addr}",
                            "",
                        )
                    )
            self._info("in range" if head == "peers" else "everyone you know", rows)
        elif head == "status":
            st = mesh.status()
            rows = [
                (
                    "you",
                    f"{st['name'] or '(no name)'} · {st['node']} · "
                    f"{self.transportName} {self.node.transport.local_addr()}",
                    "",
                )
            ]
            for lk in st["links"]:
                rows.append(
                    (
                        mesh.display_name(lk["node"]),
                        f"link {lk['addr']} · rtt {lk['rtt_ms']} ms · idle {lk['idle']}s · "
                        f"↑{lk['out'] // 1024}K ↓{lk['in'] // 1024}K",
                        dm_conv(lk["node"]),
                    )
                )
            for d, vias in st["routes"].items():
                rows.append(
                    (
                        mesh.display_name(d),
                        "route "
                        + ", ".join(
                            f"via {mesh.display_name(v)} ({h})" for v, h in vias.items()
                        ),
                        dm_conv(d),
                    )
                )
            rows.append(
                (
                    "queue",
                    f"{st['outbox']} waiting for delivery · "
                    f"{len(st['downloads'])} downloads",
                    "",
                )
            )
            rows.append(
                ("stats", ", ".join(f"{k} {v}" for k, v in st["stats"].items()), "")
            )
            self._info("status", rows)
        elif head in ("help", "h", "?"):
            self._info("commands", [(a, b, "") for a, b in self._HELP])
        elif head == "about":
            try:
                from importlib.metadata import version

                ver = version("muninn")
            except Exception:
                ver = "dev"
            self._info(
                "about",
                [
                    ("Muninn", f"v{ver} — encrypted Bluetooth mesh chat", ""),
                    ("you", f"{mesh.name} · {mesh.node_id}", ""),
                    (
                        "transport",
                        f"{self.transportName} · {self.node.transport.local_addr()}",
                        "",
                    ),
                    ("license", "MIT — see LICENSE / THIRD_PARTY_LICENSES.md", ""),
                    self._item(
                        "github",
                        "github.com/LibertyLutherMoffitt/muninn",
                        action="url",
                        url="https://github.com/LibertyLutherMoffitt/muninn",
                    ),
                ],
            )
        else:
            self.errorOccurred.emit(f"unknown command: :{head}  (:help)")

    @Slot(int)
    def cycleConv(self, delta: int) -> None:
        ids = [it["convId"] for it in self._peers._items]
        if not ids:
            return
        i = ids.index(self._active) if self._active in ids else -1
        self.setActiveConv(ids[(i + delta) % len(ids)])

    @Slot(result=str)
    def firstConvId(self) -> str:
        return self._peers._items[0]["convId"] if self._peers._items else ""

    @Slot(result=list)
    def knownPeers(self) -> list:
        out = []
        for p in self.mesh.active_peers():
            nid = p.node_id
            h = self.mesh.hops(nid)
            out.append(
                {
                    "mac": nid,
                    "name": p.display,
                    "status": "direct" if h == 1 else ("relay" if h else "offline"),
                }
            )
        return out


def _thumbnail(path: str) -> bytes:
    """Small JPEG preview (≤ ~10 KB) so receivers see the picture instantly."""
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage

        img = QImage(path)
        if img.isNull():
            return b""
        img = img.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        for q in (70, 50, 30):
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "JPEG", q)  # ty: ignore[no-matching-overload]
            if ba.size() <= 10000:
                return bytes(ba.data())
    except Exception:
        pass
    return b""
