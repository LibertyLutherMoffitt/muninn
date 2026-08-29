"""Peer presence — is this device connected, nearby, relayed, or gone?

The connection state a user cares about is richer than "socket open y/n". On a
flight you want to distinguish:

  * **connected**   — a live encrypted session; messages go out now.
  * **relay**       — not directly connected, but reachable through a peer that is.
  * **nearby**      — the radio can see the device but we cannot establish a
                      session (out of range for RFCOMM, Bluetooth busy, the
                      peer isn't running Muninn, pairing was refused).
  * **offline**     — known peer, not seen recently.

`nearby` is the interesting one, and the reason this module exists: without it
a peer that is sitting two seats away with Bluetooth off looks identical to one
that is three time zones away.

Sightings and connections are also written through to `Storage`, so the peer
list can say "last seen 20 minutes ago" the instant the app opens, before any
scan has completed.

Thread safety: every public method takes `_lock`. The scanner thread records
sightings and dial failures while the recv threads record connect/disconnect.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from muninn.storage import Storage

CONNECTED = "connected"
RELAY = "relay"
NEARBY = "nearby"
OFFLINE = "offline"

# How long after a sighting we still call a device "nearby". Two scan cycles
# plus slack: the scanner sweeps every ~15s and re-inquires every ~2 min, so a
# device present but unconnectable keeps refreshing well inside this window.
NEARBY_WINDOW = 180.0

# Consecutive failed dials before we describe a nearby peer as unreachable
# rather than merely "not connected yet". One failure is normal — RFCOMM
# connects race the peer's own outgoing attempt all the time.
UNREACHABLE_AFTER = 2


@dataclass
class PeerStatus:
    """A peer's connectivity as the UI should present it."""

    addr: str
    state: str = OFFLINE
    last_seen: float | None = None
    last_connected: float | None = None
    via: str | None = None
    rssi: int | None = None
    failed_dials: int = 0
    last_error: str | None = None

    @property
    def is_reachable(self) -> bool:
        """True when a message sent right now has a path to this peer."""
        return self.state in (CONNECTED, RELAY)

    @property
    def unreachable_nearby(self) -> bool:
        """Visible to the radio, but we cannot get a session up.

        This is the state worth surfacing prominently — it usually means the
        peer needs to open the app, enable Bluetooth, or accept pairing.
        """
        return self.state == NEARBY and self.failed_dials >= UNREACHABLE_AFTER

    def seconds_since_seen(self, now: float | None = None) -> float | None:
        if self.last_seen is None:
            return None
        return max(0.0, (now if now is not None else time.time()) - self.last_seen)

    def describe(self, now: float | None = None) -> str:
        """One-line human summary, as shown in the CLI and GUI peer lists."""
        if self.state == CONNECTED:
            return "connected"
        if self.state == RELAY:
            return f"via {self.via}" if self.via else "via relay"
        ago = self.seconds_since_seen(now)
        if self.state == NEARBY:
            detail = "nearby, can't connect" if self.unreachable_nearby else "nearby"
            return f"{detail} · seen {format_ago(ago)}" if ago is not None else detail
        if ago is None:
            return "never seen"
        return f"last seen {format_ago(ago)}"


def format_ago(seconds: float | None) -> str:
    """Compact relative time: "just now", "4m ago", "3h ago", "2d ago"."""
    if seconds is None:
        return "never"
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


