"""Presence tests — the connected / nearby / relay / offline state machine."""

import time

import pytest

from muninn import presence
from muninn.presence import (
    CONNECTED,
    NEARBY,
    OFFLINE,
    RELAY,
    PresenceTracker,
    format_ago,
)

LOCAL = "AA:AA:AA:AA:AA:AA"
PEER = "BB:BB:BB:BB:BB:BB"
PEER2 = "CC:CC:CC:CC:CC:CC"


@pytest.fixture
def tracker():
    return PresenceTracker(local_mac=LOCAL)


# --- States ---


def test_an_unknown_peer_is_offline(tracker):
    status = tracker.status(PEER)
    assert status.state == OFFLINE
    assert status.last_seen is None
    assert status.describe() == "never seen"


def test_a_sighting_makes_a_peer_nearby(tracker):
    tracker.record_sighting(PEER)
    assert tracker.status(PEER).state == NEARBY
    assert tracker.status(PEER).describe() == "nearby · seen just now"


def test_connecting_makes_a_peer_connected(tracker):
    tracker.record_connected(PEER)
    status = tracker.status(PEER)
    assert status.state == CONNECTED
    assert status.is_reachable
    assert status.describe() == "connected"


def test_disconnecting_drops_to_nearby_not_offline(tracker):
    # The device was here a second ago; the next scan decides if it's gone.
    tracker.record_connected(PEER)
    tracker.record_disconnected(PEER)
    assert tracker.status(PEER).state == NEARBY


def test_a_relay_route_is_reachable_but_not_connected(tracker):
    tracker.record_relay(PEER, PEER2)
    status = tracker.status(PEER)
    assert status.state == RELAY
    assert status.is_reachable
    assert status.via == PEER2
    assert status.describe() == f"via {PEER2}"


def test_a_relay_never_downgrades_a_live_session(tracker):
    tracker.record_connected(PEER)
    tracker.record_relay(PEER, PEER2)
    assert tracker.status(PEER).state == CONNECTED


def test_clearing_a_relay_falls_back_to_recency(tracker):
    tracker.record_sighting(PEER)
    tracker.record_relay(PEER, PEER2)
    tracker.clear_relay(PEER)
    assert tracker.status(PEER).state == NEARBY

    tracker.record_relay(PEER2, PEER)
    tracker.clear_relay(PEER2)  # never independently sighted
    assert tracker.status(PEER2).state == OFFLINE


def test_a_sighting_does_not_clobber_a_live_session(tracker):
    tracker.record_connected(PEER)
    tracker.record_sighting(PEER)
    assert tracker.status(PEER).state == CONNECTED


# --- The "nearby but can't connect" case ---


def test_one_dial_failure_is_not_yet_unreachable(tracker):
    # RFCOMM connects routinely lose a race with the peer's own outgoing dial.
    tracker.record_sighting(PEER)
    tracker.record_dial_failure(PEER, "br-connection-refused")
    assert tracker.status(PEER).state == NEARBY
    assert tracker.status(PEER).unreachable_nearby is False


def test_repeated_dial_failures_mark_a_visible_peer_unreachable(tracker):
    tracker.record_sighting(PEER)
    for _ in range(presence.UNREACHABLE_AFTER):
        tracker.record_dial_failure(PEER, "br-connection-key-missing")
    status = tracker.status(PEER)
    assert status.unreachable_nearby is True
    assert status.last_error == "br-connection-key-missing"
    assert status.describe() == "nearby, can't connect · seen just now"
    assert tracker.nearby_unreachable() == [PEER]


def test_a_successful_connection_clears_the_failure_count(tracker):
    for _ in range(5):
        tracker.record_dial_failure(PEER)
    tracker.record_connected(PEER)
    assert tracker.status(PEER).failed_dials == 0
    assert tracker.status(PEER).last_error is None
    assert tracker.nearby_unreachable() == []


# --- Ageing ---


def test_a_stale_sighting_ages_out_to_offline(tracker):
    tracker.record_sighting(PEER)
    tracker._peers[PEER].last_seen = time.time() - presence.NEARBY_WINDOW - 1
    assert tracker.status(PEER).state == OFFLINE
    assert "last seen" in tracker.status(PEER).describe()


def test_a_sighting_inside_the_window_stays_nearby(tracker):
    tracker.record_sighting(PEER)
    tracker._peers[PEER].last_seen = time.time() - presence.NEARBY_WINDOW + 30
    assert tracker.status(PEER).state == NEARBY


def test_ageing_does_not_mutate_the_stored_record(tracker):
    tracker.record_sighting(PEER)
    tracker._peers[PEER].last_seen = time.time() - presence.NEARBY_WINDOW - 1
    assert tracker.status(PEER).state == OFFLINE
    assert tracker._peers[PEER].state == NEARBY  # untouched
    tracker.record_sighting(PEER)  # a fresh scan brings it right back
    assert tracker.status(PEER).state == NEARBY


