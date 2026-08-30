"""GUI entrypoint — QGuiApplication + QML engine + core init."""

from __future__ import annotations

import os
import pathlib
import sys
import threading

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

from muninn import bt
from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.groups import GroupStore
from muninn.discovery import acceptor, scanner
from muninn.peers import ConnectionManager
from muninn.storage import Storage

from .bridge import ChatBridge
from .models import MessageListModel, PeerListModel
from .vim import VimEditor
from .writer_lock import WriterLock

_QML_DIR = pathlib.Path(__file__).parent / "qml"

# Design tokens. Every colour, radius and type size in the QML comes from
# here — a literal in a .qml file is a bug, because it cannot be restyled and
# it drifts from whatever the rest of the app does.
_THEME = {
    # Surfaces, from furthest back to nearest front.
    "bg": "#0f1115",
    "surface": "#151820",
    "surfaceRaised": "#1b1f2a",
    "surfaceHover": "#232838",
    "border": "#262b38",
    # Text.
    "textPrimary": "#e5e7eb",
    "textMuted": "#9ca3af",
    "textFaint": "#6b7280",
    "onAccent": "#ffffff",
    # Brand.
    "accent": "#7c3aed",
    "accentMuted": "#3b2a6a",
    # Bubbles.
    "incomingBubble": "#1f2330",
    "outgoingBubble": "#3b2a6a",
    # Status. `warning` was previously hardcoded as #f59e0b in five places.
    "success": "#10b981",
    "warning": "#f59e0b",
    "error": "#ef4444",
    # Type scale.
    "fontTiny": 10,
    "fontSmall": 11,
    "fontBody": 13,
    "fontTitle": 15,
    "fontLarge": 20,
    # Spacing scale.
    "spaceXs": 4,
    "spaceSm": 8,
    "spaceMd": 12,
    "spaceLg": 16,
    "spaceXl": 24,
    # Radii.
    "radiusSm": 4,
    "radiusMd": 8,
    "radiusLg": 12,
    # Chrome.
    "sidebarWidth": 260,
    "headerHeight": 52,
    "statusBarHeight": 24,
}


def main() -> None:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Muninn")
    app.setOrganizationName("Muninn")

    # Default to JetBrains Mono everywhere; fall back to whatever the system
    # picks for the Monospace style hint if it isn't installed.
    default_font = QFont("JetBrains Mono")
    default_font.setStyleHint(QFont.StyleHint.Monospace)
    default_font.setPointSize(11)
    QGuiApplication.setFont(default_font)

    local_mac = bt.get_local_mac()
    storage = Storage()

    identity = storage.get_identity()
    if identity is None:
        private_key = generate_keypair()
        identity = storage.create_identity(bytes(private_key))
    else:
        private_key = privkey_from_bytes(identity.privkey)

    storage.save_peer_pubkey(local_mac, bytes(private_key.public_key))

    env_name = os.environ.get("MUNINN_NAME", "")
    display_name = env_name or identity.display_name

    group_store = GroupStore(storage=storage)
    conn_mgr = ConnectionManager(
        local_mac,
        private_key,
        group_store,
        display_name=display_name,
        storage=storage,
    )

    writer_lock = WriterLock()
    is_writer = writer_lock.try_acquire()

    peer_model = PeerListModel(group_store, conn_mgr, storage)
    msg_model = MessageListModel()
    bridge = ChatBridge(
        conn_mgr,
        group_store,
        storage,
        local_mac,
        is_writer,
        peer_model,
        msg_model,
    )
    vim = VimEditor()

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("peerModel", peer_model)
    ctx.setContextProperty("msgModel", msg_model)
    ctx.setContextProperty("vimEditor", vim)
    ctx.setContextProperty("Theme", _THEME)

    qml_file = _QML_DIR / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_file)))

    if not engine.rootObjects():
        sys.exit(1)

    stop = threading.Event()
    if is_writer:
        bt.create_server()
        threading.Thread(target=acceptor, args=(conn_mgr,), daemon=True).start()
        threading.Thread(
            target=scanner, args=(conn_mgr, local_mac, stop), daemon=True
        ).start()

    ret = app.exec()

    stop.set()
    if is_writer:
        bt.close_server()
    writer_lock.release()
    storage.close()
    sys.exit(ret)
