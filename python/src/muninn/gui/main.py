"""GUI entrypoint — QGuiApplication + QML engine + core init."""

from __future__ import annotations

import os
import pathlib
import sys
import threading

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

from muninn import bt
from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.groups import GroupStore
from muninn.peers import ConnectionManager
from muninn.storage import Storage

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
    "incomingBubble": "#1f2330",
    "outgoingBubble": "#3b2a6a",
    "success": "#10b981",
    "error": "#ef4444",
}


def _acceptor(conn_mgr: ConnectionManager) -> None:
    while True:
        try:
            sock, addr = bt.accept()
            conn_mgr.add_peer(sock, addr)
        except ConnectionError:
            break


def _scanner(
    conn_mgr: ConnectionManager, local_mac: str, stop: threading.Event
) -> None:
    import time

    try:
        bt.scan_devices(duration=5)
    except Exception:
        pass

    deferred: dict[str, float] = {}
    cycles = 0

    while not stop.is_set():
        cycles += 1
        if cycles % 8 == 0:
            try:
                bt.scan_devices(duration=5, quiet=True)
            except Exception:
                pass
        try:
            services = bt.discover()
        except Exception:
            services = []

        for addr, _name in services:
            addr = addr.upper()
            if addr == local_mac:
                continue
            with conn_mgr.peers_lock:
                if addr in conn_mgr.peers:
                    deferred.pop(addr, None)
                    continue
            if not bt.should_keep_outgoing(local_mac, addr):
                if addr not in deferred:
                    deferred[addr] = time.time()
                    continue
                if time.time() - deferred[addr] < 10:
                    continue
            deferred.pop(addr, None)
            try:
                bt.ensure_paired(addr)
                sock, peer_addr = bt.connect(addr)
                conn_mgr.add_peer(sock, peer_addr)
            except (ConnectionError, OSError):
                pass

        stop.wait(15)


def main() -> None:
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Muninn")
    app.setOrganizationName("Muninn")

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

    peer_model = PeerListModel(group_store, conn_mgr)
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
        threading.Thread(target=_acceptor, args=(conn_mgr,), daemon=True).start()
        threading.Thread(
            target=_scanner, args=(conn_mgr, local_mac, stop), daemon=True
        ).start()

    ret = app.exec()

    stop.set()
    if is_writer:
        bt.close_server()
    writer_lock.release()
    storage.close()
    sys.exit(ret)
