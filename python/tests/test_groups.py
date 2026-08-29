"""GroupStore tests — name precedence, pubkey trust rules, resolution."""

import pytest

from muninn.groups import Group, GroupStore

A = "AA:AA:AA:AA:AA:AA"
B = "BB:BB:BB:BB:BB:BB"


@pytest.fixture
def store():
    return GroupStore()


def test_an_unknown_peer_displays_as_its_mac(store):
    assert store.display_name(A) == A


def test_a_self_chosen_name_is_used_when_present(store):
    store.set_name(A, "Ravn")
    assert store.display_name(A) == "Ravn"


def test_a_local_override_beats_a_self_chosen_name(store):
    store.set_name(A, "Ravn")
    store.set_override(A, "The Pilot")
    assert store.display_name(A) == "The Pilot"
    store.clear_override(A)
    assert store.display_name(A) == "Ravn"


def test_clearing_a_name_falls_back_to_the_mac(store):
    store.set_name(A, "Ravn")
    store.clear_name(A)
    assert store.display_name(A) == A


def test_a_direct_pubkey_overwrites(store):
    store.add_pubkey(A, b"\x01" * 32)
    store.add_pubkey(A, b"\x02" * 32)
    assert store.get_pubkey(A) == b"\x02" * 32


def test_a_relayed_pubkey_never_overwrites_a_direct_one(store):
    store.add_pubkey(A, b"\x01" * 32)
    store.add_pubkey_if_missing(A, b"\xff" * 32)
    assert store.get_pubkey(A) == b"\x01" * 32


def test_a_group_seeds_pubkeys_only_for_unknown_members(store):
    store.add_pubkey(A, b"\x01" * 32)
    store.add_group(Group(b"\x00" * 16, {A: b"\xff" * 32, B: b"\x02" * 32}, "Crew"))
    assert store.get_pubkey(A) == b"\x01" * 32  # direct key kept
    assert store.get_pubkey(B) == b"\x02" * 32  # new member seeded


@pytest.mark.parametrize("query", [A, A.lower(), "Ravn", "ravn", "RAVN"])
def test_resolution_is_case_insensitive(store, query):
    store.add_pubkey(A, b"\x01" * 32)
    store.set_name(A, "Ravn")
    assert store.resolve(query) == A


def test_an_override_wins_resolution_over_another_peers_self_chosen_name(store):
    store.add_pubkey(A, b"\x01" * 32)
    store.add_pubkey(B, b"\x02" * 32)
    store.set_name(B, "shared")     # B calls itself "shared"
    store.set_override(A, "shared")  # we call A "shared"
    assert store.resolve("shared") == A


def test_a_renamed_peer_is_no_longer_resolvable_by_its_old_name(store):
    store.add_pubkey(A, b"\x01" * 32)
    store.set_name(A, "old")
    store.set_name(A, "new")
    assert store.resolve("new") == A
    assert store.resolve("old") is None


def test_resolving_something_unknown_returns_none(store):
    assert store.resolve("nobody") is None
    assert store.resolve("11:22:33:44:55:66") is None


def test_state_reloads_from_storage(tmp_storage):
    first = GroupStore(storage=tmp_storage)
    first.add_pubkey(A, b"\x01" * 32)
    first.set_name(A, "Ravn")
    first.set_override(A, "Pilot")
    first.add_group(Group(b"\x07" * 16, {A: b"\x01" * 32}, "Crew"))

    second = GroupStore(storage=tmp_storage)
    assert second.get_pubkey(A) == b"\x01" * 32
    assert second.names[A] == "Ravn"
    assert second.overrides[A] == "Pilot"
    assert second.display_name(A) == "Pilot"
    assert second.groups[b"\x07" * 16].name == "Crew"
