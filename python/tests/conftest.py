"""Shared fixtures.

Every test here runs without Bluetooth hardware. `muninn.bt` is never imported
by the modules under test (protocol/crypto/storage/groups/peers/presence), so
the suite runs on any platform.
"""

import socket
import sys
import threading
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from muninn import protocol  # noqa: E402
from muninn.crypto import generate_keypair  # noqa: E402
from muninn.groups import GroupStore  # noqa: E402
from muninn.peers import ConnectionManager  # noqa: E402
from muninn.storage import Storage  # noqa: E402


@pytest.fixture
def tmp_storage(tmp_path):
    store = Storage(tmp_path / "muninn.db")
    yield store
    store.close()


class Node:
    """One in-process Muninn peer, driven over a real socketpair.

    Wraps a ConnectionManager plus its own Storage so tests exercise the same
    code path the CLI and GUI use — handshake, framing, dedup, persistence —
    without any Bluetooth backend.
    """

    def __init__(self, mac: str, db_path: Path | None = None, name: str = ""):
        self.mac = mac.upper()
        self.storage = Storage(db_path) if db_path is not None else None
        self.key = generate_keypair()
        self.groups = GroupStore(storage=self.storage)
        if self.storage is not None:
            self.storage.create_identity(bytes(self.key))
            self.storage.save_peer_pubkey(self.mac, bytes(self.key.public_key))
        self.cm = ConnectionManager(
            self.mac, self.key, self.groups, display_name=name, storage=self.storage
        )
        self.messages: list[tuple[bytes, str, str, bytes]] = []
        self.acks: list[tuple[bytes, str]] = []
        self.reads: list[tuple[bytes, str]] = []
        self.profiles: list[tuple[str, str]] = []
        self.peer_changes: list[tuple[str, bool]] = []
        self.groups_setup: list = []
        self.cm.on_message = lambda g, s, t, m: self.messages.append((g, s, t, m))
        self.cm.on_ack = lambda m, f: self.acks.append((m, f))
        self.cm.on_read = lambda m, f: self.reads.append((m, f))
        self.cm.on_profile = lambda a, n: self.profiles.append((a, n))
        self.cm.on_peer_change = lambda a, c: self.peer_changes.append((a, c))
        self.cm.on_group_setup = self.groups_setup.append

    def close(self):
        for addr in list(self.cm.peers):
            self.cm.remove_peer(addr)
        if self.storage is not None:
            self.storage.close()


def link(a: Node, b: Node, timeout: float = 5.0) -> tuple:
    """Connect two Nodes over a socketpair and complete both handshakes.

    add_peer() blocks reading the peer's handshake, so both sides must run
    concurrently — exactly as they do in production (acceptor thread on one
    side, scanner thread on the other).
    """
    s1, s2 = socket.socketpair()
    results: dict[str, bool] = {}

    def run(node, sock, peer_mac, key):
        results[key] = node.cm.add_peer(sock, peer_mac)

    t1 = threading.Thread(target=run, args=(a, s1, b.mac, "a"))
    t2 = threading.Thread(target=run, args=(b, s2, a.mac, "b"))
    t1.start()
    t2.start()
    t1.join(timeout)
    t2.join(timeout)
    assert results.get("a") is True, "node A handshake failed"
    assert results.get("b") is True, "node B handshake failed"
    return s1, s2


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """Poll until predicate() is truthy. Returns False on timeout."""
    import time as _t

    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        if predicate():
            return True
        _t.sleep(interval)
    return predicate()


@pytest.fixture
def node_factory(tmp_path):
    made: list[Node] = []

    def make(mac: str, name: str = "", persist: bool = True) -> Node:
        db = tmp_path / f"{mac.replace(':', '')}.db" if persist else None
        node = Node(mac, db, name=name)
        made.append(node)
        return node

    yield make
    for n in made:
        n.close()


@pytest.fixture
def proto():
    return protocol


class RecordingSock:
    """Wraps a live socket and keeps every byte written through it.

    `socket.socket` attributes are read-only, so tests that need to inspect the
    wire substitute one of these into `ConnectionManager.peers[addr].sock` —
    which is duck-typed, not annotated to a concrete socket.
    """

    def __init__(self, sock):
        self._sock = sock
        self.written: list[bytes] = []

    def sendall(self, data):
        self.written.append(bytes(data))
        return self._sock.sendall(data)

    def wire(self) -> bytes:
        return b"".join(self.written)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def record_peer_wire(node, peer_addr: str) -> RecordingSock:
    """Start recording everything `node` writes to `peer_addr`."""
    peer = node.cm.peers[peer_addr]
    rec = RecordingSock(peer.sock)
    peer.sock = rec
    return rec


def drop_link(sock) -> None:
    """Simulate the peer's process going away.

    Closing an fd that another in-process thread is blocked reading does not
    deliver EOF to the far end — the blocked recv keeps it open. A half-close
    sends FIN immediately, which is what a real remote disconnect looks like.
    """
    import socket as _s

    try:
        sock.shutdown(_s.SHUT_RDWR)
    except OSError:
        pass
