"""Deciding who to dial next, and when to give up on them.

The hard case is a full cabin: forty Bluetooth devices in inquiry range, of
which one is the person you want to talk to. Every dial attempt is a slow
blocking connect, so a scanner that treats all devices alike spends its whole
cycle on headsets and never reaches the peer two rows back.

Three rules follow from that:

* **A known peer is never given up on.** Its retry interval is capped low. Out
  of range for twenty minutes then back? Picked up on the next sweep.
* **An unidentified device is probed, then forgotten fast.** Bluetooth's UUID
  cache is unreliable enough (BlueZ frequently omits 128-bit UUIDs from inquiry
  EIR) that the only sure way to know whether something speaks Muninn is to
  try. But a failure is strong evidence it is a headset, so the backoff grows
  hard and far.
* **Probes are rationed.** A per-sweep budget keeps a crowded cabin from
  starving the peers we actually care about — known peers are always served
  first, and probes use what is left.

This module is pure logic: no sockets, no clock of its own. That makes the
behaviour testable, which matters because the situation it exists for is
miserable to reproduce. `DialScheduler.kt` is the Android counterpart and must
behave identically.
"""

import threading
from dataclasses import dataclass, field

from muninn.scanpolicy import DEFAULT, ScanPolicy

# Classification of a device we can see.
KNOWN_PEER = "peer"  # advertises Muninn, or we already hold its key
UNKNOWN = "unknown"  # seen by inquiry, never identified


@dataclass
class _Entry:
    addr: str
    kind: str = UNKNOWN
    failures: int = 0
    next_attempt: float = 0.0
    last_seen: float = 0.0
    last_error: str = ""
    # Set once a device has answered our Muninn profile. Such a device is a
    # peer forever after, even if a later dial fails and even if its UUIDs
    # vanish from the adapter cache.
    confirmed: bool = False


@dataclass
class DialPlan:
    """What the scanner should do this sweep."""

    peers: list[str] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        """Known peers first — they are why the app exists."""
        return self.peers + self.probes

    def __bool__(self) -> bool:
        return bool(self.peers or self.probes)


class DialScheduler:
    """Tracks what is worth dialling, and how soon.

    Thread-safe: the scanner thread drives it while the UI may read stats.
    """

    def __init__(self, policy: ScanPolicy = DEFAULT, local_mac: str = ""):
        self.policy = policy
        self.local_mac = local_mac.upper()
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()

    def set_policy(self, policy: ScanPolicy) -> None:
        """Swap timings mid-run. Pending backoffs are rescaled rather than
        kept, so choosing Aggressive takes effect now instead of after the
        old, longer waits expire."""
        with self._lock:
            old, self.policy = self.policy, policy
            if old.name == policy.name:
                return
            for entry in self._entries.values():
                entry.next_attempt = 0.0

    # --- Feeding ---

    def _entry(self, addr: str) -> _Entry:
        entry = self._entries.get(addr)
        if entry is None:
            entry = _Entry(addr=addr)
            self._entries[addr] = entry
        return entry

    def saw(self, addr: str, now: float, is_peer: bool = False) -> None:
        """Record that the radio can see `addr` right now.

        `is_peer` means it advertised the Muninn service. That is a hint, not
        proof — the cache lies in both directions — so it upgrades a device but
        never downgrades a confirmed one.
        """
        addr = addr.upper()
        if addr == self.local_mac:
            return
        with self._lock:
            entry = self._entry(addr)
            entry.last_seen = now
            if is_peer and entry.kind == UNKNOWN:
                entry.kind = KNOWN_PEER
                # A device newly identified as a peer deserves an immediate
                # attempt, whatever backoff it accumulated while anonymous.
                entry.next_attempt = 0.0
                entry.failures = 0

    def mark_peer(self, addr: str, now: float = 0.0) -> None:
        """Promote to a known peer — we hold its key, or it has talked to us."""
        addr = addr.upper()
        if addr == self.local_mac:
            return
        with self._lock:
            entry = self._entry(addr)
            if entry.kind != KNOWN_PEER:
                entry.kind = KNOWN_PEER
                entry.failures = 0
                entry.next_attempt = 0.0

    def succeeded(self, addr: str) -> None:
        """A session came up. This device is a peer from now on."""
        addr = addr.upper()
        with self._lock:
            entry = self._entry(addr)
            entry.kind = KNOWN_PEER
            entry.confirmed = True
            entry.failures = 0
            entry.next_attempt = 0.0
            entry.last_error = ""

    def failed(self, addr: str, now: float, error: str = "") -> None:
        """A dial attempt failed. Backs off, on the curve for its kind."""
        addr = addr.upper()
        with self._lock:
            entry = self._entry(addr)
            entry.failures += 1
            entry.last_error = error
            entry.next_attempt = now + self._backoff(entry)

    def forget(self, addr: str) -> None:
        with self._lock:
            self._entries.pop(addr.upper(), None)

    def _backoff(self, entry: _Entry) -> float:
        policy = self.policy
        if entry.kind == KNOWN_PEER or entry.confirmed:
            base, cap = policy.peer_backoff_base, policy.peer_backoff_max
        else:
            base, cap = policy.probe_backoff_base, policy.probe_backoff_max
        # Exponential, capped. Shift by one so the first failure waits `base`.
        return min(cap, base * (2 ** min(entry.failures - 1, 16)))

    # --- Planning ---

    def plan(
        self,
        now: float,
        is_connected,
        visible_only: bool = True,
        visibility_window: float = 180.0,
    ) -> DialPlan:
        """Who to dial this sweep.

        `is_connected(addr)` skips live sessions. `visible_only` restricts
        probing to devices the radio has seen recently — dialling a device that
        left the cabin an hour ago is pure latency.
        """
        peers: list[_Entry] = []
        probes: list[_Entry] = []
        with self._lock:
            for entry in self._entries.values():
                if entry.next_attempt > now:
                    continue
                if is_connected(entry.addr):
                    continue
                if entry.kind == KNOWN_PEER or entry.confirmed:
                    # A known peer is always worth trying, even if this sweep's
                    # inquiry missed it — inquiry misses are routine, and its
                    # own connect attempt may be what finds it.
                    peers.append(entry)
                elif not visible_only or (now - entry.last_seen) <= visibility_window:
                    probes.append(entry)

            # Freshest sightings first: the device that just appeared is the
            # one most likely to be the person who sat down next to you.
            peers.sort(key=lambda e: (-e.last_seen, e.failures))
            probes.sort(key=lambda e: (e.failures, -e.last_seen))
            budget = max(0, self.policy.probe_budget)
            return DialPlan(
                peers=[e.addr for e in peers],
                probes=[e.addr for e in probes[:budget]],
            )

    # --- Introspection (for /scan and the GUI) ---

    def stats(self) -> dict:
        with self._lock:
            entries = list(self._entries.values())
        return {
            "policy": self.policy.name,
            "tracked": len(entries),
            "peers": sum(1 for e in entries if e.kind == KNOWN_PEER),
            "unknown": sum(1 for e in entries if e.kind == UNKNOWN),
            "backing_off": sum(1 for e in entries if e.failures > 0),
        }
