"""Scenario tests over the simulated radio."""

import time

from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import wait_until


def texts(node, conv):
    return [m.text for m in node.storage.load_history(conv) if m.kind == "text"]


def state_of(node, msg_id):
    return node.storage.get_message(msg_id).state


def test_direct_dm_and_ack(lab):
    a = lab.node("alice", (0, 0))
    b = lab.node("bob", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 20, msg="link")
    mid = a.mesh.send_text(dm_conv(b.node_id), "hi bob")
    wait_until(lambda: texts(b, dm_conv(a.node_id)) == ["hi bob"], 10)
    wait_until(lambda: state_of(a, mid) == "delivered", 10)
    b.mesh.mark_read(dm_conv(a.node_id))
    wait_until(lambda: state_of(a, mid) == "read", 10)
    assert b.mesh.display_name(a.node_id) == "alice"


def test_relay_line(lab):
    # A — B — C : A and C out of range of each other
    a = lab.node("a", (0, 0))
    lab.node("b", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(
        lambda: a.mesh.reachable(c.node_id) and c.mesh.reachable(a.node_id),
        40,
        msg="route a<->c",
    )
    assert c.node_id not in a.mesh.connected()
    assert a.mesh.hops(c.node_id) == 2
    mid = a.mesh.send_text(dm_conv(c.node_id), "via bob")
    wait_until(lambda: texts(c, dm_conv(a.node_id)) == ["via bob"], 15)
    wait_until(lambda: state_of(a, mid) == "delivered", 15)


def test_store_and_forward_when_dest_offline(lab):
    a = lab.node("a", (0, 0))
    lab.node("b", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    lab.admin("offline", addr=lab.addr_of("c"))
    wait_until(lambda: not a.mesh.reachable(c.node_id), 30, msg="c unreachable")
    mid = a.mesh.send_text(dm_conv(c.node_id), "later")
    time.sleep(1)
    lab.admin("online", addr=lab.addr_of("c"))
    wait_until(lambda: texts(c, dm_conv(a.node_id)) == ["later"], 60)
    wait_until(lambda: state_of(a, mid) == "delivered", 30)


def test_group_three_way(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    c = lab.node("c", (2, 0))
    wait_until(
        lambda: len(a.mesh.connected()) == 2 and len(c.mesh.connected()) == 2, 30
    )
    gid = a.mesh.create_group("trip", [b.node_id, c.node_id])
    conv = group_conv(gid)
    wait_until(lambda: gid in b.mesh.groups and gid in c.mesh.groups, 15)
    assert b.mesh.groups[gid].name == "trip"
    b.mesh.send_text(conv, "from b")
    wait_until(lambda: "from b" in texts(a, conv) and "from b" in texts(c, conv), 15)


def test_duplicate_names_are_told_apart(lab):
    a = lab.node("a", (0, 0))
    b1 = lab.node("alex", (1, 0))
    b2 = lab.node("alex2", (1, 1))
    b2.mesh.set_name("alex")
    wait_until(lambda: len(a.mesh.connected()) == 2, 20)
    wait_until(lambda: a.mesh.peers[b2.node_id].name == "alex", 15)
    n1, n2 = a.mesh.display_name(b1.node_id), a.mesh.display_name(b2.node_id)
    assert n1 != n2 and n1.startswith("alex ·") and n2.startswith("alex ·")
    assert a.mesh.resolve(n2) == b2.node_id
