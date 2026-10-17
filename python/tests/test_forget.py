"""Deleting chats, forgetting people, blocking, deleting rooms."""

import time

from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import wait_until


def texts(node, conv):
    return [m.text for m in node.storage.load_history(conv)]


def test_delete_chat_keeps_the_contact(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    a.mesh.send_text(dm_conv(b.node_id), "one")
    wait_until(lambda: "one" in texts(b, dm_conv(a.node_id)), 15)
    a.mesh.delete_conversation(dm_conv(b.node_id))
    assert texts(a, dm_conv(b.node_id)) == []
    assert b.node_id in a.mesh.peers and b.node_id in a.mesh.connected()
    assert "one" in texts(b, dm_conv(a.node_id))  # only on this device


def test_forget_stops_redialling_but_they_can_come_back(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    b.mesh.send_text(dm_conv(a.node_id), "hi")
    wait_until(lambda: "hi" in texts(a, dm_conv(b.node_id)), 15)
    b_addr = lab.addr_of("b")
    a.forget(b.node_id)
    assert texts(a, dm_conv(b.node_id)) == []
    assert b.node_id not in a.mesh.peers
    assert all(n != b.node_id for n, _, _ in a.storage.load_transports())
    assert b_addr in a._forgotten
    assert b_addr not in [x for x, _ in a._candidates()]
    time.sleep(3)  # their device is still around (and may re-link)…
    assert b.node_id not in [p.node_id for p in a.mesh.active_peers()]  # …but hidden
    # not blocked: b may still write, and becomes a contact again
    b.mesh.send_text(dm_conv(a.node_id), "remember me?")
    wait_until(lambda: "remember me?" in texts(a, dm_conv(b.node_id)), 30)
    assert b.node_id in [p.node_id for p in a.mesh.active_peers()]


def test_block_refuses_links_and_messages(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    a.forget(b.node_id, block=True)
    wait_until(lambda: b.node_id not in a.mesh.connected(), 15)
    b.mesh.send_text(dm_conv(a.node_id), "let me in")
    time.sleep(4)
    assert texts(a, dm_conv(b.node_id)) == []
    assert b.node_id not in a.mesh.connected()
    assert b.node_id in a.storage.blocked()
    a.mesh.unblock(b.node_id)
    assert b.node_id not in a.storage.blocked()


def test_deleting_a_room_leaves_it(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    c = lab.node("c", (0, 1))
    wait_until(lambda: len(a.mesh.connected()) == 2, 30)
    gid = a.mesh.create_group("trip", [b.node_id, c.node_id])
    wait_until(lambda: gid in b.mesh.groups and gid in c.mesh.groups, 20)
    a.mesh.send_text(group_conv(gid), "hello room")
    wait_until(lambda: "hello room" in texts(c, group_conv(gid)), 15)
    a.mesh.delete_conversation(group_conv(gid))
    assert gid not in a.mesh.groups and texts(a, group_conv(gid)) == []
    wait_until(lambda: a.node_id not in b.mesh.groups[gid].members, 20)
    wait_until(lambda: a.node_id not in c.mesh.groups[gid].members, 20)


def test_privacy_switches_and_doctor(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    b.mesh.set_privacy("receipts", False)
    b.mesh.set_privacy("typing", False)
    mid = a.mesh.send_text(dm_conv(b.node_id), "did you read this?")
    wait_until(lambda: a.storage.get_message(mid).state == "delivered", 15)
    b.mesh.mark_read(dm_conv(a.node_id))
    b.mesh.send_typing(dm_conv(a.node_id))
    time.sleep(3)
    assert a.storage.get_message(mid).state == "delivered"  # never "read"
    assert not a.mesh.typing_in(dm_conv(b.node_id))
    labels = {label: lvl for lvl, label, _ in a.doctor()}
    assert labels["Bluetooth"] == "ok" and labels["b"] == "ok" and "Version" in labels
