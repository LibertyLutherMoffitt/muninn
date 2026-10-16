"""GUI entrypoint — QApplication + QML engine + Node."""

from __future__ import annotations

import os
import pathlib
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QIcon, QOpenGLContext
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWidgets import QApplication, QMessageBox

from muninn.instance import AlreadyRunning
from muninn.node import Node, default_transport

from . import notifications
from .bridge import ChatBridge
from .models import MessageListModel, PeerListModel
from .vim import VimEditor

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


def _ensure_renderer() -> None:
    """Fall back to Qt Quick's software renderer when OpenGL is unusable.

    Happens e.g. on NixOS when the app's Mesa differs from the system driver
    (`nix run` from a flake pinned to another nixpkgs), or over plain X
    forwarding. A chat window doesn't need a GPU; it must just open.
    """
    if os.environ.get("QT_QUICK_BACKEND") or os.environ.get("QSG_RHI_BACKEND"):
        return
    ctx = QOpenGLContext()
    if not ctx.create():
        print(
            "muninn: OpenGL unavailable, using the software renderer",
            file=sys.stderr,
        )
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


def main() -> None:
    app = QApplication(sys.argv)
    _ensure_renderer()
    app.setApplicationName("Muninn")
    app.setOrganizationName("Muninn")
    app.setQuitOnLastWindowClosed(True)
    font = QFont("JetBrains Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(11)
    app.setFont(font)

    try:
        node = Node(default_transport(), name=os.environ.get("MUNINN_NAME") or None)
    except AlreadyRunning as e:
        QMessageBox.information(None, "Muninn", str(e))
        sys.exit(0)
    except Exception as e:
        QMessageBox.critical(None, "Muninn", f"Could not start Muninn:\n\n{e}")
        sys.exit(1)

    notifications.init(app, QIcon())
    peer_model = PeerListModel(node.mesh, node.presence)
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

    try:
        node.start()
    except Exception as e:
        QMessageBox.critical(None, "Muninn", f"Could not start Bluetooth:\n\n{e}")
        node.stop()
        sys.exit(1)
    ret = app.exec()
    node.stop()
    sys.exit(ret)


if __name__ == "__main__":
    main()