def test_a_connected_peer_never_ages_out(tracker):
    tracker.record_connected(PEER)
    tracker._peers[PEER].last_seen = time.time() - 10_000
    assert tracker.status(PEER).state == CONNECTED


# --- Self ---


def test_our_own_address_is_never_tracked(tracker):
    tracker.record_sighting(LOCAL)
    assert tracker.all_statuses() == {}


def test_addresses_are_matched_case_insensitively(tracker):
    tracker.record_connected(PEER.lower())
    assert tracker.status(PEER).state == CONNECTED
    assert tracker.status(PEER.lower()).state == CONNECTED


# --- Reads ---


def test_connected_lists_only_live_sessions(tracker):
    tracker.record_connected(PEER)
    tracker.record_relay(PEER2, PEER)
    assert tracker.connected() == [PEER]


def test_on_change_fires_for_every_transition(tracker):
    seen: list[str] = []
    tracker.on_change = seen.append
    tracker.record_sighting(PEER)
    tracker.record_connected(PEER)
    tracker.record_disconnected(PEER)
    assert seen == [PEER, PEER, PEER]


def test_a_raising_callback_cannot_break_the_scanner(tracker):
    def boom(_addr):
        raise RuntimeError("UI exploded")

    tracker.on_change = boom
    tracker.record_sighting(PEER)  # must not propagate
    assert tracker.status(PEER).state == NEARBY


# --- Persistence ---


def test_presence_survives_a_restart(tmp_storage):
    first = PresenceTracker(storage=tmp_storage, local_mac=LOCAL)
    first.record_connected(PEER)
    first.record_sighting(PEER2)

    second = PresenceTracker(storage=tmp_storage, local_mac=LOCAL)
    # Nothing is connected after a restart, but we remember when they were.
    assert second.status(PEER).state == OFFLINE
    assert second.status(PEER).last_connected is not None
    assert second.status(PEER2).last_seen is not None
    assert second.status(PEER2).last_connected is None


def test_a_sighting_does_not_invent_a_pubkey(tmp_storage):
    """A device seen in a scan is not yet a peer we can encrypt to."""
    tracker = PresenceTracker(storage=tmp_storage, local_mac=LOCAL)
    tracker.record_sighting(PEER)
    assert tmp_storage.load_peers() == []
    assert PEER in tmp_storage.load_presence()


def test_a_sighting_does_not_clobber_a_stored_pubkey(tmp_storage):
    tmp_storage.save_peer_pubkey(PEER, b"\x01" * 32)
    PresenceTracker(storage=tmp_storage, local_mac=LOCAL).record_sighting(PEER)
    assert tmp_storage.load_peers()[0][1] == b"\x01" * 32


# --- Reconciliation with ConnectionManager ---


def test_sync_repairs_state_the_tracker_missed(node_factory):
    from conftest import link

    a, b = node_factory("AA:AA:AA:AA:AA:AA"), node_factory("BB:BB:BB:BB:BB:BB")
    link(a, b)
    tracker = PresenceTracker(local_mac=a.mac)  # wired up after the fact
    assert tracker.status(b.mac).state == OFFLINE
    tracker.sync_from_manager(a.cm)
    assert tracker.status(b.mac).state == CONNECTED

    a.cm.remove_peer(b.mac)
    tracker.sync_from_manager(a.cm)
    assert tracker.status(b.mac).state == NEARBY


def test_sync_records_relay_routes(node_factory):
    from conftest import link, wait_for

    a = node_factory("AA:AA:AA:AA:AA:AA")
    b = node_factory("BB:BB:BB:BB:BB:BB")
    c = node_factory("CC:CC:CC:CC:CC:CC")
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(c.mac) == b.mac)
    tracker = PresenceTracker(local_mac=a.mac)
    tracker.sync_from_manager(a.cm)
    assert tracker.status(c.mac).state == RELAY
    assert tracker.status(c.mac).via == b.mac


# --- Formatting ---


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, "never"),
        (0, "just now"),
        (44, "just now"),
        (45, "0m ago"),
        (60, "1m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (86399, "23h ago"),
        (86400, "1d ago"),
        (86400 * 9, "9d ago"),
    ],
)
def test_relative_time_formatting(seconds, expected):
    assert format_ago(seconds) == expected


# --- Discovery loop wiring ---


def test_the_acceptor_survives_a_peer_that_blows_up(node_factory, monkeypatch):
    """If the accept loop dies the device stops answering, silently."""
    import threading

    from muninn import discovery

    a = node_factory(LOCAL)
    calls = {"n": 0}

    class FakeSock:
        def close(self):
            pass

    def fake_accept():
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeSock(), PEER
        raise ConnectionError("closed")

    def boom(_sock, _addr):
        raise RuntimeError("peer sent nonsense")

    monkeypatch.setattr(discovery.bt, "accept", fake_accept, raising=False)
    monkeypatch.setattr(a.cm, "add_peer", boom)

    done = threading.Event()

    def run():
        discovery.acceptor(a.cm)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(5), "acceptor did not keep going after a failing peer"
    assert calls["n"] == 2, "it stopped accepting after the first failure"
