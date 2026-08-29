"""Storage tests — schema, delivery state, history queries, restart recovery."""

import time

import pytest

from muninn.groups import Group
from muninn.storage import GROUP_ZERO_ID, Storage

LOCAL = "AA:AA:AA:AA:AA:AA"
PEER = "BB:BB:BB:BB:BB:BB"
PEER2 = "CC:CC:CC:CC:CC:CC"


def mid(n: int) -> bytes:
    return bytes([n]) * 16


# --- Identity ---


def test_identity_starts_empty_and_persists(tmp_storage):
    assert tmp_storage.get_identity() is None
    ident = tmp_storage.create_identity(b"\x01" * 32, "Ravn")
    assert ident.privkey == b"\x01" * 32
    got = tmp_storage.get_identity()
    assert got.privkey == b"\x01" * 32
    assert got.display_name == "Ravn"


def test_identity_survives_reopen(tmp_path):
    s = Storage(tmp_path / "d.db")
    s.create_identity(b"\x02" * 32, "Hugin")
    s.close()
    s2 = Storage(tmp_path / "d.db")
    assert s2.get_identity().privkey == b"\x02" * 32
    assert s2.get_identity().display_name == "Hugin"
    s2.close()


def test_display_name_can_be_set_and_cleared(tmp_storage):
    tmp_storage.create_identity(b"\x03" * 32)
    assert tmp_storage.get_identity().display_name == ""
    tmp_storage.set_display_name("Munin")
    assert tmp_storage.get_identity().display_name == "Munin"
    tmp_storage.set_display_name("")
    assert tmp_storage.get_identity().display_name == ""


def test_only_one_identity_row_is_allowed(tmp_storage):
    tmp_storage.create_identity(b"\x04" * 32)
    with pytest.raises(Exception):
        tmp_storage.create_identity(b"\x05" * 32)


# --- Peers ---


def test_direct_handshake_pubkey_overwrites(tmp_storage):
    tmp_storage.save_peer_pubkey(PEER, b"\x01" * 32)
    tmp_storage.save_peer_pubkey(PEER, b"\x02" * 32)
    assert dict((m, k) for m, k, _, _ in tmp_storage.load_peers())[PEER] == b"\x02" * 32


def test_relayed_pubkey_never_overwrites_a_direct_one(tmp_storage):
    # A plaintext PEER_ANNC must not be able to redirect our encryption for a
    # peer we already handshook with.
    tmp_storage.save_peer_pubkey(PEER, b"\x01" * 32)
    tmp_storage.save_peer_pubkey_if_missing(PEER, b"\xff" * 32)
    assert dict((m, k) for m, k, _, _ in tmp_storage.load_peers())[PEER] == b"\x01" * 32


def test_relayed_pubkey_fills_an_unknown_peer(tmp_storage):
    tmp_storage.save_peer_pubkey_if_missing(PEER2, b"\x09" * 32)
    assert dict((m, k) for m, k, _, _ in tmp_storage.load_peers())[PEER2] == b"\x09" * 32


def test_names_and_overrides_persist_independently(tmp_storage):
    tmp_storage.save_peer_pubkey(PEER, b"\x01" * 32)
    tmp_storage.set_peer_name(PEER, "self-chosen")
    tmp_storage.set_peer_override(PEER, "my-nickname")
    row = {m: (n, o) for m, _, n, o in tmp_storage.load_peers()}[PEER]
    assert row == ("self-chosen", "my-nickname")
    tmp_storage.clear_peer_override(PEER)
    assert {m: o for m, _, _, o in tmp_storage.load_peers()}[PEER] is None
    # Clearing the override leaves the peer's own name alone.
    assert {m: n for m, _, n, _ in tmp_storage.load_peers()}[PEER] == "self-chosen"


def test_empty_self_chosen_name_is_stored_as_null(tmp_storage):
    tmp_storage.save_peer_pubkey(PEER, b"\x01" * 32)
    tmp_storage.set_peer_name(PEER, "x")
    tmp_storage.set_peer_name(PEER, "")
    assert {m: n for m, _, n, _ in tmp_storage.load_peers()}[PEER] is None


# --- Seen dedup ---


def test_claim_seen_is_first_wins(tmp_storage):
    assert tmp_storage.claim_seen(mid(1)) is True
    assert tmp_storage.claim_seen(mid(1)) is False


