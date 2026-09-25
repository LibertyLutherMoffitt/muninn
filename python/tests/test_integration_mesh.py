"""Real CLI processes in a cabin where not everyone can hear everyone.

`topology.json` (see `muninn/bt/loopback.py`) decides who is in range of whom,
and can change mid-test — that is how these tests model a row of seats, a
bulkhead, and someone walking to the galley. Everything else is the shipping
code: backend, scanner, dial scheduler, handshake, routing, SQLite, the CLI.
"""

import time

from cli_harness import diagnose, poll, set_topology

ALICE = "AA:AA:AA:AA:AA:01"
BOB = "BB:BB:BB:BB:BB:02"
CAROL = "CC:CC:CC:CC:CC:03"
DAVE = "DD:DD:DD:DD:DD:04"


def test_alice_writes_to_carol_through_bob(clients, tmp_path):
    set_topology(tmp_path / "rendezvous", (ALICE, BOB), (BOB, CAROL))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    carol = clients(CAROL, "carol")
    assert alice.wait_for("bob connected"), diagnose(alice, bob, carol)
    assert carol.wait_for("bob connected"), diagnose(alice, bob, carol)

    assert poll(alice, "/peers", "relay via bob"), diagnose(alice, bob, carol)
    # Someone reachable only through a relay is still a conversation.
    assert poll(alice, "/list", "DM: carol — relay via bob"), alice.output()
    alice.send("/dm carol")
    assert alice.wait_for("Switched to DM with carol"), alice.output()
    alice.send("hello from two rows back")
    assert carol.wait_for("[DM:alice] < hello from two rows back"), diagnose(
        alice, bob, carol
    )
    assert alice.wait_for("✓ carol"), diagnose(alice, bob, carol)
    # Bob carried it but never saw it.
    assert "hello from two rows back" not in bob.output()
    assert "carol connected" not in alice.output()

    carol.send("/dm alice")
    carol.send("got it")
    assert alice.wait_for("[DM:carol] < got it"), diagnose(alice, bob, carol)


def test_a_chain_of_four_seats(clients, tmp_path):
    set_topology(tmp_path / "rendezvous", (ALICE, BOB), (BOB, CAROL), (CAROL, DAVE))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    carol = clients(CAROL, "carol")
    dave = clients(DAVE, "dave")
    everyone = (alice, bob, carol, dave)
    assert poll(alice, "/peers", "dave"), diagnose(*everyone)
    alice.send("/dm dave")
    assert alice.wait_for("Switched to DM with dave"), alice.output()
    alice.send("three hops")
    assert dave.wait_for("[DM:alice] < three hops"), diagnose(*everyone)
    assert alice.wait_for("✓ dave"), diagnose(*everyone)


def test_a_group_spans_a_relay(clients, tmp_path):
    set_topology(tmp_path / "rendezvous", (ALICE, BOB), (BOB, CAROL))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    carol = clients(CAROL, "carol")
    trio = (alice, bob, carol)
    assert poll(alice, "/peers", "relay via bob"), diagnose(*trio)

    alice.send('/new "Row 14" bob carol')
    assert alice.wait_for("Created group 'Row 14'"), alice.output()
    assert bob.wait_for("Group 'Row 14' created"), diagnose(*trio)
    assert carol.wait_for("Group 'Row 14' created"), diagnose(*trio)

    alice.send("boarding in ten")
    assert bob.wait_for("[Row 14] < alice: boarding in ten"), diagnose(*trio)
    assert carol.wait_for("[Row 14] < alice: boarding in ten"), diagnose(*trio)

    carol.send('/group "Row 14"')
    assert carol.wait_for("Switched to group 'Row 14'"), carol.output()
    carol.send("on my way")
    assert alice.wait_for("[Row 14] < carol: on my way"), diagnose(*trio)
    assert bob.wait_for("[Row 14] < carol: on my way"), diagnose(*trio)


def test_a_conversation_follows_someone_walking_down_the_aisle(clients, tmp_path):
    rendezvous = tmp_path / "rendezvous"
    # Carol starts next to Alice.
    set_topology(rendezvous, (ALICE, BOB), (ALICE, CAROL))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    carol = clients(CAROL, "carol")
    trio = (alice, bob, carol)
    assert alice.wait_for("carol connected"), diagnose(*trio)
    assert alice.wait_for("bob connected"), diagnose(*trio)
    alice.send("/dm carol")
    alice.send("before the walk")
    assert carol.wait_for("< before the walk"), diagnose(*trio)

    # Carol walks to Bob's row, out of Alice's range.
    set_topology(rendezvous, (ALICE, BOB), (BOB, CAROL))
    assert alice.wait_for("carol disconnected"), diagnose(*trio)
    assert carol.wait_for("bob connected", timeout=60), diagnose(*trio)
    alice.send("after the walk")
    assert carol.wait_for("< after the walk", timeout=60), diagnose(*trio)
    assert alice.wait_for("✓ carol"), diagnose(*trio)


def test_a_message_rides_along_after_the_sender_closes_the_laptop(clients, tmp_path):
    rendezvous = tmp_path / "rendezvous"
    set_topology(rendezvous, (ALICE, BOB), (BOB, CAROL), (ALICE, CAROL))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    carol = clients(CAROL, "carol")
    trio = (alice, bob, carol)
    assert alice.wait_for("carol connected"), diagnose(*trio)
    assert bob.wait_for("carol connected"), diagnose(*trio)

    # Carol goes to the galley: nobody can reach her.
    set_topology(rendezvous, (ALICE, BOB))
    assert alice.wait_for("carol disconnected"), diagnose(*trio)
    assert bob.wait_for("carol disconnected"), diagnose(*trio)
    alice.send("/dm carol")
    alice.send("meet you at the gate")
    assert alice.wait_for("held for carol"), alice.output()
    time.sleep(1.0)  # let it reach Bob
    alice.close()  # and Alice shuts her laptop

    # Carol sits back down next to Bob, never near Alice again.
    set_topology(rendezvous, (BOB, CAROL))
    assert carol.wait_for("[DM:alice] < meet you at the gate", timeout=90), diagnose(
        bob, carol
    )


def test_nothing_is_lost_on_a_link_that_keeps_dropping(clients, tmp_path):
    """Weak signal: the only link flaps while Alice keeps typing."""
    rendezvous = tmp_path / "rendezvous"
    set_topology(rendezvous, (ALICE, BOB))
    alice = clients(ALICE, "alice")
    bob = clients(BOB, "bob")
    assert alice.wait_for("bob connected"), diagnose(alice, bob)
    alice.send("/dm bob")
    assert alice.wait_for("Switched to DM with bob")

    sent = []
    for round_ in range(3):
        set_topology(rendezvous)  # signal gone
        assert alice.wait_for_count("bob disconnected", round_ + 1), diagnose(
            alice, bob
        )
        for i in range(3):
            line = f"flap {round_}.{i}"
            alice.send(line)
            sent.append(line)
        set_topology(rendezvous, (ALICE, BOB))  # back
        assert alice.wait_for_count("bob connected", round_ + 2, timeout=90), diagnose(
            alice, bob
        )

    for line in sent:
        assert bob.wait_for(f"< {line}", timeout=60), diagnose(alice, bob)
    for line in sent:
        assert bob.output().count(f"< {line}") == 1, f"{line!r} shown twice"
