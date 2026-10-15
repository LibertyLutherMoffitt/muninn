"""GUI entrypoint — QApplication + QML engine + Node."""

from __future__ import annotations

import os
import pathlib
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMessageBox

from muninn.node import Node, default_transport

from . import notifications
from .bridge import ChatBridge
from .models import MessageListModel, PeerListModel
from .vim import VimEditor
from .writer_lock import WriterLock

_QML_DIR = pathlib.Path(__file__).parent / "qml"

_THEME = {
    "bg": "#0f1115",
    "surface": "#151820",
    "surfaceRaised": "#1b1f2a",
    "textPrimary": "#e5e7eb",
    "textMuted": "#9ca3af",
    "accent": "#7c3aed",
    "accentSoft": "#2a1f4a",
    "incomingBubble": "#1f2330",
    "outgoingBubble": "#3b2a6a",
    "success": "#10b981",
    "warning": "#f59e0b",
    "error": "#ef4444",
}


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Muninn")
    app.setOrganizationName("Muninn")
    app.setQuitOnLastWindowClosed(True)
    font = QFont("JetBrains Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(11)
    app.setFont(font)

    lock = WriterLock()
    if not lock.try_acquire():
        QMessageBox.information(
            None,
            "Muninn",
            "Muninn is already running (only one instance can use Bluetooth).",
        )
        sys.exit(0)

    try:
        transport = default_transport()
        node = Node(transport, name=os.environ.get("MUNINN_NAME") or None)
    except Exception as e:
        QMessageBox.critical(None, "Muninn", f"Could not start Bluetooth:\n\n{e}")
        sys.exit(1)

    notifications.init(app, QIcon())
    peer_model = PeerListModel(node.mesh)
    msg_model = MessageListModel(node.mesh)
    bridge = ChatBridge(node, peer_model, msg_model)
    vim = VimEditor()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(
        lambda ws: [print("QML:", w.toString(), file=sys.stderr) for w in ws]
    )
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("peerModel", peer_model)
    ctx.setContextProperty("msgModel", msg_model)
    ctx.setContextProperty("vimEditor", vim)
    ctx.setContextProperty("Theme", _THEME)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        sys.exit(1)

    node.start()
    ret = app.exec()
    node.stop()
    lock.release()
    sys.exit(ret)


if __name__ == "__main__":
    main()