def test_released_claim_can_be_reclaimed(tmp_storage):
    # Needed so a retransmit still lands after a decrypt failure.
    assert tmp_storage.claim_seen(mid(2)) is True
    tmp_storage.release_seen(mid(2))
    assert tmp_storage.claim_seen(mid(2)) is True


def test_claim_seen_survives_restart(tmp_path):
    s = Storage(tmp_path / "d.db")
    s.claim_seen(mid(3))
    s.close()
    s2 = Storage(tmp_path / "d.db")
    assert s2.claim_seen(mid(3)) is False
    s2.close()


# --- Messages and delivery state ---


def test_outgoing_message_starts_as_sent(tmp_storage):
    now = int(time.time())
    tmp_storage.save_outgoing_message(mid(4), GROUP_ZERO_ID, LOCAL, "hi", now, [PEER])
    rows = tmp_storage.load_dm_history(LOCAL, PEER, 10)
    assert [(r[1], r[2], r[4]) for r in rows] == [(LOCAL, "hi", "sent")]


def test_ack_then_read_advance_the_state(tmp_storage):
    now = int(time.time())
    tmp_storage.save_outgoing_message(mid(5), GROUP_ZERO_ID, LOCAL, "hi", now, [PEER])
    tmp_storage.mark_acked(mid(5), PEER)
    assert tmp_storage.load_dm_history(LOCAL, PEER, 10)[0][4] == "acked"
    tmp_storage.mark_read(mid(5), PEER)
    assert tmp_storage.load_dm_history(LOCAL, PEER, 10)[0][4] == "read"


def test_a_repeated_read_keeps_the_first_timestamp(tmp_storage):
    now = int(time.time())
    tmp_storage.save_outgoing_message(mid(6), GROUP_ZERO_ID, LOCAL, "hi", now, [PEER])
    tmp_storage.mark_read(mid(6), PEER)
    first = tmp_storage._conn.execute(
        "SELECT read_at FROM message_recipients WHERE msg_id = ?", (mid(6),)
    ).fetchone()[0]
    time.sleep(1.05)
    tmp_storage.mark_read(mid(6), PEER)
    again = tmp_storage._conn.execute(
        "SELECT read_at FROM message_recipients WHERE msg_id = ?", (mid(6),)
    ).fetchone()[0]
    assert first == again


def test_inbound_messages_are_always_read(tmp_storage):
    tmp_storage.save_incoming_body(mid(7), GROUP_ZERO_ID, PEER, "yo", int(time.time()))
    assert tmp_storage.load_dm_history(LOCAL, PEER, 10)[0][4] == "read"


def test_save_incoming_body_is_idempotent(tmp_storage):
    ts = int(time.time())
    tmp_storage.save_incoming_body(mid(8), GROUP_ZERO_ID, PEER, "one", ts)
    tmp_storage.save_incoming_body(mid(8), GROUP_ZERO_ID, PEER, "two", ts)
    rows = tmp_storage.load_dm_history(LOCAL, PEER, 10)
    assert len(rows) == 1 and rows[0][2] == "one"


def test_dm_history_is_oldest_first_and_limited(tmp_storage):
    base = int(time.time()) - 100
    for i in range(5):
        tmp_storage.save_incoming_body(
            mid(20 + i), GROUP_ZERO_ID, PEER, f"m{i}", base + i
        )
    rows = tmp_storage.load_dm_history(LOCAL, PEER, 3)
    assert [r[2] for r in rows] == ["m2", "m3", "m4"]


def test_dm_history_excludes_other_peers_conversations(tmp_storage):
    ts = int(time.time())
    tmp_storage.save_incoming_body(mid(30), GROUP_ZERO_ID, PEER, "for-b", ts)
    tmp_storage.save_incoming_body(mid(31), GROUP_ZERO_ID, PEER2, "for-c", ts)
    tmp_storage.save_outgoing_message(mid(32), GROUP_ZERO_ID, LOCAL, "to-c", ts, [PEER2])
    assert [r[2] for r in tmp_storage.load_dm_history(LOCAL, PEER, 10)] == ["for-b"]
    assert sorted(r[2] for r in tmp_storage.load_dm_history(LOCAL, PEER2, 10)) == [
        "for-c",
        "to-c",
    ]


