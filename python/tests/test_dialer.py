"""Dial scheduler tests.

The situation this code exists for — a full cabin, forty Bluetooth devices in
range, one of them the peer you want — is miserable to reproduce with hardware,
so it is simulated here in full.
"""

import pytest

from muninn import scanpolicy as sp
from muninn.dialer import KNOWN_PEER, UNKNOWN, DialScheduler

LOCAL = "AA:AA:AA:AA:AA:AA"
PEER = "BB:BB:BB:BB:BB:BB"
PEER2 = "BB:BB:BB:BB:BB:CC"
NOISE = "CC:CC:CC:CC:CC:01"

NEVER_CONNECTED = lambda _addr: False  # noqa: E731


@pytest.fixture
def sched():
    return DialScheduler(sp.AGGRESSIVE, LOCAL)


def cabin(sched, now, headsets=40):
    """One Muninn peer plus a lot of other people's audio gear."""
    sched.saw(PEER, now, is_peer=True)
    for i in range(headsets):
        sched.saw(f"CC:CC:CC:CC:{i // 256:02X}:{i % 256:02X}", now)


# --- Priority ---


def test_a_known_peer_is_dialled_before_any_unknown_device(sched):
    cabin(sched, 1000.0)
    plan = sched.plan(1000.0, NEVER_CONNECTED)
    assert plan.targets[0] == PEER
    assert plan.peers == [PEER]


def test_probes_are_rationed_so_a_crowd_cannot_starve_real_peers(sched):
    cabin(sched, 1000.0, headsets=200)
    plan = sched.plan(1000.0, NEVER_CONNECTED)
    assert len(plan.probes) == sp.AGGRESSIVE.probe_budget
    assert plan.peers == [PEER], "the peer must still be served"


def test_a_connected_peer_is_not_redialled(sched):
    cabin(sched, 1000.0)
    plan = sched.plan(1000.0, lambda a: a == PEER)
    assert PEER not in plan.targets


def test_the_freshest_sighting_is_dialled_first(sched):
    sched.saw(PEER, 1000.0, is_peer=True)
    sched.saw(PEER2, 1100.0, is_peer=True)
    assert sched.plan(1200.0, NEVER_CONNECTED).peers[0] == PEER2


def test_unprobed_devices_are_tried_before_ones_that_already_failed(sched):
    # Both still in the cabin; only one has been tried and refused.
    later = 1000.0 + sp.AGGRESSIVE.probe_backoff_base + 1
    sched.saw(NOISE, 1000.0)
    sched.failed(NOISE, 1000.0)
    sched.saw("CC:CC:CC:CC:CC:02", 1000.0)
    sched.saw(NOISE, later)
    sched.saw("CC:CC:CC:CC:CC:02", later)
    plan = sched.plan(later, NEVER_CONNECTED)
    assert plan.probes[0] == "CC:CC:CC:CC:CC:02"
    assert NOISE in plan.probes, "a device still present is worth re-probing"


# --- Backoff curves ---


def test_a_known_peer_is_retried_soon_and_never_abandoned(sched):
    """A peer out of range for a while must be picked up when they return."""
    sched.saw(PEER, 0.0, is_peer=True)
    now = 0.0
    for _ in range(30):
        sched.failed(PEER, now)
        now += sp.AGGRESSIVE.peer_backoff_max
    # However many failures, the wait is still capped low.
    assert sched.plan(now, NEVER_CONNECTED).peers == [PEER]


def test_a_peer_backoff_never_exceeds_its_cap(sched):
    sched.mark_peer(PEER)
    for _ in range(20):
        sched.failed(PEER, 0.0)
    entry = sched._entries[PEER]
    assert sched._backoff(entry) == sp.AGGRESSIVE.peer_backoff_max


def test_an_unknown_device_backs_off_much_harder_than_a_peer(sched):
    sched.saw(NOISE, 0.0)
    sched.failed(NOISE, 0.0)
    sched.mark_peer(PEER)
    sched.failed(PEER, 0.0)
    assert sched._backoff(sched._entries[NOISE]) > sched._backoff(sched._entries[PEER])


def test_a_failed_probe_is_not_retried_immediately(sched):
    sched.saw(NOISE, 0.0)
    sched.failed(NOISE, 0.0, "br-connection-refused")
    assert NOISE not in sched.plan(1.0, NEVER_CONNECTED).targets
    assert NOISE in sched.plan(sp.AGGRESSIVE.probe_backoff_base + 1, NEVER_CONNECTED).targets


def test_repeated_probe_failures_grow_the_wait(sched):
    sched.saw(NOISE, 0.0)
    waits = []
    for _ in range(4):
        sched.failed(NOISE, 0.0)
        waits.append(sched._backoff(sched._entries[NOISE]))
    assert waits == sorted(waits)
    assert waits[-1] > waits[0]


def test_backoff_is_capped_for_unknown_devices_too(sched):
    sched.saw(NOISE, 0.0)
    for _ in range(40):
        sched.failed(NOISE, 0.0)
    assert sched._backoff(sched._entries[NOISE]) == sp.AGGRESSIVE.probe_backoff_max


# --- Promotion ---


