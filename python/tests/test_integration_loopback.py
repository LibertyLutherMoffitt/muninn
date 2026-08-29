"""Full-stack integration: real CLI processes talking over the loopback backend.

Everything else in this suite exercises modules in-process. These tests start
the actual CLI entry point — backend dispatch, radio-free server, acceptor
thread, scanner, discovery, pairing, handshake, SQLite, the readline UI — and
drive it through stdin exactly as a user would.

That covers the seam the unit tests cannot reach: the wiring between the
Bluetooth backend and ConnectionManager, which is where most real breakage has
historically lived.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
GHOST_MAC = "DE:AD:BE:EF:00:01"
GHOST_NAME = "Phantom Pixel"


class Client:
    """A Muninn CLI subprocess with line-buffered capture."""

    def __init__(self, mac: str, name: str, rendezvous: Path, home: Path, ghosts=""):
        env = dict(os.environ)
        env.update(
            PYTHONPATH=str(SRC),
            PYTHONUNBUFFERED="1",
            MUNINN_BT_BACKEND="loopback",
            MUNINN_LOOPBACK_DIR=str(rendezvous),
            MUNINN_LOOPBACK_MAC=mac,
            MUNINN_LOOPBACK_NAME=name,
            MUNINN_LOOPBACK_GHOSTS=ghosts,
            MUNINN_NAME=name,
            XDG_DATA_HOME=str(home),
            TERM="dumb",
        )
        home.mkdir(parents=True, exist_ok=True)
        self.mac = mac.upper()
        self.name = name
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "muninn.cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            with self._lock:
                self.lines.append(raw.rstrip("\n"))

    def send(self, line: str) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def output(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def wait_for(self, needle: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output():
                return True
            if self.proc.poll() is not None:
                return needle in self.output()
            time.sleep(0.05)
        return needle in self.output()

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass


@pytest.fixture
def clients(tmp_path):
    made: list[Client] = []

    def spawn(mac: str, name: str, ghosts: str = "") -> Client:
        client = Client(mac, name, tmp_path / "rendezvous", tmp_path / name, ghosts)
        made.append(client)
        return client

    yield spawn
    for c in made:
        c.close()


def _diagnose(*cs: Client) -> str:
    return "\n\n".join(f"--- {c.name} ---\n{c.output()}" for c in cs)


@pytest.fixture
def pair(clients):
    """Two clients that have found each other and completed a handshake."""
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    bob = clients("BB:BB:BB:BB:BB:02", "bob")
    assert alice.wait_for("bob connected"), _diagnose(alice, bob)
    assert bob.wait_for("alice connected"), _diagnose(alice, bob)
    return alice, bob


# --- Startup ---


def test_a_client_starts_and_reports_its_identity(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    assert alice.wait_for("Local MAC: AA:AA:AA:AA:AA:01"), alice.output()
    assert alice.wait_for("Display name: alice"), alice.output()


def test_two_clients_discover_each_other_with_no_configuration(pair):
    alice, bob = pair
    # Neither was told the other's address; the scanner found it.
    assert "bob connected" in alice.output()
    assert "alice connected" in bob.output()


# --- Messaging ---


def test_a_message_typed_into_one_client_appears_in_the_other(pair):
    alice, bob = pair
    alice.send("/dm bob")
    assert alice.wait_for("Switched to DM with bob")
    alice.send("hello from seat 14C")
    assert bob.wait_for("hello from seat 14C"), _diagnose(alice, bob)
    assert "[DM:alice] < hello from seat 14C" in bob.output()


def test_replies_flow_back(pair):
    alice, bob = pair
    alice.send("/dm bob")
    alice.send("ping")
    assert bob.wait_for("ping")
    bob.send("/dm alice")
    assert bob.wait_for("Switched to DM with alice")
    bob.send("pong")
    assert alice.wait_for("pong"), _diagnose(alice, bob)


def test_a_delivered_message_is_acknowledged(pair):
    alice, bob = pair
    alice.send("/dm bob")
    alice.send("did this land")
    assert bob.wait_for("did this land")
    # ChatUI renders an ACK as a right-aligned single check.
    assert alice.wait_for("✓"), _diagnose(alice, bob)


def test_a_read_receipt_comes_back_when_the_peer_is_on_the_conversation(pair):
    alice, bob = pair
    bob.send("/dm alice")
    assert bob.wait_for("Switched to DM with alice")
    alice.send("/dm bob")
    alice.send("are you reading this")
    assert bob.wait_for("are you reading this")
    assert alice.wait_for("✓✓"), _diagnose(alice, bob)


def test_unicode_survives_the_full_stack(pair):
    alice, bob = pair
    alice.send("/dm bob")
    alice.send("wheels up \U0001f6eb — see you in Osló")
    assert bob.wait_for("wheels up \U0001f6eb"), _diagnose(alice, bob)


# --- Names ---


def test_a_name_change_propagates_to_the_peer(pair):
    alice, bob = pair
    alice.send("/nick Skipper")
    assert bob.wait_for("is now known as Skipper"), _diagnose(alice, bob)
    bob.send("/dm Skipper")
    assert bob.wait_for("Switched to DM with Skipper")


def test_a_local_override_renames_a_peer_only_locally(pair):
    alice, bob = pair
    alice.send("/nick bob Copilot")
    assert alice.wait_for("Local override")
    alice.send("/peers")
    assert alice.wait_for("Copilot")
    assert "Copilot" not in bob.output()


# --- Presence ---


def test_peers_reports_a_live_connection(pair):
    alice, bob = pair
    alice.send("/peers")
    assert alice.wait_for("Connected:"), alice.output()
    assert alice.wait_for("bob"), alice.output()
    assert "connected" in alice.output()


def test_a_device_that_refuses_connections_shows_as_nearby_not_offline(clients):
    """The headline presence case: visible to the radio, unreachable anyway."""
    alice = clients("AA:AA:AA:AA:AA:01", "alice", ghosts=f"{GHOST_MAC}={GHOST_NAME}")
    assert alice.wait_for("Scanning for peers"), alice.output()
    # The scanner needs a couple of cycles to accumulate failed dials.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        alice.send("/known")
        if "can't connect" in alice.output():
            break
        time.sleep(3)
    out = alice.output()
    assert GHOST_MAC in out, out
    assert "Nearby:" in out, out
    assert "can't connect" in out, out


def test_a_disconnected_peer_is_remembered_as_recently_seen(pair):
    alice, bob = pair
    bob.close()
    assert alice.wait_for("bob disconnected"), alice.output()
    # Wait on something only /known emits — the peer's MAC already appears in
    # earlier output, so matching that would snapshot before the command ran.
    alice.send("/known")
    assert alice.wait_for("Nearby:") or alice.wait_for("Not in range:"), alice.output()
    out = alice.output()
    assert "BB:BB:BB:BB:BB:02" in out or "bob" in out, out


# --- Persistence ---


def test_history_survives_a_restart(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    bob = clients("BB:BB:BB:BB:BB:02", "bob")
    assert alice.wait_for("bob connected"), _diagnose(alice, bob)
    alice.send("/dm bob")
    alice.send("remember this line")
    assert bob.wait_for("remember this line"), _diagnose(alice, bob)
    bob.close()

    revived = clients("BB:BB:BB:BB:BB:02", "bob")
    assert revived.wait_for("alice connected"), revived.output()
    revived.send("/dm alice")
    assert revived.wait_for("remember this line"), revived.output()
    assert "previous message" in revived.output()


def test_a_message_sent_while_the_peer_is_away_is_delivered_on_reconnect(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    bob = clients("BB:BB:BB:BB:BB:02", "bob")
    assert alice.wait_for("bob connected"), _diagnose(alice, bob)
    alice.send("/dm bob")
    assert alice.wait_for("Switched to DM with bob")
    bob.close()
    assert alice.wait_for("bob disconnected"), alice.output()

    alice.send("sent while you were gone")
    time.sleep(1)
    revived = clients("BB:BB:BB:BB:BB:02", "bob")
    assert revived.wait_for("sent while you were gone", timeout=90), revived.output()
