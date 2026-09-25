"""Routing, relay and recovery — the situations a flight actually produces.

Every test here is a scenario a user would recognise: the person three rows
back, the friend who went to the galley, the laptop that went to sleep, two
phones that noticed each other at the same instant. Each is wired from real
ConnectionManagers over socketpairs, so the handshake, framing and threads are
the production ones.
"""

import socket
import threading

import pytest
from conftest import drop_link, link, record_peer_wire, wait_for

from muninn import peers, protocol
from muninn.protocol import GROUP_ZERO_ID

A = "AA:AA:AA:AA:AA:AA"
B = "BB:BB:BB:BB:BB:BB"
C = "CC:CC:CC:CC:CC:CC"
D = "DD:DD:DD:DD:DD:DD"
X = "EE:EE:EE:EE:EE:EE"


def message_frames(rec, msg_id: bytes) -> int:
    """How many MESSAGE frames carrying msg_id went over a recorded link."""
    wire = rec.wire()
    count = 0
    i = 0
    while i + 3 <= len(wire):
        ftype = wire[i]
        length = int.from_bytes(wire[i + 1 : i + 3], "big")
        payload = wire[i + 3 : i + 3 + length]
        if ftype == protocol.TYPE_MESSAGE and payload[16:32] == msg_id:
            count += 1
        i += 3 + length
    return count


def chain(node_factory, *macs):
    """Nodes linked in a line: macs[0] - macs[1] - … - macs[-1]."""
    nodes = [node_factory(m) for m in macs]
    for left, right in zip(nodes, nodes[1:]):
        link(left, right)
    return nodes


# --- Routes ---


def test_a_device_two_hops_away_shows_as_reachable_through_the_middle(node_factory):
    a, b, c = chain(node_factory, A, B, C)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    status = a.cm.presence.status(C)
    assert status.state == "relay"
    assert status.via == B
    assert status.hops == 2


def test_routes_reach_down_a_chain_of_four(node_factory):
    a, b, c, d = chain(node_factory, A, B, C, D)
    assert wait_for(lambda: a.cm.indirect_via.get(D) == B)
    assert a.cm.presence.status(D).hops == 3
    assert wait_for(lambda: d.cm.indirect_via.get(A) == C)


def test_a_known_peer_is_not_called_reachable_just_because_a_relay_once_met_it(
    node_factory,
):
    """PEER_ANNC lists everyone the relay has ever met; only ROUTES says who
    it can reach right now."""
    a, b, c = chain(node_factory, A, B, C)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    c.cm.remove_peer(B)
    b.cm.remove_peer(C)
    assert wait_for(lambda: C not in a.cm.indirect_via)
    assert a.cm.presence.status(C).state != "relay"
    # But A still knows C well enough to write to them.
    assert a.groups.get_pubkey(C) == bytes(c.key.public_key)

    # A fresh neighbour of B's learns C's key, and nothing more.
    x = node_factory(X)
    link(x, b)
    assert wait_for(lambda: x.groups.get_pubkey(C) is not None)
    assert C not in x.cm.indirect_via


def test_a_walk_down_the_aisle_moves_the_route(node_factory):
    """C walks from B's row to D's row. A should follow them."""
    a, b, c, d = (node_factory(m) for m in (A, B, C, D))
    link(a, b)
    link(a, d)
    _bc, cb = link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    c.cm.remove_peer(B)
    b.cm.remove_peer(C)
    link(d, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == D)


def test_the_shortest_route_wins(node_factory):
    a, b, c, d, x = (node_factory(m) for m in (A, B, C, D, X))
    # Long way: A - X - D - C. Short way: A - B - C.
    link(a, x)
    link(x, d)
    link(d, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == X)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    assert a.cm.presence.status(C).hops == 2


# --- Relay delivery ---


def test_a_message_crosses_two_relays_and_its_ack_comes_back(node_factory):
    a, b, c, d = chain(node_factory, A, B, C, D)
    assert wait_for(lambda: D in a.cm.indirect_via)
    msg_id, sent, _ = a.cm.send_message(GROUP_ZERO_ID, "four rows back", [D])
    assert sent == [D]
    assert wait_for(lambda: d.messages), "message never crossed the chain"
    assert d.messages[0][1:3] == (A, "four rows back")
    assert wait_for(lambda: (msg_id, D) in a.acks), "ACK never came back"
    assert not b.messages and not c.messages
    assert msg_id not in a.cm.unacked