def test_a_successful_dial_makes_a_device_a_peer_forever(sched):
    """Adapter UUID caches lie; a device that once spoke Muninn still does."""
    sched.saw(NOISE, 0.0)
    sched.succeeded(NOISE)
    assert sched._entries[NOISE].kind == KNOWN_PEER
    assert sched._entries[NOISE].confirmed
    # Later sightings without the UUID must not demote it.
    sched.saw(NOISE, 100.0, is_peer=False)
    assert sched._entries[NOISE].kind == KNOWN_PEER
    sched.failed(NOISE, 100.0)
    assert sched._backoff(sched._entries[NOISE]) <= sp.AGGRESSIVE.peer_backoff_max


def test_being_identified_as_a_peer_clears_an_accumulated_probe_backoff(sched):
    sched.saw(NOISE, 0.0)
    for _ in range(6):
        sched.failed(NOISE, 0.0)
    assert NOISE not in sched.plan(10.0, NEVER_CONNECTED).targets
    sched.saw(NOISE, 10.0, is_peer=True)  # SDP finally resolved
    assert NOISE in sched.plan(10.0, NEVER_CONNECTED).peers


def test_a_success_clears_the_error(sched):
    sched.saw(PEER, 0.0, is_peer=True)
    sched.failed(PEER, 0.0, "br-connection-key-missing")
    sched.succeeded(PEER)
    assert sched._entries[PEER].last_error == ""
    assert sched._entries[PEER].failures == 0


# --- Visibility ---


def test_an_unknown_device_that_left_is_not_dialled(sched):
    sched.saw(NOISE, 0.0)
    assert NOISE not in sched.plan(10_000.0, NEVER_CONNECTED).targets


def test_a_known_peer_is_dialled_even_when_this_inquiry_missed_it(sched):
    """Inquiry misses are routine; our own connect may be what finds them."""
    sched.saw(PEER, 0.0, is_peer=True)
    assert sched.plan(10_000.0, NEVER_CONNECTED).peers == [PEER]


# --- Self ---


def test_our_own_address_is_never_dialled(sched):
    sched.saw(LOCAL, 0.0, is_peer=True)
    sched.mark_peer(LOCAL)
    assert sched.plan(0.0, NEVER_CONNECTED).targets == []


# --- Policy ---


@pytest.mark.parametrize("policy", [sp.AGGRESSIVE, sp.BALANCED, sp.CONSERVATIVE])
def test_every_policy_probes_less_than_it_dials_peers(policy):
    assert policy.probe_backoff_base > policy.peer_backoff_base
    assert policy.probe_backoff_max > policy.peer_backoff_max
    assert policy.probe_budget >= 1
    assert policy.dial_interval < policy.inquiry_interval


def test_the_presets_are_ordered_from_eager_to_quiet():
    order = [sp.AGGRESSIVE, sp.BALANCED, sp.CONSERVATIVE]
    assert [p.inquiry_interval for p in order] == sorted(p.inquiry_interval for p in order)
    assert [p.dial_interval for p in order] == sorted(p.dial_interval for p in order)
    assert [p.probe_budget for p in order] == sorted(
        (p.probe_budget for p in order), reverse=True
    )


def test_the_default_is_the_eager_one():
    # Finding peers unattended is the whole point.
    assert sp.DEFAULT is sp.AGGRESSIVE


def test_changing_policy_takes_effect_immediately(sched):
    sched.saw(NOISE, 0.0)
    sched.failed(NOISE, 0.0)
    assert NOISE not in sched.plan(1.0, NEVER_CONNECTED).targets
    sched.set_policy(sp.CONSERVATIVE)
    # Switching must not leave the user waiting out the old schedule.
    assert NOISE in sched.plan(1.0, NEVER_CONNECTED).targets


def test_setting_the_same_policy_does_not_reset_backoffs(sched):
    sched.saw(NOISE, 0.0)
    sched.failed(NOISE, 0.0)
    sched.set_policy(sp.AGGRESSIVE)
    assert NOISE not in sched.plan(1.0, NEVER_CONNECTED).targets


def test_a_smaller_budget_is_honoured_after_a_policy_change(sched):
    cabin(sched, 1000.0, headsets=50)
    sched.set_policy(sp.CONSERVATIVE)
    plan = sched.plan(1000.0, NEVER_CONNECTED)
    assert len(plan.probes) == sp.CONSERVATIVE.probe_budget


# --- Resolution / persistence ---


def test_policy_lookup_is_forgiving():
    assert sp.by_name("AGGRESSIVE") is sp.AGGRESSIVE
    assert sp.by_name(" balanced ") is sp.BALANCED
    assert sp.by_name("nonsense") is None
    assert sp.by_name(None) is None


def test_resolve_prefers_the_environment_then_storage(tmp_storage, monkeypatch):
    monkeypatch.delenv("MUNINN_SCAN_POLICY", raising=False)
    assert sp.resolve(tmp_storage) is sp.DEFAULT

    sp.store(tmp_storage, sp.CONSERVATIVE)
    assert sp.resolve(tmp_storage) is sp.CONSERVATIVE

    monkeypatch.setenv("MUNINN_SCAN_POLICY", "balanced")
    assert sp.resolve(tmp_storage) is sp.BALANCED

    monkeypatch.setenv("MUNINN_SCAN_POLICY", "rubbish")
    assert sp.resolve(tmp_storage) is sp.CONSERVATIVE, "a bad env value must not win"


def test_stats_summarise_what_is_being_tracked(sched):
    cabin(sched, 1000.0, headsets=5)
    sched.failed(NOISE, 1000.0)
    stats = sched.stats()
    assert stats["policy"] == "aggressive"
    assert stats["peers"] == 1
    assert stats["unknown"] >= 5
    assert stats["tracked"] == stats["peers"] + stats["unknown"]