def test_group_history_aggregates_the_worst_recipient_state(tmp_storage):
    gid = b"\x77" * 16
    ts = int(time.time())
    tmp_storage.save_outgoing_message(mid(40), gid, LOCAL, "hey all", ts, [PEER, PEER2])
    assert tmp_storage.load_group_history(gid, LOCAL, 10)[0][4] == "sent"
    tmp_storage.mark_acked(mid(40), PEER)
    assert tmp_storage.load_group_history(gid, LOCAL, 10)[0][4] == "sent"
    tmp_storage.mark_acked(mid(40), PEER2)
    assert tmp_storage.load_group_history(gid, LOCAL, 10)[0][4] == "acked"
    tmp_storage.mark_read(mid(40), PEER)
    assert tmp_storage.load_group_history(gid, LOCAL, 10)[0][4] == "acked"
    tmp_storage.mark_read(mid(40), PEER2)
    assert tmp_storage.load_group_history(gid, LOCAL, 10)[0][4] == "read"


def test_unacked_outbound_is_rebuilt_for_resend(tmp_storage):
    ts = int(time.time())
    tmp_storage.save_outgoing_message(
        mid(50), GROUP_ZERO_ID, LOCAL, "retry me", ts, [PEER, PEER2]
    )
    tmp_storage.mark_acked(mid(50), PEER)
    unacked = tmp_storage.load_unacked_outbound(LOCAL)
    assert len(unacked) == 1
    assert unacked[0].body == "retry me"
    assert unacked[0].recipients == [PEER2]  # PEER already acked


def test_fully_acked_messages_drop_out_of_the_resend_set(tmp_storage):
    ts = int(time.time())
    tmp_storage.save_outgoing_message(mid(51), GROUP_ZERO_ID, LOCAL, "done", ts, [PEER])
    tmp_storage.mark_acked(mid(51), PEER)
    assert tmp_storage.load_unacked_outbound(LOCAL) == []


def test_previews_pick_the_latest_message_per_peer(tmp_storage):
    base = int(time.time()) - 50
    tmp_storage.save_incoming_body(mid(60), GROUP_ZERO_ID, PEER, "older", base)
    tmp_storage.save_outgoing_message(
        mid(61), GROUP_ZERO_ID, LOCAL, "newer", base + 10, [PEER]
    )
    tmp_storage.save_incoming_body(mid(62), GROUP_ZERO_ID, PEER2, "other", base + 5)
    previews = tmp_storage.last_message_per_dm(LOCAL)
    assert previews[PEER] == ("newer", base + 10)
    assert previews[PEER2] == ("other", base + 5)


def test_group_previews_exclude_dms(tmp_storage):
    gid = b"\x88" * 16
    base = int(time.time())
    tmp_storage.save_incoming_body(mid(70), GROUP_ZERO_ID, PEER, "a dm", base)
    tmp_storage.save_incoming_body(mid(71), gid, PEER, "a group msg", base)
    previews = tmp_storage.last_message_per_group()
    assert list(previews) == [gid]
    assert previews[gid][0] == "a group msg"


# --- Groups ---


def test_group_members_reload_with_their_pubkeys(tmp_storage):
    tmp_storage.save_peer_pubkey(LOCAL, b"\x01" * 32)
    tmp_storage.save_peer_pubkey(PEER, b"\x02" * 32)
    gid = b"\x99" * 16
    tmp_storage.save_group(Group(gid, {LOCAL: b"\x01" * 32, PEER: b"\x02" * 32}, "Crew"))
    loaded = tmp_storage.load_groups()
    assert len(loaded) == 1
    assert loaded[0].name == "Crew"
    assert loaded[0].members == {LOCAL: b"\x01" * 32, PEER: b"\x02" * 32}


def test_resaving_a_group_does_not_duplicate_it(tmp_storage):
    tmp_storage.save_peer_pubkey(PEER, b"\x02" * 32)
    gid = b"\xaa" * 16
    tmp_storage.save_group(Group(gid, {PEER: b"\x02" * 32}, "Crew"))
    tmp_storage.save_group(Group(gid, {PEER: b"\x02" * 32}, "Renamed"))
    loaded = tmp_storage.load_groups()
    assert len(loaded) == 1 and loaded[0].name == "Crew"


# --- Schema ---


def test_reopening_an_existing_db_does_not_re_run_migrations(tmp_path):
    s = Storage(tmp_path / "d.db")
    s.save_peer_pubkey(PEER, b"\x01" * 32)
    s.close()
    s2 = Storage(tmp_path / "d.db")
    assert len(s2.load_peers()) == 1
    s2.close()


def test_wal_mode_is_enabled(tmp_storage):
    mode = tmp_storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