def test_a_relayed_message_goes_to_the_neighbour_that_can_reach_it(node_factory):
    """Not the first neighbour in the dict — the one with the route."""
    a, b, c, x = (node_factory(m) for m in (A, B, C, X))
    link(a, x)  # X knows nobody else
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    to_x = record_peer_wire(a, X)
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "hi", [C])
    assert wait_for(lambda: c.messages)
    assert message_frames(to_x, msg_id) == 0
    assert not x.cm.relay_queue


def test_a_message_for_someone_out_of_range_rides_along_with_whoever_meets_them(
    node_factory,
):
    """A writes to C, then closes the laptop. C later sits down next to B."""
    a, b, c = (node_factory(m) for m in (A, B, C))
    a.groups.add_pubkey(C, bytes(c.key.public_key))
    s_ab, s_ba = link(a, b)
    a.cm.send_message(GROUP_ZERO_ID, "see you at the gate", [C])
    assert wait_for(lambda: b.cm.relay_queue.get(C)), "B is not carrying it"

    a.cm.remove_peer(B)  # A leaves
    b.cm.remove_peer(A)
    link(b, c)
    assert wait_for(lambda: c.messages), "B never handed it over"
    assert c.messages[0][1:3] == (A, "see you at the gate")
    # Delivered, so B stops carrying it.
    assert wait_for(lambda: not b.cm.relay_queue.get(C))


def test_a_message_is_resent_the_moment_a_relay_path_appears(node_factory):
    """No waiting for the retry timer: the route is the trigger."""
    a, b, c = (node_factory(m) for m in (A, B, C))
    a.groups.add_pubkey(C, bytes(c.key.public_key))
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "when you can", [C])
    assert msg_id in a.cm.unacked
    link(a, b)
    link(b, c)
    assert wait_for(lambda: c.messages)
    assert wait_for(lambda: (msg_id, C) in a.acks)


def test_the_ack_for_a_resent_message_reaches_a_sender_who_missed_the_first(
    node_factory, monkeypatch
):
    monkeypatch.setattr(peers, "RETRY_INTERVAL", 0.0)
    a, b, c = (node_factory(m) for m in (A, B, C))
    a.groups.add_pubkey(C, bytes(c.key.public_key))
    link(a, b)
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "ping", [C])
    assert wait_for(lambda: b.cm.relay_queue.get(C))
    a.cm.remove_peer(B)
    b.cm.remove_peer(A)
    link(b, c)  # C gets B's carried copy and ACKs; A is not there to hear it
    assert wait_for(lambda: c.messages)
    assert msg_id in a.cm.unacked

    link(a, b)  # A comes back: resends, C re-ACKs, the ACK gets home
    assert wait_for(lambda: (msg_id, C) in a.acks), "ACK was swallowed by dedup"
    assert len(c.messages) == 1, "the resend must not show twice"


class SwallowNextMessage:
    """A link that silently loses the next MESSAGE frame written to it —
    what a relay sees when its own link to the recipient dies mid-send."""

    def __init__(self, sock):
        self._sock = sock
        self.armed = True

    def sendall(self, data):
        if self.armed and data[0] == protocol.TYPE_MESSAGE:
            self.armed = False
            return None
        return self._sock.sendall(data)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def test_maintain_retries_down_a_relay_path(node_factory, monkeypatch):
    """A relay can lose a frame. Only the sender's retry notices."""
    a, b, c = chain(node_factory, A, B, C)
    assert wait_for(lambda: C in a.cm.indirect_via)
    b.cm.peers[C].sock = SwallowNextMessage(b.cm.peers[C].sock)
    a.cm.send_message(GROUP_ZERO_ID, "lost once", [C])
    assert not wait_for(lambda: c.messages, timeout=0.5)

    a.cm.maintain()  # too soon: RETRY_INTERVAL has not passed
    assert not wait_for(lambda: c.messages, timeout=0.3)

    monkeypatch.setattr(peers, "RETRY_INTERVAL", 0.0)
    a.cm.maintain()
    assert wait_for(lambda: c.messages)
    assert len(c.messages) == 1


