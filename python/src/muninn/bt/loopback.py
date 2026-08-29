"""Loopback Bluetooth backend — real sockets, no radio.

Selected with `MUNINN_BT_BACKEND=loopback`. Implements the same surface as
`bluez.py` and `winrt.py` but carries frames over TCP on 127.0.0.1, so two or
more Muninn clients can talk to each other on one machine.

Why this exists:

* **Running the app at all.** BlueZ needs a D-Bus system bus and a real
  adapter; WinRT needs Windows. Neither is available in CI, in a container, or
  on a dev machine without a spare second device. With this backend the CLI and
  the Qt GUI both start and hold a real conversation.
* **Testing the parts hardware hides.** Discovery, the MAC tiebreak, relay
  through a third instance, reconnect-and-resend — none of that is reachable
  from a unit test that stops at `ConnectionManager`.
* **Demonstrating presence.** `MUNINN_LOOPBACK_GHOSTS` fabricates devices that
  show up in scans and refuse every connection, which is exactly the
  "nearby but can't connect" case that is otherwise awkward to reproduce.

Instances find each other through a rendezvous directory
(`MUNINN_LOOPBACK_DIR`, default `$TMPDIR/muninn-loopback`): each running client
writes `<MAC>.json` describing its TCP port while its server is up.

Environment:
    MUNINN_BT_BACKEND=loopback   select this backend
    MUNINN_LOOPBACK_MAC          this instance's address (default: derived from pid)
    MUNINN_LOOPBACK_NAME         device name shown to peers in scans
    MUNINN_LOOPBACK_DIR          rendezvous directory
    MUNINN_LOOPBACK_GHOSTS       "MAC=Name,MAC=Name" — visible, unconnectable
    MUNINN_LOOPBACK_PAIRING      set to 1 to require ensure_paired() first
"""

import json
import os
import queue
import socket
import tempfile
import threading
import time
from pathlib import Path

SERVICE_UUID = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
SERVICE_NAME = "Muninn"

_incoming: queue.Queue = queue.Queue()
_server: socket.socket | None = None
_accept_thread: threading.Thread | None = None
_stop = threading.Event()
_registered: Path | None = None
_paired: set[str] = set()
_paired_lock = threading.Lock()


# --- Identity / rendezvous ---


