"""Python and Android protocol cores in one cabin, relaying for each other.

The Python clients are the real CLI. The Kotlin nodes run `Mesh.kt` — the
routing, relaying and group code the Android app ships — over the same
loopback backend and `topology.json`. Every test here has at least one
message cross from one implementation to the other *through a third device*,
which is where two independently written relays would disagree.

Skipped when no JDK/Gradle is available to build the node.
"""

import time

import pytest
from cli_harness import diagnose, poll, set_topology
from kotlin_node import KotlinNode, ensure_built

ALICE = "AA:AA:AA:AA:AA:01"  # Python
BOB = "BB:BB:BB:BB:BB:02"  # Python
CAROL = "CC:CC:CC:CC:CC:03"  # Python
KATE = "0A:0A:0A:0A:0A:0A"  # Kotlin — a locally administered id, like a phone's
KARL = "0B:0B:0B:0B:0B:0B"  # Kotlin


@pytest.fixture
def kotlin_nodes(tmp_path):
    error = ensure_built()
    if error is not None:
        pytest.skip(error)
    made: list[KotlinNode] = []

    def spawn(mac: str, name: str) -> KotlinNode:
        node = KotlinNode(mac, name, tmp_path / "rendezvous")
        made.append(node)
        assert node.wait_for(f"READY {mac}", timeout=30), node.output()
        return node

    yield spawn
    for node in made:
        node.close()


def test_a_laptop_and_a_phone_talk_directly(clients, kotlin_nodes):
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    assert alice.wait_for("kate connected"), diagnose(alice, kate)
    assert kate.wait_for(f"CONNECTED {ALICE} alice"), diagnose(alice, kate)

    alice.send("/dm kate")
    assert alice.wait_for("Switched to DM with kate")
    alice.send("hello from the laptop")
    assert kate.wait_for("alice: hello from the laptop"), diagnose(alice, kate)
    assert alice.wait_for("✓ kate"), diagnose(alice, kate)

    kate.send("dm alice hello from the phone")
    assert alice.wait_for("[DM:kate] < hello from the phone"), diagnose(alice, kate)
    assert kate.wait_for("ACK "), diagnose(alice, kate)
    # Alice is looking at the conversation, so she sends a read receipt.
    assert kate.wait_for("READ "), diagnose(alice, kate)


def test_a_phone_relays_between_two_laptops(clients, kotlin_nodes, tmp_path):
    set_topology(tmp_path / "rendezvous", (ALICE, KATE), (KATE, CAROL))
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    carol = clients(CAROL, "carol")
    cabin = (alice, kate, carol)
    assert poll(alice, "/peers", "relay via kate"), diagnose(*cabin)

    alice.send("/dm carol")
    assert alice.wait_for("Switched to DM with carol"), alice.output()
    alice.send("through the phone")
    assert carol.wait_for("[DM:alice] < through the phone"), diagnose(*cabin)
    assert alice.wait_for("✓ carol"), diagnose(*cabin)
    assert "through the phone" not in kate.output(), "the relay must not read it"

    carol.send("/dm alice")
    carol.send("and back")
    assert alice.wait_for("[DM:carol] < and back"), diagnose(*cabin)


def test_a_laptop_relays_between_two_phones(clients, kotlin_nodes, tmp_path):
    set_topology(tmp_path / "rendezvous", (KATE, BOB), (BOB, KARL))
    kate = kotlin_nodes(KATE, "kate")
    bob = clients(BOB, "bob")
    karl = kotlin_nodes(KARL, "karl")
    cabin = (kate, bob, karl)
    assert poll(kate, "peers", "karl RELAY BB:BB:BB:BB:BB:02 2"), diagnose(*cabin)

    kate.send("dm karl across the aisle")
    assert karl.wait_for("MSG dm"), diagnose(*cabin)
    assert karl.wait_for("kate: across the aisle"), diagnose(*cabin)
    assert kate.wait_for("ACK "), diagnose(*cabin)
    assert "across the aisle" not in bob.output()