def test_a_relay_never_forwards_a_frame_back_around_a_loop(node_factory):
    """Triangle A-B-C plus D hanging off C: a flood must terminate."""
    a, b, c = (node_factory(m) for m in (A, B, C))
    link(a, b)
    link(b, c)
    link(c, a)
    d = node_factory(D)
    a.groups.add_pubkey(D, bytes(d.key.public_key))
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "anyone?", [D])
    # Every node carries at most one copy.
    assert wait_for(lambda: b.cm.relay_queue.get(D) and c.cm.relay_queue.get(D))
    assert len(b.cm.relay_queue[D]) == 1
    assert len(c.cm.relay_queue[D]) == 1
    assert D not in a.cm.relay_queue  # the sender holds it in unacked instead
    link(c, d)
    assert wait_for(lambda: d.messages)
    assert wait_for(lambda: (msg_id, D) in a.acks)
    assert len(d.messages) == 1


def test_read_receipts_wait_for_a_path_to_the_sender(node_factory):
    a, b, c = chain(node_factory, A, B, C)
    assert wait_for(lambda: C in a.cm.indirect_via)
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "read me", [C])
    assert wait_for(lambda: c.messages)
    assert wait_for(lambda: (msg_id, C) in a.acks)

    # A drops off; C reads the message while A cannot be reached.
    a.cm.remove_peer(B)
    b.cm.remove_peer(A)
    assert wait_for(lambda: A not in c.cm.indirect_via)
    c.cm.send_read(msg_id)
    link(a, b)
    assert wait_for(lambda: (msg_id, C) in a.reads), "the READ was lost"


# --- Groups over relays ---


def test_a_relay_that_is_not_in_the_group_passes_it_on_without_joining(node_factory):
    a, b, c = chain(node_factory, A, B, C)
    assert wait_for(lambda: a.groups.get_pubkey(C) is not None)
    group = a.cm.create_group("Row 12 and 14", [C])
    assert wait_for(lambda: group.group_id in c.groups.groups)
    assert group.group_id not in b.groups.groups
    assert b.groups_setup == []

    msg_id, sent, _ = a.cm.send_message(group.group_id, "window or aisle?", [C])
    assert wait_for(lambda: c.messages)
    assert c.messages[0][0] == group.group_id


def test_a_member_offline_at_creation_still_gets_the_group_before_its_messages(
    node_factory,
):
    a, b, c = (node_factory(m) for m in (A, B, C))
    link(a, b)
    a.groups.add_pubkey(C, bytes(c.key.public_key))
    group = a.cm.create_group("Trip", [B, C])
    a.cm.send_message(group.group_id, "first", [B, C])
    assert wait_for(lambda: b.messages)

    link(b, c)  # C turns up next to B, never next to A
    assert wait_for(lambda: group.group_id in c.groups.groups)
    assert wait_for(lambda: c.messages)
    assert c.messages[0][0] == group.group_id
    assert c.groups_setup and c.groups_setup[0].name == "Trip"


def test_a_reconnecting_member_is_reminded_of_shared_groups(node_factory):
    a, b = node_factory(A), node_factory(B)
    a.groups.add_pubkey(B, bytes(b.key.public_key))
    group = a.cm.create_group("Late joiner", [B])
    # Throw away A's held copy: only the reconnect reminder can deliver it.
    a.cm.relay_queue.clear()
    link(a, b)
    assert wait_for(lambda: group.group_id in b.groups.groups)


# --- Crossed dials, stale sessions, changed keys ---


def _half(node, sock, peer_mac, outbound, results, key):
    results[key] = node.cm.add_peer(sock, peer_mac, outbound=outbound)