def _rendezvous_dir() -> Path:
    path = Path(
        os.environ.get(
            "MUNINN_LOOPBACK_DIR", Path(tempfile.gettempdir()) / "muninn-loopback"
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_local_mac() -> str:
    """This instance's address.

    Defaults to a locally-administered address derived from the pid so two
    clients started without configuration still get distinct identities.
    """
    configured = os.environ.get("MUNINN_LOOPBACK_MAC")
    if configured:
        return configured.upper()
    pid = os.getpid() & 0xFFFFFFFF
    octets = [0x02, 0x00, (pid >> 24) & 0xFF, (pid >> 16) & 0xFF, (pid >> 8) & 0xFF, pid & 0xFF]
    return ":".join(f"{o:02X}" for o in octets)


def _device_name() -> str:
    return os.environ.get("MUNINN_LOOPBACK_NAME") or f"muninn-{os.getpid()}"


def _record_path(mac: str) -> Path:
    return _rendezvous_dir() / f"{mac.upper().replace(':', '-')}.json"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return True  # unknown provenance — let the connect attempt decide
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_records() -> list[dict]:
    """Every live instance's record. Prunes ones whose process is gone."""
    records = []
    for path in _rendezvous_dir().glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not _alive(int(record.get("pid", 0))):
            path.unlink(missing_ok=True)
            continue
        records.append(record)
    return records


def _ghosts() -> list[tuple[str, str]]:
    """Devices that appear in scans but refuse every connection.

    Fabricated on purpose: this is how a peer with Bluetooth off, or one out of
    RFCOMM range but inside inquiry range, presents itself.
    """
    raw = os.environ.get("MUNINN_LOOPBACK_GHOSTS", "").strip()
    if not raw:
        return []
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        mac, _, name = entry.partition("=")
        out.append((mac.strip().upper(), name.strip() or mac.strip().upper()))
    return out


def _is_ghost(addr: str) -> bool:
    return any(mac == addr.upper() for mac, _ in _ghosts())


# --- Server ---


def create_server() -> None:
    """Listen on an ephemeral loopback port and publish a rendezvous record."""
    global _server, _accept_thread, _registered
    if _server is not None:
        return
    _stop.clear()
    _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server.bind(("127.0.0.1", 0))
    _server.listen(8)
    port = _server.getsockname()[1]

    mac = get_local_mac()
    _registered = _record_path(mac)
    _registered.write_text(
        json.dumps(
            {
                "mac": mac,
                "name": _device_name(),
                "port": port,
                "pid": os.getpid(),
                "uuid": SERVICE_UUID,
                "started": int(time.time()),
            }
        )
    )

    def accept_loop() -> None:
        while not _stop.is_set():
            try:
                conn, _peer = _server.accept()
            except OSError:
                break
            # The dialling side announces its address first so we can report a
            # peer address the way a real backend does. The handshake that
            # follows is what actually establishes identity.
            try:
                conn.settimeout(10)
                raw = b""
                while not raw.endswith(b"\n") and len(raw) < 64:
                    chunk = conn.recv(1)
                    if not chunk:
                        break
                    raw += chunk
                conn.settimeout(None)
            except OSError:
                conn.close()
                continue
            addr = raw.decode("ascii", "replace").strip().upper() or "00:00:00:00:00:00"
            _incoming.put((conn, addr))

    _accept_thread = threading.Thread(target=accept_loop, daemon=True)
    _accept_thread.start()


def close_server() -> None:
    global _server
    _stop.set()
    if _registered is not None:
        _registered.unlink(missing_ok=True)
    if _server is not None:
        try:
            _server.close()
        except OSError:
            pass
        _server = None
    _incoming.put((None, None))


def accept() -> tuple:
    sock, addr = _incoming.get()
    if sock is None:
        raise ConnectionError("Server closed")
    return sock, addr


# --- Discovery ---


def discover() -> list[tuple[str, str]]:
    """Peers advertising the Muninn service.

    Ghosts are included: they advertise the service but refuse every RFCOMM
    connection, which is what a peer with a stale link key or a busy radio
    looks like. Being dialled and failing is the whole point — that is how the
    presence tracker learns "nearby, can't connect".
    """
    me = get_local_mac()
    live = [
        (r["mac"].upper(), r.get("name") or r["mac"])
        for r in _read_records()
        if r["mac"].upper() != me and r.get("uuid") == SERVICE_UUID
    ]
    return live + [g for g in _ghosts() if g[0] != me]


def scan_devices(duration: float = 10.0, quiet: bool = False) -> list[tuple[str, str]]:
    """A general inquiry: every Muninn instance plus any configured ghosts.

    Mirrors a real scan, which returns every visible radio — not just the ones
    running Muninn.
    """
    if not quiet:
        print(f"Scanning for nearby Bluetooth devices ({duration:.0f}s)...")
    # A real inquiry takes seconds; keep a token delay so callers that assume
    # scanning is slow behave the same here, but never block a test for long.
    time.sleep(min(float(duration), 0.2))
    found = discover()
    seen: set[str] = set()
    out = []
    for mac, name in found:
        if mac not in seen:
            seen.add(mac)
            out.append((mac, name))
    return out


# --- Pairing ---


def is_paired(addr: str) -> bool:
    if os.environ.get("MUNINN_LOOPBACK_PAIRING") != "1":
        return True
    with _paired_lock:
        return addr.upper() in _paired


def pair(addr: str) -> None:
    """Simulated pairing. Ghosts refuse, as an unreachable device would."""
    addr = addr.upper()
    if _is_ghost(addr):
        raise ConnectionError(f"Pairing failed: {addr} is not responding")
    with _paired_lock:
        _paired.add(addr)


def ensure_paired(addr: str) -> None:
    if not is_paired(addr):
        pair(addr)


# --- Connect ---


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def should_keep_outgoing(local_mac: str, peer_mac: str) -> bool:
    """Lower MAC keeps its outgoing socket (higher MAC's outgoing is dropped)."""
    return mac_to_int(local_mac) < mac_to_int(peer_mac)


def set_discoverable(enabled: bool) -> None:
    """No-op — a loopback instance is discoverable whenever its server is up."""
    return


def connect(addr: str) -> tuple:
    addr = addr.upper()
    if _is_ghost(addr):
        # What a real stack reports for a device that answers inquiry but not
        # RFCOMM. Drives the "nearby, can't connect" presence state.
        raise ConnectionError(f"Connect failed: {addr} refused (br-connection-refused)")
    record = next((r for r in _read_records() if r["mac"].upper() == addr), None)
    if record is None:
        raise ConnectionError(f"No Muninn service advertised by {addr}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(("127.0.0.1", int(record["port"])))
        # Announce who we are so the accepting side can name the peer, the way
        # a real backend learns it from the link layer.
        sock.sendall((get_local_mac() + "\n").encode("ascii"))
        sock.settimeout(None)
    except OSError as e:
        sock.close()
        raise ConnectionError(f"Connect failed: {e}") from e
    return sock, addr