class PresenceTracker:
    """Aggregates radio sightings, dial outcomes and live sessions.

    Fed by the scanner (sightings, dial failures) and by ConnectionManager
    callbacks (connect/disconnect). Read by the UIs.
    """

    def __init__(self, storage: "Storage | None" = None, local_mac: str = ""):
        self.storage = storage
        self.local_mac = local_mac.upper()
        self._lock = threading.RLock()
        self._peers: dict[str, PeerStatus] = {}
        # Called with (addr) whenever a peer's presentation could have changed,
        # so a UI can refresh just that row instead of polling.
        self.on_change = None

        if storage is not None:
            for mac, (last_seen, last_connected) in storage.load_presence().items():
                if mac == self.local_mac:
                    continue
                self._peers[mac] = PeerStatus(
                    addr=mac,
                    state=OFFLINE,
                    last_seen=float(last_seen) if last_seen else None,
                    last_connected=float(last_connected) if last_connected else None,
                )

    # --- Internal ---

    def _entry(self, addr: str) -> PeerStatus:
        status = self._peers.get(addr)
        if status is None:
            status = PeerStatus(addr=addr)
            self._peers[addr] = status
        return status

    def _notify(self, addr: str) -> None:
        if self.on_change is not None:
            try:
                self.on_change(addr)
            except Exception:
                # A UI callback must never take down the scanner or a recv loop.
                pass

    # --- Feeds ---

    def record_sighting(self, addr: str, rssi: int | None = None) -> None:
        """The radio saw this device. Says nothing about whether we can talk."""
        addr = addr.upper()
        if addr == self.local_mac:
            return
        now = time.time()
        with self._lock:
            status = self._entry(addr)
            status.last_seen = now
            if rssi is not None:
                status.rssi = rssi
            if status.state == OFFLINE:
                status.state = NEARBY
        if self.storage is not None:
            self.storage.record_sighting(addr, int(now))
        self._notify(addr)

    def record_connected(self, addr: str) -> None:
        addr = addr.upper()
        now = time.time()
        with self._lock:
            status = self._entry(addr)
            status.state = CONNECTED
            status.last_seen = now
            status.last_connected = now
            status.via = None
            status.failed_dials = 0
            status.last_error = None
        if self.storage is not None:
            self.storage.record_connection(addr, int(now))
        self._notify(addr)

    def record_disconnected(self, addr: str) -> None:
        """A session ended. The device was here a moment ago, so it drops to
        `nearby` rather than straight to `offline`; the next scan decides."""
        addr = addr.upper()
        with self._lock:
            status = self._entry(addr)
            if status.state == CONNECTED:
                status.state = NEARBY
                status.last_seen = time.time()
        self._notify(addr)

    def record_dial_failure(self, addr: str, error: str = "") -> None:
        """A connect attempt to a device we can see failed."""
        addr = addr.upper()
        now = time.time()
        with self._lock:
            status = self._entry(addr)
            status.failed_dials += 1
            status.last_error = error or None
            # We only get here having just seen it in a scan.
            status.last_seen = now
            if status.state != CONNECTED:
                status.state = NEARBY
        if self.storage is not None:
            self.storage.record_sighting(addr, int(now))
        self._notify(addr)

    def record_relay(self, addr: str, via: str) -> None:
        """Reachable through `via`. Never downgrades a live direct session."""
        addr = addr.upper()
        with self._lock:
            status = self._entry(addr)
            if status.state == CONNECTED:
                return
            status.state = RELAY
            status.via = via.upper()
        self._notify(addr)

    def clear_relay(self, addr: str) -> None:
        addr = addr.upper()
        with self._lock:
            status = self._peers.get(addr)
            if status is None or status.state != RELAY:
                return
            status.via = None
            status.state = NEARBY if self._is_recent(status) else OFFLINE
        self._notify(addr)

    def forget(self, addr: str) -> None:
        with self._lock:
            self._peers.pop(addr.upper(), None)

    # --- Reads ---

    @staticmethod
    def _is_recent(status: PeerStatus, now: float | None = None) -> bool:
        if status.last_seen is None:
            return False
        return (now if now is not None else time.time()) - status.last_seen < NEARBY_WINDOW

    def _aged(self, status: PeerStatus, now: float) -> PeerStatus:
        """Apply the nearby-window timeout without mutating stored state.

        Ageing on read (rather than on a timer) keeps the tracker free of
        background threads: a status is only ever stale in the instant between
        the window expiring and someone asking.
        """
        if status.state == NEARBY and not self._is_recent(status, now):
            return PeerStatus(**{**status.__dict__, "state": OFFLINE})
        return status

    def status(self, addr: str) -> PeerStatus:
        addr = addr.upper()
        now = time.time()
        with self._lock:
            status = self._peers.get(addr)
            if status is None:
                return PeerStatus(addr=addr)
            return self._aged(status, now)

    def all_statuses(self) -> dict[str, PeerStatus]:
        now = time.time()
        with self._lock:
            return {a: self._aged(s, now) for a, s in self._peers.items()}

    def connected(self) -> list[str]:
        return sorted(a for a, s in self.all_statuses().items() if s.state == CONNECTED)

    def nearby_unreachable(self) -> list[str]:
        """Devices the radio can see but we have repeatedly failed to reach."""
        return sorted(
            a for a, s in self.all_statuses().items() if s.unreachable_nearby
        )

    def sync_from_manager(self, conn_mgr) -> None:
        """Reconcile against ConnectionManager's live view.

        Cheap enough to call on every UI refresh, and it repairs any state the
        tracker missed (a callback that raised, a peer added before the tracker
        was wired up).
        """
        with conn_mgr.peers_lock:
            live = set(conn_mgr.peers)
        relays = dict(conn_mgr.indirect_via)
        with self._lock:
            for addr in live:
                if self._entry(addr).state != CONNECTED:
                    self.record_connected(addr)
            for addr, status in self._peers.items():
                if status.state == CONNECTED and addr not in live:
                    self.record_disconnected(addr)
            for addr, via in relays.items():
                if addr not in live:
                    self.record_relay(addr, via)
