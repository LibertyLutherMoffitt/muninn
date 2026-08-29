"""End-to-end ConnectionManager tests.

Each test wires two (or three) real ConnectionManagers together over
socketpairs and drives the actual handshake / framing / dedup / persistence
code. No Bluetooth backend is involved, so this runs anywhere — but it is the
same code path the CLI, the Qt GUI and the Android peer both speak.
"""

import socket
import threading

import pytest
from conftest import drop_link, link, record_peer_wire, wait_for

from muninn import protocol
from muninn.protocol import GROUP_ZERO_ID

A = "AA:AA:AA:AA:AA:AA"
B = "BB:BB:BB:BB:BB:BB"
C = "CC:CC:CC:CC:CC:CC"


# --- Handshake ---


def test_handshake_registers_each_peer_by_its_wire_id(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    assert list(a.cm.peers) == [B]
    assert list(b.cm.peers) == [A]
    assert a.groups.get_pubkey(B) == bytes(b.key.public_key)
    assert b.groups.get_pubkey(A) == bytes(a.key.public_key)


def test_peer_change_callback_fires_on_connect_and_disconnect(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    assert (B, True) in a.peer_changes
    a.cm.remove_peer(B)
    assert (B, False) in a.peer_changes


def test_a_peer_keyed_by_wire_id_is_reachable_by_its_transport_mac(node_factory):
    """Android's wire id differs from the BT address we dialled.

    add_peer must key the peer by the announced wire id but still let the
    scanner recognise the transport address as connected, or it redials the
    same phone every cycle.
    """
    a = node_factory(A)
    android = node_factory("02:11:22:33:44:55")  # locally-administered wire id
    transport = "D0:AB:CD:12:34:56"  # what the Linux box actually dialled

    s1, s2 = socket.socketpair()
    t = threading.Thread(target=android.cm.add_peer, args=(s2, A))
    t.start()
    assert a.cm.add_peer(s1, transport) is True
    t.join(5)

    assert list(a.cm.peers) == ["02:11:22:33:44:55"]
    assert a.cm.is_connected(transport) is True
    assert a.cm.is_connected(transport.lower()) is True
    assert a.cm.is_connected("00:00:00:00:00:00") is False


def test_transport_mapping_is_dropped_when_the_peer_goes_away(node_factory):
    a = node_factory(A)
    android = node_factory("02:11:22:33:44:55")
    transport = "D0:AB:CD:12:34:56"
    s1, s2 = socket.socketpair()
    t = threading.Thread(target=android.cm.add_peer, args=(s2, A))
    t.start()
    a.cm.add_peer(s1, transport)
    t.join(5)
    a.cm.remove_peer("02:11:22:33:44:55")
    assert a.cm.is_connected(transport) is False


def test_a_legacy_32_byte_handshake_falls_back_to_the_transport_mac(node_factory):
    """Older clients send no wire id. They must still connect."""
    a = node_factory(A)
    s1, s2 = socket.socketpair()

    def legacy_peer():
        s2.sendall(protocol.encode_handshake(b"\x05" * 32))  # no wire id
        protocol.read_frame(s2)

    t = threading.Thread(target=legacy_peer)
    t.start()
    assert a.cm.add_peer(s1, B) is True
    t.join(5)
    assert list(a.cm.peers) == [B]


def test_a_non_handshake_first_frame_is_refused(node_factory):
    a = node_factory(A)
    s1, s2 = socket.socketpair()

    def rude_peer():
        protocol.read_frame(s2)
        s2.sendall(protocol.encode_ack(b"\x00" * 16, b"\x00" * 6))

    t = threading.Thread(target=rude_peer)
    t.start()
    assert a.cm.add_peer(s1, B) is False
    t.join(5)
    assert a.cm.peers == {}


def test_a_malformed_handshake_payload_is_refused(node_factory):
    a = node_factory(A)
    s1, s2 = socket.socketpair()

    def bad_peer():
        protocol.read_frame(s2)
        s2.sendall(protocol.encode_frame(protocol.TYPE_HANDSHAKE, b"\x01" * 20))

    t = threading.Thread(target=bad_peer)
    t.start()
    assert a.cm.add_peer(s1, B) is False
    t.join(5)


def test_a_peer_that_hangs_up_during_handshake_is_refused(node_factory):
    a = node_factory(A)
    s1, s2 = socket.socketpair()
    s2.close()
    assert a.cm.add_peer(s1, B) is False


# --- Messaging ---


def test_a_message_arrives_decrypted_and_is_acked(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, sent, skipped = a.cm.send_message(GROUP_ZERO_ID, "hello flight", [B])
    assert sent == [B] and skipped == []

    assert wait_for(lambda: b.messages), "message never arrived"
    gid, sender, text, got_id = b.messages[0]
    assert (gid, sender, text, got_id) == (GROUP_ZERO_ID, A, "hello flight", msg_id)

    assert wait_for(lambda: a.acks), "ACK never came back"
    assert a.acks[0] == (msg_id, B)
    assert msg_id not in a.cm.unacked  # cleared by the ACK


def test_messages_survive_a_round_trip_in_both_directions(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    a.cm.send_message(GROUP_ZERO_ID, "ping", [B])
    assert wait_for(lambda: b.messages)
    b.cm.send_message(GROUP_ZERO_ID, "pong", [A])
    assert wait_for(lambda: a.messages)
    assert a.messages[0][2] == "pong"


def test_unicode_and_long_bodies_round_trip(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    body = "🛫 " + "boarding " * 2000
    a.cm.send_message(GROUP_ZERO_ID, body, [B])
    assert wait_for(lambda: b.messages)
    assert b.messages[0][2] == body


def test_a_body_past_the_frame_limit_raises_before_any_send(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    with pytest.raises(protocol.FrameTooLarge):
        a.cm.send_message(GROUP_ZERO_ID, "x" * 70_000, [B])
    assert b.messages == []


def test_a_dest_with_no_pubkey_is_reported_as_skipped(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    _msg_id, sent, skipped = a.cm.send_message(GROUP_ZERO_ID, "hi", [B, C])
    assert sent == [B] and skipped == [C]


def test_sending_only_to_self_is_a_no_op(node_factory):
    a = node_factory(A)
    msg_id, sent, skipped = a.cm.send_message(GROUP_ZERO_ID, "note to self", [A])
    assert sent == [] and skipped == []
    assert len(msg_id) == 16


def test_the_message_body_is_not_on_the_wire_in_clear(node_factory):
    """Metadata is deliberately plaintext; the text must not be."""
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    wire = record_peer_wire(a, B)
    a.cm.send_message(GROUP_ZERO_ID, "SECRET-CANARY", [B])
    assert wait_for(lambda: b.messages)
    assert wire.written, "nothing was written"
    assert b"SECRET-CANARY" not in wire.wire()
    # ...but the routing metadata deliberately is in the clear.
    assert protocol.mac_to_bytes(B) in wire.wire()


# --- Dedup / retransmit ---


def test_a_replayed_message_is_delivered_once_but_acked_again(node_factory):
    """A resend after reconnect must be dropped from the UI but still acked —
    otherwise the sender retransmits it forever."""
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "once", [B])
    assert wait_for(lambda: b.messages)

    wire = record_peer_wire(b, A)
    replay = protocol.encode_message(
        GROUP_ZERO_ID, msg_id, protocol.mac_to_bytes(A), protocol.mac_to_bytes(B),
        b"\x00" * 40,
    )[3:]
    b.cm._handle_message(A, replay)

    assert len(b.messages) == 1, "duplicate delivered to the UI"
    assert wire.wire() == protocol.encode_ack(msg_id, protocol.mac_to_bytes(B))


def test_the_sender_reports_a_duplicate_ack_only_once(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "once", [B])
    assert wait_for(lambda: a.acks)
    a.cm._handle_ack(B, protocol.encode_ack(msg_id, protocol.mac_to_bytes(B))[3:])
    assert len(a.acks) == 1


def test_a_decrypt_failure_releases_the_dedup_claim(node_factory):
    """Otherwise the sender's retransmit is silently swallowed forever."""
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id = protocol.new_msg_id()
    garbage = protocol.encode_message(
        GROUP_ZERO_ID, msg_id, protocol.mac_to_bytes(A),
        protocol.mac_to_bytes(B), b"\xff" * 60,
    )[3:]
    b.cm._handle_message(A, garbage)
    assert b.messages == []
    assert b.cm._claim_seen(msg_id) is True, "claim was not released"


def test_a_message_from_an_unknown_sender_releases_its_claim(node_factory):
    b = node_factory(B)
    msg_id = protocol.new_msg_id()
    frame = protocol.encode_message(
        GROUP_ZERO_ID, msg_id, protocol.mac_to_bytes(C),
        protocol.mac_to_bytes(B), b"\xff" * 60,
    )[3:]
    b.cm._handle_message(C, frame)
    assert b.messages == []
    assert b.cm._claim_seen(msg_id) is True


def test_unacked_messages_are_resent_when_the_peer_returns(node_factory):
    a, b = node_factory(A), node_factory(B)
    s1, s2 = link(a, b)

    # Send while the link is dead so nothing is delivered or acked.
    drop_link(s1)
    drop_link(s2)
    a.cm.remove_peer(B)
    b.cm.remove_peer(A)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "queued while away", [B])
    assert msg_id in a.cm.unacked

    link(a, b)  # reconnect
    assert wait_for(lambda: b.messages), "unacked message was not resent"
    assert b.messages[0][2] == "queued while away"
    assert wait_for(lambda: msg_id not in a.cm.unacked)


def test_unacked_messages_are_rebuilt_from_storage_after_a_restart(node_factory, tmp_path):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    a.cm.send_message(GROUP_ZERO_ID, "before restart", [B])
    assert wait_for(lambda: b.messages)

    # A fresh manager over the same DB, before any ACK was recorded.
    a.storage.save_outgoing_message(
        b"\x5a" * 16, GROUP_ZERO_ID, A, "never acked", 1700000000, [B]
    )
    from muninn.peers import ConnectionManager

    revived = ConnectionManager(A, a.key, a.groups, storage=a.storage)
    assert b"\x5a" * 16 in revived.unacked
    assert B in revived.unacked[b"\x5a" * 16]


# --- ACK / READ ---


def test_read_receipts_flow_back_to_the_sender(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "did you see this", [B])
    assert wait_for(lambda: b.messages)
    b.cm.send_read(msg_id)
    assert wait_for(lambda: a.reads)
    assert a.reads[0] == (msg_id, B)


def test_a_read_receipt_is_recorded_in_storage(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "hi", [B])
    assert wait_for(lambda: b.messages)
    b.cm.send_read(msg_id)
    assert wait_for(lambda: a.storage.load_dm_history(A, B, 5)[0][4] == "read")


def test_duplicate_acks_are_only_reported_once(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    msg_id, _sent, _ = a.cm.send_message(GROUP_ZERO_ID, "hi", [B])
    assert wait_for(lambda: a.acks)
    payload = protocol.encode_ack(msg_id, protocol.mac_to_bytes(B))[3:]
    a.cm._handle_ack(B, payload)
    a.cm._handle_ack(B, payload)
    assert len(a.acks) == 1


# --- Profile / display names ---


def test_a_display_name_set_at_startup_reaches_the_peer(node_factory):
    a, b = node_factory(A, name="Ravn"), node_factory(B)
    link(a, b)
    assert wait_for(lambda: b.groups.names.get(A) == "Ravn")
    assert b.groups.display_name(A) == "Ravn"


def test_a_name_change_is_broadcast_to_connected_peers(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    a.cm.set_display_name("Hugin")
    assert wait_for(lambda: b.groups.names.get(A) == "Hugin")
    a.cm.set_display_name("")
    assert wait_for(lambda: A not in b.groups.names)


def test_a_local_override_beats_the_peers_own_name(node_factory):
    a, b = node_factory(A, name="Ravn"), node_factory(B)
    link(a, b)
    assert wait_for(lambda: b.groups.names.get(A) == "Ravn")
    b.groups.set_override(A, "The Pilot")
    assert b.groups.display_name(A) == "The Pilot"
    a.cm.set_display_name("Renamed")
    assert wait_for(lambda: b.groups.names.get(A) == "Renamed")
    assert b.groups.display_name(A) == "The Pilot"  # override still wins


def test_a_peer_broadcasting_its_own_mac_as_a_name_is_ignored(node_factory):
    # Old clients defaulted display_name to their MAC; treat that as "unset".
    b = node_factory(B)
    b.cm._handle_profile(A, A.encode())
    assert A not in b.groups.names


# --- Peer announcements ---


def test_peers_learn_about_each_others_contacts(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)          # A knows B
    link(b, c)          # B knows C, and tells A about C
    # The route is recorded after the pubkey, so gate on the later of the two.
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    assert a.groups.get_pubkey(C) == bytes(c.key.public_key)


def test_an_announcement_cannot_overwrite_a_handshake_pubkey(node_factory):
    """A relay must never be able to redirect our encryption for a third party."""
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    real = a.groups.get_pubkey(B)
    forged = protocol.encode_peer_annc(
        [(protocol.mac_to_bytes(B), b"\xff" * 32, "impostor")]
    )[3:]
    a.cm._handle_peer_annc(B, forged)
    assert a.groups.get_pubkey(B) == real


def test_a_name_change_propagates_one_hop_through_a_relay(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.groups.get_pubkey(C) is not None)
    c.cm.set_display_name("Far Away")
    assert wait_for(lambda: a.groups.names.get(C) == "Far Away")


def test_direct_connection_clears_an_indirect_route(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    link(a, c)
    assert C not in a.cm.indirect_via


def test_losing_the_relay_drops_the_routes_it_carried(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.cm.indirect_via.get(C) == B)
    a.cm.remove_peer(B)
    assert C not in a.cm.indirect_via


# --- Relay ---


def test_a_message_is_relayed_through_a_middle_peer(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.groups.get_pubkey(C) is not None)

    msg_id, sent, _ = a.cm.send_message(GROUP_ZERO_ID, "via bravo", [C])
    assert sent == [C]
    assert wait_for(lambda: c.messages), "relayed message never arrived"
    assert c.messages[0][2] == "via bravo"
    assert c.messages[0][1] == A  # sender is the origin, not the relay
    assert b.messages == [], "the relay must not decrypt what it forwards"
    assert wait_for(lambda: a.acks), "ACK did not flood back through the relay"
    assert a.acks[0] == (msg_id, C)


def test_a_relay_forwards_each_message_only_once(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(b, c)
    assert wait_for(lambda: a.groups.get_pubkey(C) is not None)
    msg_id = protocol.new_msg_id()
    frame = protocol.encode_message(
        GROUP_ZERO_ID, msg_id, protocol.mac_to_bytes(A),
        protocol.mac_to_bytes(C), b"\x00" * 40,
    )[3:]
    b.cm._handle_message(A, frame)
    b.cm._handle_message(A, frame)
    assert (msg_id, protocol.mac_to_bytes(C)) in b.cm.seen_relayed


def test_a_message_for_an_offline_peer_is_queued_until_it_returns(node_factory):
    a, c = node_factory(A), node_factory(C)
    a.groups.add_pubkey(C, bytes(c.key.public_key))
    msg_id, sent, _ = a.cm.send_message(GROUP_ZERO_ID, "held for you", [C])
    assert sent == [C]
    assert a.cm.relay_queue.get(C), "frame was not queued"
    link(a, c)
    assert wait_for(lambda: c.messages), "queued frame was not flushed on connect"
    assert c.messages[0][2] == "held for you"


def test_a_relay_does_not_bounce_a_frame_back_to_its_sender(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    # A frame addressed to B but delivered by B is nonsense — drop it.
    frame = protocol.encode_message(
        GROUP_ZERO_ID, protocol.new_msg_id(), protocol.mac_to_bytes(A),
        protocol.mac_to_bytes(B), b"\x00" * 40,
    )[3:]
    b_peers_before = dict(a.cm.relay_queue)
    a.cm._handle_message(B, frame)
    assert a.cm.relay_queue == b_peers_before


# --- Groups ---


def test_creating_a_group_pushes_setup_to_every_member(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(a, c)
    group = a.cm.create_group("Sky Team", [B, C])
    assert wait_for(lambda: b.groups_setup and c.groups_setup)
    assert b.groups_setup[0].name == "Sky Team"
    assert set(b.groups_setup[0].members) == {A, B, C}
    assert group.group_id in c.groups.groups


def test_a_group_message_reaches_every_member(node_factory):
    a, b, c = node_factory(A), node_factory(B), node_factory(C)
    link(a, b)
    link(a, c)
    group = a.cm.create_group("Sky Team", [B, C])
    assert wait_for(lambda: b.groups_setup and c.groups_setup)
    a.cm.send_message(group.group_id, "wheels up", [B, C])
    assert wait_for(lambda: b.messages and c.messages)
    assert b.messages[0][2] == "wheels up"
    assert c.messages[0][2] == "wheels up"
    assert b.messages[0][0] == group.group_id


def test_creating_a_group_with_an_unknown_member_is_refused(node_factory):
    a = node_factory(A)
    with pytest.raises(ValueError):
        a.cm.create_group("Nope", [B])


def test_a_group_setup_is_not_forwarded_twice(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    gid = protocol.new_group_id()
    payload = protocol.encode_group_setup(
        gid, [(protocol.mac_to_bytes(A), b"\x01" * 32)], "Dup"
    )[3:]
    b.cm._handle_group_setup(A, payload)
    b.cm._handle_group_setup(A, payload)
    assert len(b.groups_setup) == 1


# --- Reconnect / teardown ---


def test_a_second_connection_replaces_the_stale_one(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    first = a.cm.peers[B]
    link(a, b)
    assert a.cm.peers[B] is not first, "the stale socket was kept"
    assert wait_for(lambda: first.stop.is_set())


def test_remove_peer_ignores_a_stale_expectation(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    stale = a.cm.peers[B]
    link(a, b)
    current = a.cm.peers[B]
    a.cm.remove_peer(B, expected=stale)  # the old recv loop finally noticing
    assert a.cm.peers.get(B) is current, "removed the live peer"


def test_a_dropped_link_removes_the_peer(node_factory):
    a, b = node_factory(A), node_factory(B)
    _s1, s2 = link(a, b)
    drop_link(s2)
    assert wait_for(lambda: B not in a.cm.peers)
    assert (B, False) in a.peer_changes


def test_send_to_a_vanished_peer_reports_failure(node_factory):
    a, b = node_factory(A), node_factory(B)
    link(a, b)
    assert a.cm.send_to(C, b"\x00\x00\x00") is False
