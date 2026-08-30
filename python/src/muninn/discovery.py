"""Peer discovery and connection loops, shared by every desktop front end.

The CLI and the Qt GUI both need to accept inbound sockets and periodically
scan for, pair with and dial nearby peers. These loops used to be copy-pasted
into both entry points, and the copies drifted: only one of them fed the
presence tracker, so the GUI's peer list could not tell a device that had gone
away from one sitting in range refusing connections.

One implementation, imported by both.
"""

import threading
import time

from muninn import bt
from muninn.peers import ConnectionManager

# How long the higher-MAC device waits for the lower-MAC one to dial first.
# The deferral is the primary defence against both sides connecting at once;
# see PROTOCOL.md, "Simultaneous Connection Tiebreak".
TIEBREAK_DEFER = 10.0

# Gap between discovery sweeps.
SCAN_INTERVAL = 15.0

# Sweeps between full inquiries. A full inquiry is slow and disruptive to an
# active link, so it runs roughly every two minutes rather than every cycle.
INQUIRY_EVERY = 8

INQUIRY_SECONDS = 5.0


def acceptor(conn_mgr: ConnectionManager) -> None:
    """Accept inbound connections and hand each to the ConnectionManager.

    A failure handling one peer must never end the loop: if this thread dies
    the device silently stops accepting connections for the rest of the
    session, which looks exactly like being out of range.
    """
    while True:
        try:
            sock, addr = bt.accept()
        except ConnectionError:
            break
        try:
            conn_mgr.add_peer(sock, addr)
        except Exception as e:
            print(f"[accept {addr}] handshake failed: {e!r}")
            try:
                sock.close()
            except Exception:
                pass


def scanner(
    conn_mgr: ConnectionManager, local_mac: str, stop: threading.Event
) -> None:
    """Discover Muninn peers and keep connections up.

    Every device the radio reports is recorded as a sighting, and every failed
    dial as a failure, so the UI can distinguish "not here" from "here but
    unreachable" — the two look identical from the peers table alone.
    """
    presence = conn_mgr.presence
    local_mac = local_mac.upper()

    def inquiry() -> None:
        try:
            for addr, _name in bt.scan_devices(duration=INQUIRY_SECONDS, quiet=True):
                if addr.upper() != local_mac:
                    presence.record_sighting(addr)
        except Exception:
            # A scan failure must never take the loop down; the next sweep
            # tries again.
            pass

    inquiry()  # populate the adapter's cache before the first sweep

    deferred: dict[str, float] = {}
    cycles = 0

    while not stop.is_set():
        cycles += 1
        if cycles % INQUIRY_EVERY == 0:
            inquiry()

        try:
            services = bt.discover()
        except Exception:
            services = []

        for addr, _name in services:
            addr = addr.upper()
            if addr == local_mac:
                continue
            presence.record_sighting(addr)

            if conn_mgr.is_connected(addr):
                deferred.pop(addr, None)
                continue

            # The higher MAC holds off briefly so the lower one dials first.
            if not bt.should_keep_outgoing(local_mac, addr):
                started = deferred.setdefault(addr, time.time())
                if time.time() - started < TIEBREAK_DEFER:
                    continue

            deferred.pop(addr, None)
            try:
                bt.ensure_paired(addr)
                sock, peer_addr = bt.connect(addr)
                if not conn_mgr.add_peer(sock, peer_addr):
                    presence.record_dial_failure(addr, "handshake failed")
            except (ConnectionError, OSError) as e:
                presence.record_dial_failure(addr, str(e))

        stop.wait(SCAN_INTERVAL)