@pytest.mark.parametrize("low_first", [True, False])
def test_a_crossed_dial_settles_on_one_session_on_both_sides(node_factory, low_first):
    """Both phones notice each other at once and both dial. Each side sees
    two sockets finish; both must keep the same one or neither works."""
    low, high = node_factory(A), node_factory(B)
    by_low = socket.socketpair()  # low dialled high
    by_high = socket.socketpair()  # high dialled low
    results: dict = {}

    def run(pairs):
        threads = [threading.Thread(target=_half, args=p) for p in pairs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

    first = [
        (low, by_low[0], B, True, results, "low-out"),
        (high, by_low[1], A, False, results, "high-in"),
    ]
    second = [
        (low, by_high[1], B, False, results, "low-in"),
        (high, by_high[0], A, True, results, "high-out"),
    ]
    run(first if low_first else second)
    run(second if low_first else first)
    assert all(results.values()), results

    assert wait_for(lambda: B in low.cm.peers and A in high.cm.peers)
    kept_low = low.cm.peers[B].sock
    kept_high = high.cm.peers[A].sock
    assert kept_low is by_low[0], "low must keep the session it opened"
    assert kept_high is by_low[1], "high must keep the session low opened"

    msg_id, _, _ = low.cm.send_message(GROUP_ZERO_ID, "still there?", [B])
    assert wait_for(lambda: high.messages)
    assert wait_for(lambda: (msg_id, B) in low.acks)


def test_a_redial_replaces_a_session_the_old_socket_has_not_noticed_is_dead(
    node_factory, monkeypatch
):
    monkeypatch.setattr(peers, "DUPLICATE_SESSION_WINDOW", 0.0)
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    old = a.cm.peers[B]
    link(a, b)
    assert a.cm.peers[B] is not old
    assert old.stop.is_set()


def test_a_peer_that_reinstalled_still_gets_what_was_waiting_for_it(node_factory):
    a = node_factory(A)
    b_old = node_factory(B, persist=False)
    link(a, b_old)
    a.cm.remove_peer(B)
    b_old.cm.remove_peer(A)

    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "you there?", [B])
    b_new = node_factory(B, persist=False)  # same address, fresh keys
    assert bytes(b_new.key.public_key) != bytes(b_old.key.public_key)
    link(a, b_new)
    assert wait_for(lambda: b_new.messages), "resend was sealed to the old key"
    assert b_new.messages[0][2] == "you there?"


# --- Robustness ---


def test_a_malformed_frame_costs_one_frame_not_the_session(node_factory):
    a, b = node_factory(A), node_factory(B)
    s_ab, _ = link(a, b)
    # A truncated group setup, sent raw down A's socket to B.
    a.cm.send_to(B, protocol.encode_frame(protocol.TYPE_GROUP_SETUP, b"\x01" * 20))
    a.cm.send_to(B, protocol.encode_frame(protocol.TYPE_ACK, b"\x01"))
    msg_id, _, _ = a.cm.send_message(GROUP_ZERO_ID, "after the junk", [B])
    assert wait_for(lambda: b.messages)
    assert A in b.cm.peers


def test_an_unknown_frame_type_from_a_newer_peer_is_ignored(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    a.cm.send_to(B, protocol.encode_frame(0x7E, b"from the future"))
    a.cm.send_message(GROUP_ZERO_ID, "still compatible", [B])
    assert wait_for(lambda: b.messages)
    assert A in b.cm.peers


def test_a_message_that_is_too_large_is_refused_before_it_is_saved(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    with pytest.raises(protocol.FrameTooLarge):
        a.cm.send_message(GROUP_ZERO_ID, "x" * 70000, [B])
    assert not a.cm.unacked


def test_a_dropped_link_mid_conversation_loses_nothing(node_factory):
    """Weak signal: the link dies, comes back, dies again. Everything typed
    in between arrives exactly once, in the order it was written."""
    a, b = node_factory(A), node_factory(B)
    _, s = link(a, b)
    sent = []
    for round_ in range(3):
        drop_link(s)
        assert wait_for(lambda: B not in a.cm.peers)
        for i in range(3):
            text = f"round {round_} line {i}"
            a.cm.send_message(GROUP_ZERO_ID, text, [B])
            sent.append(text)
        _, s = link(a, b)
    assert wait_for(lambda: len(b.messages) == len(sent)), b.messages
    assert [m[2] for m in b.messages] == sent
    assert wait_for(lambda: not a.cm.unacked)
