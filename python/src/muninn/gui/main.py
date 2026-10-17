"""GUI entrypoint — QApplication + QML engine + Node."""

from __future__ import annotations

import os
import pathlib
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QOpenGLContext
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtWidgets import QApplication, QMessageBox

from muninn.instance import AlreadyRunning
from muninn.node import Node, default_transport

from . import notifications, tray
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
    app.setWindowIcon(tray.icon())
    background_start = "--background" in sys.argv[1:]
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

    notifications.init(app, tray.icon())
    peer_model = PeerListModel(node.mesh, node.presence)
    msg_model = MessageListModel(node.mesh)
    bridge = ChatBridge(node, peer_model, msg_model)
    # Keep running in the tray when the window closes — only if a tray exists.
    keep = tray.available() and node.storage.get_setting("background", "on") != "off"
    bridge.keepRunning = keep
    if keep:
        app.setQuitOnLastWindowClosed(False)
    vim = VimEditor()

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("peerModel", peer_model)
    ctx.setContextProperty("msgModel", msg_model)
    ctx.setContextProperty("vimEditor", vim)
    ctx.setContextProperty("Theme", _THEME)
    engine.load(QUrl.fromLocalFile(str(_QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        sys.exit(1)
    window = engine.rootObjects()[0]
    tray_icon = None
    if keep:
        tray_icon = tray.Tray(app, window, node, bridge.runCommand)
        notifications.use_tray(tray_icon)
        bridge.trayShowRequested.connect(tray_icon.show_window)
    if background_start and keep:
        window.hide()
    else:
        window.show()

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