def test_a_group_spans_both_implementations(clients, kotlin_nodes, tmp_path):
    set_topology(tmp_path / "rendezvous", (ALICE, KATE), (KATE, CAROL))
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    carol = clients(CAROL, "carol")
    cabin = (alice, kate, carol)
    assert poll(alice, "/peers", "relay via kate"), diagnose(*cabin)

    alice.send("/new Mixed kate carol")
    assert alice.wait_for("Created group 'Mixed'"), alice.output()
    assert kate.wait_for(" Mixed"), diagnose(*cabin)
    assert carol.wait_for("Group 'Mixed' created"), diagnose(*cabin)

    alice.send("hello both")
    assert kate.wait_for("MSG group:Mixed"), diagnose(*cabin)
    assert kate.wait_for("alice: hello both"), diagnose(*cabin)
    assert carol.wait_for("[Mixed] < alice: hello both"), diagnose(*cabin)

    kate.send("group-say Mixed from the phone")
    assert alice.wait_for("[Mixed] < kate: from the phone"), diagnose(*cabin)
    assert carol.wait_for("[Mixed] < kate: from the phone"), diagnose(*cabin)

    carol.send("/group Mixed")
    assert carol.wait_for("Switched to group 'Mixed'"), carol.output()
    carol.send("from the far laptop")
    assert alice.wait_for("[Mixed] < carol: from the far laptop"), diagnose(*cabin)
    assert kate.wait_for("carol: from the far laptop"), diagnose(*cabin)


def test_a_phone_creates_a_group_for_two_laptops_it_bridges(
    clients, kotlin_nodes, tmp_path
):
    set_topology(tmp_path / "rendezvous", (ALICE, KATE), (KATE, CAROL))
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    carol = clients(CAROL, "carol")
    cabin = (alice, kate, carol)
    assert kate.wait_for(f"CONNECTED {ALICE}"), diagnose(*cabin)
    assert kate.wait_for(f"CONNECTED {CAROL}"), diagnose(*cabin)

    kate.send("group-new Galley alice,carol")
    assert alice.wait_for("Group 'Galley' created"), diagnose(*cabin)
    assert carol.wait_for("Group 'Galley' created"), diagnose(*cabin)
    alice.send("/group Galley")
    assert alice.wait_for("Switched to group 'Galley'"), alice.output()
    alice.send("snacks?")
    assert carol.wait_for("[Galley] < alice: snacks?"), diagnose(*cabin)
    assert kate.wait_for("alice: snacks?"), diagnose(*cabin)


def test_a_phone_carries_a_message_after_the_sender_leaves(
    clients, kotlin_nodes, tmp_path
):
    rendezvous = tmp_path / "rendezvous"
    set_topology(rendezvous, (ALICE, KATE), (KATE, CAROL), (ALICE, CAROL))
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    carol = clients(CAROL, "carol")
    cabin = (alice, kate, carol)
    assert alice.wait_for("carol connected"), diagnose(*cabin)
    assert kate.wait_for(f"CONNECTED {CAROL}"), diagnose(*cabin)

    set_topology(rendezvous, (ALICE, KATE))  # Carol goes to the galley
    assert alice.wait_for("carol disconnected"), diagnose(*cabin)
    alice.send("/dm carol")
    alice.send("meet you at the gate")
    assert alice.wait_for("held for carol"), alice.output()
    time.sleep(1.0)  # let it reach the phone
    alice.close()

    set_topology(rendezvous, (KATE, CAROL))  # Carol sits back down by Kate
    assert carol.wait_for("[DM:alice] < meet you at the gate", timeout=60), diagnose(
        kate, carol
    )


def test_a_mixed_chain_of_four(clients, kotlin_nodes, tmp_path):
    """Laptop - phone - laptop - phone, three hops end to end."""
    set_topology(tmp_path / "rendezvous", (ALICE, KATE), (KATE, BOB), (BOB, KARL))
    alice = clients(ALICE, "alice")
    kate = kotlin_nodes(KATE, "kate")
    bob = clients(BOB, "bob")
    karl = kotlin_nodes(KARL, "karl")
    cabin = (alice, kate, bob, karl)
    # Karl's name has to cross both implementations to reach Alice.
    assert poll(alice, "/peers", "karl"), diagnose(*cabin)
    assert poll(alice, "/peers", "3 hops"), diagnose(*cabin)

    alice.send("/dm karl")
    assert alice.wait_for("Switched to DM with karl"), alice.output()
    alice.send("end to end")
    assert karl.wait_for("alice: end to end"), diagnose(*cabin)
    assert alice.wait_for("✓ karl"), diagnose(*cabin)
    karl.send("dm alice right back at you")
    assert alice.wait_for("[DM:karl] < right back at you"), diagnose(*cabin)
