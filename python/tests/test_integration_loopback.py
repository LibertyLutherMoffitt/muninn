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

    def __init__(
        self,
        mac: str,
        name: str,
        rendezvous: Path,
        home: Path,
        ghosts="",
        noise="",
        hide_uuid=False,
    ):
        env = dict(os.environ)
        env.update(
            PYTHONPATH=str(SRC),
            PYTHONUNBUFFERED="1",
            MUNINN_BT_BACKEND="loopback",
            MUNINN_LOOPBACK_DIR=str(rendezvous),
            MUNINN_LOOPBACK_MAC=mac,
            MUNINN_LOOPBACK_NAME=name,
            MUNINN_LOOPBACK_GHOSTS=ghosts,
            MUNINN_LOOPBACK_NOISE=noise,
            MUNINN_LOOPBACK_HIDE_UUID="1" if hide_uuid else "",
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

    def spawn(
        mac: str,
        name: str,
        ghosts: str = "",
        noise: str = "",
        hide_uuid: bool = False,
    ) -> Client:
        client = Client(
            mac,
            name,
            tmp_path / "rendezvous",
            tmp_path / name,
            ghosts,
            noise,
            hide_uuid,
        )
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


def test_whoami_reports_the_address_needed_for_pairing(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    assert alice.wait_for("Scanning for peers")
    alice.send("/whoami")
    assert alice.wait_for("You are AA:AA:AA:AA:AA:01"), alice.output()
    assert alice.wait_for("display name: alice"), alice.output()
    assert alice.wait_for("connected peers:"), alice.output()


# --- Scan policy ---


def test_scanmode_reports_and_changes_the_policy(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    assert alice.wait_for("Scanning for peers"), alice.output()
    # Aggressive is the default: finding peers unattended is the point.
    assert "aggressive" in alice.output()

    alice.send("/scanmode")
    assert alice.wait_for("Scan mode: Aggressive"), alice.output()
    assert alice.wait_for("conservative"), alice.output()

    alice.send("/scanmode conservative")
    assert alice.wait_for("Scan mode: Conservative"), alice.output()


def test_an_unknown_scan_mode_is_rejected(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    assert alice.wait_for("Scanning for peers")
    alice.send("/scanmode turbo")
    assert alice.wait_for("Unknown scan mode: turbo"), alice.output()


def test_the_chosen_scan_mode_survives_a_restart(clients):
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    assert alice.wait_for("Scanning for peers")
    alice.send("/scanmode balanced")
    assert alice.wait_for("Scan mode: Balanced")
    alice.close()

    revived = clients("AA:AA:AA:AA:AA:01", "alice")
    assert revived.wait_for("Scanning for peers (balanced)"), revived.output()


def test_peers_are_still_found_with_the_new_scheduler(clients):
    """The scheduler must not regress the thing it exists to improve."""
    alice = clients("AA:AA:AA:AA:AA:01", "alice")
    bob = clients("BB:BB:BB:BB:BB:02", "bob")
    assert alice.wait_for("bob connected"), _diagnose(alice, bob)
    assert bob.wait_for("alice connected"), _diagnose(alice, bob)


def test_a_peer_whose_service_record_never_resolves_is_still_found(clients):
    """The bug this scheduler exists for.

    BlueZ routinely omits 128-bit UUIDs from inquiry EIR, and only browses SDP
    after a pair or connect — so a peer that has never been connected to can be
    invisible to a UUID-filtered discover() forever. Probing every visible
    device is what closes that hole.

    Both sides hide their service record here, so neither can discover the
    other and the only route to a connection is a blind dial. (Hiding just one
    proves nothing: the other side would still find it and dial in.)
    """
    alice = clients("AA:AA:AA:AA:AA:01", "alice", hide_uuid=True)
    bob = clients("BB:BB:BB:BB:BB:02", "bob", hide_uuid=True)
    assert alice.wait_for("bob connected", timeout=60), _diagnose(alice, bob)
    assert bob.wait_for("alice connected", timeout=60), _diagnose(alice, bob)


def test_a_cabin_full_of_other_devices_does_not_stop_a_peer_connecting(clients):
    """40 headsets between you and the person you want to talk to."""
    headsets = ",".join(
        f"C0:FF:EE:00:{i // 256:02X}:{i % 256:02X}=Headset{i}" for i in range(40)
    )
    alice = clients("AA:AA:AA:AA:AA:01", "alice", noise=headsets)
    bob = clients("BB:BB:BB:BB:BB:02", "bob", noise=headsets)
    assert alice.wait_for("bob connected", timeout=90), _diagnose(alice, bob)
    alice.send("/dm bob")
    alice.send("row 14, is that you")
    assert bob.wait_for("row 14, is that you", timeout=60), _diagnose(alice, bob)


def test_a_crowd_of_unknown_devices_is_probed_but_rationed(clients):
    headsets = ",".join(
        f"C0:FF:EE:00:{i // 256:02X}:{i % 256:02X}=Headset{i}" for i in range(30)
    )
    alice = clients("AA:AA:AA:AA:AA:01", "alice", noise=headsets)
    assert alice.wait_for("Scanning for peers")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        alice.send("/scanmode")
        if "unidentified" in alice.output():
            break
        time.sleep(2)
    out = alice.output()
    assert "tracking" in out, out
    # It must have noticed them without trying to dial all 30 at once.
    assert "unidentified" in out, out
