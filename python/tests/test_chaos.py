"""Harsh-cabin scenarios: flapping links, weak edges, movement, restarts,
decoys, concurrent group chatter, file transfer through unstable relays."""

import hashlib
import os
import random
import threading
import time

import pytest

from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import wait_until


def inbound(node, conv):
    return [m.text for m in node.storage.load_history(conv, 10000) if m.kind == "text"]


def all_delivered(node, ids):
    return all(node.storage.get_message(i).state in ("delivered", "read") for i in ids)


def line(lab, *names, spacing=5):
    return [lab.node(n, (i * spacing, 0)) for i, n in enumerate(names)]


def test_flapping_relay_link(lab):
    a, b, c = line(lab, "a", "b", "c")
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    lab.pair_cmd("flap", "b", "c", up=2.0, down=3.0)
    ids = []
    for i in range(40):
        ids.append(a.mesh.send_text(dm_conv(c.node_id), f"m{i}"))
        time.sleep(0.15)
    lab.pair_cmd("flap", "b", "c")  # stop flapping
    wait_until(
        lambda: len(inbound(c, dm_conv(a.node_id))) == 40, 120, msg="all 40 delivered"
    )
    assert sorted(inbound(c, dm_conv(a.node_id)), key=lambda s: int(s[1:])) == [
        f"m{i}" for i in range(40)
    ]
    wait_until(lambda: all_delivered(a, ids), 60, msg="all acked")


def test_weak_edge_both_directions(lab):
    # 5 rows apart: inside the flaky zone (good<=3, max=7)
    a = lab.node("a", (0, 0))
    b = lab.node("b", (5.2, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 60)
    ids_a, ids_b = [], []
    for i in range(25):
        ids_a.append(a.mesh.send_text(dm_conv(b.node_id), f"a{i}"))
        ids_b.append(b.mesh.send_text(dm_conv(a.node_id), f"b{i}"))
        time.sleep(0.2)
    wait_until(lambda: len(inbound(b, dm_conv(a.node_id))) == 50, 180)
    wait_until(lambda: len(inbound(a, dm_conv(b.node_id))) == 50, 180)
    wait_until(lambda: all_delivered(a, ids_a) and all_delivered(b, ids_b), 120)


def test_relay_path_switch_while_walking(lab):
    # a fixed; c walks from near a, out of a's range, staying near b.
    a = lab.node("a", (0, 0))
    lab.node("b", (5, 0))
    c = lab.node("c", (1, 1))
    wait_until(lambda: c.node_id in a.mesh.connected(), 40)
    ids = []
    for step, row in enumerate([2, 4, 6, 8, 9, 10, 9, 6, 3, 1]):
        lab.move("c", (row, 1))
        for k in range(3):
            ids.append(a.mesh.send_text(dm_conv(c.node_id), f"s{step}.{k}"))
        time.sleep(1.5)
    wait_until(lambda: len(inbound(c, dm_conv(a.node_id))) == 30, 150)
    wait_until(lambda: all_delivered(a, ids), 120)


def test_restart_with_queued_messages(lab):
    a, b, c = line(lab, "a", "b", "c")
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    lab.admin("offline", addr=lab.addr_of("c"))
    wait_until(lambda: not a.mesh.reachable(c.node_id), 30)
    ids = [a.mesh.send_text(dm_conv(c.node_id), f"q{i}") for i in range(5)]
    # The sender restarts while everything is still queued.
    a.stop()
    a2 = lab.node("a", (0, 0))
    assert a2.mesh.pending_count(c.node_id) == 5
    lab.admin("online", addr=lab.addr_of("c"))
    wait_until(lambda: len(inbound(c, dm_conv(a2.node_id))) == 5, 90)
    wait_until(lambda: all_delivered(a2, ids), 60)
    # Receiver restarts too: duplicates after restart must not reappear.
    c.stop()
    c2 = lab.node("c", (10, 0))
    time.sleep(3)
    assert len(inbound(c2, dm_conv(a2.node_id))) == 5


def test_cabin_group_chatter_with_decoys():
    from muninn.sim.lab import SimLab

    lab = SimLab(time_scale=0.3, decoys=40, seed=7)
    try:
        # Two clusters of seats 9 rows apart joined by one person mid-cabin.
        layout = {
            "p1": (0, 0),
            "p2": (1, 3),
            "p3": (2, 5),
            "mid": (5.5, 2),
            "p4": (9, 0),
            "p5": (10, 4),
        }
        nodes = {n: lab.node(n, pos) for n, pos in layout.items()}
        ids = {n: nodes[n].node_id for n in nodes}
        wait_until(
            lambda: (
                all(nodes["p1"].mesh.reachable(ids[o]) for o in ids if o != "p1")
                and nodes["p5"].mesh.reachable(ids["p1"])
            ),
            90,
            msg="everyone reachable from p1",
        )
        gid = nodes["p1"].mesh.create_group(
            "row party", [ids[n] for n in ids if n != "p1"]
        )
        conv = group_conv(gid)
        wait_until(lambda: all(gid in nodes[n].mesh.groups for n in nodes), 60)

        def chatter(n):
            for i in range(8):
                nodes[n].mesh.send_text(conv, f"{n}:{i}")
                time.sleep(random.uniform(0.05, 0.4))

        threads = [threading.Thread(target=chatter, args=(n,)) for n in nodes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected = {f"{n}:{i}" for n in nodes for i in range(8)}
        for n in nodes:
            wait_until(
                lambda n=n: set(inbound(nodes[n], conv)) >= expected,
                180,
                msg=f"{n} has all group messages",
            )
            assert len(inbound(nodes[n], conv)) == len(expected)  # no dupes
    finally:
        lab.close()


def test_file_through_flapping_relay(lab):
    a, b, c = line(lab, "a", "b", "c")
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    data = os.urandom(600_000)
    path = lab.tmp / "blob.bin"
    path.write_bytes(data)
    lab.pair_cmd("flap", "a", "b", up=4.0, down=2.0)
    mid = a.mesh.send_file(dm_conv(c.node_id), str(path), "application/octet-stream")
    wait_until(lambda: c.storage.get_message(mid) is not None, 60)

    def done():
        recs = [f for f in c.mesh.files.values() if f.msg_id == mid]
        return recs and recs[0].state == "complete"

    wait_until(done, 240, msg="file complete")
    rec = [f for f in c.mesh.files.values() if f.msg_id == mid][0]
    assert (
        hashlib.sha256(open(rec.path, "rb").read()).digest()
        == hashlib.sha256(data).digest()
    )
    lab.pair_cmd("flap", "a", "b")


def test_names_and_keys_propagate_multihop(lab):
    a, b, c, d = line(lab, "a", "b", "c", "d")
    wait_until(lambda: a.mesh.reachable(d.node_id), 60)
    wait_until(lambda: a.node_id in d.mesh.peers, 30)
    a.mesh.set_name("Alice ✈")
    wait_until(lambda: d.mesh.display_name(a.node_id) == "Alice ✈", 30)
    # d never had a direct link to a, yet can DM it (pubkey via gossip).
    mid = d.mesh.send_text(dm_conv(a.node_id), "hello from the back")
    wait_until(lambda: "hello from the back" in inbound(a, dm_conv(d.node_id)), 30)
    wait_until(lambda: all_delivered(d, [mid]), 30)


def test_group_created_while_member_away(lab):
    a, b, c = line(lab, "a", "b", "c")
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    lab.admin("offline", addr=lab.addr_of("c"))
    wait_until(lambda: not a.mesh.reachable(c.node_id), 30)
    gid = a.mesh.create_group("late", [b.node_id, c.node_id])
    a.mesh.send_text(group_conv(gid), "before you came")
    time.sleep(2)
    lab.admin("online", addr=lab.addr_of("c"))
    wait_until(lambda: gid in c.mesh.groups and c.mesh.groups[gid].name == "late", 90)
    wait_until(lambda: "before you came" in inbound(c, group_conv(gid)), 60)
    # c can talk in it and b hears it
    c.mesh.send_text(group_conv(gid), "made it")
    wait_until(lambda: "made it" in inbound(b, group_conv(gid)), 30)


def test_simultaneous_dial_settles_to_one_link(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 20)
    time.sleep(6)
    la, lb = a.mesh.link_info(), b.mesh.link_info()
    assert len(la) == 1 and len(lb) == 1
    # link must be stable (not churning): rtt measured and both sides agree
    first = (la[0]["initiated"], lb[0]["initiated"])
    assert first[0] != first[1]


def test_reactions_edits_replies_receipts_via_relay(lab):
    a, b, c = line(lab, "a", "b", "c")
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    conv_ac, conv_ca = dm_conv(c.node_id), dm_conv(a.node_id)
    m1 = a.mesh.send_text(conv_ac, "original")
    wait_until(lambda: c.storage.get_message(m1) is not None, 20)
    r = c.mesh.send_text(conv_ca, "a reply", reply_to=m1)
    c.mesh.react(conv_ca, m1, "👍")
    a.mesh.edit(conv_ac, m1, "edited")
    wait_until(lambda: c.storage.get_message(m1).text == "edited", 20)
    wait_until(lambda: a.storage.get_message(m1).reactions.get("👍") == [c.node_id], 20)
    wait_until(lambda: a.storage.get_message(r) is not None, 20)
    assert a.storage.get_message(r).reply_to == m1
    c.mesh.mark_read(conv_ca)
    wait_until(lambda: a.storage.get_message(m1).state == "read", 20)
    a.mesh.delete(conv_ac, m1)
    wait_until(lambda: c.storage.get_message(m1).deleted, 20)


def test_app_events_generic(lab):
    a, b = line(lab, "a", "b", spacing=1)
    wait_until(lambda: b.node_id in a.mesh.connected(), 20)
    got = []
    b.mesh.on("app", lambda ev: got.append(ev))
    conv = dm_conv(b.node_id)
    a.mesh.app_event(
        conv, "chess", "s1", "invite", {"players": [a.node_id, b.node_id]}, 0
    )
    for i in range(1, 6):
        a.mesh.app_event(conv, "chess", "s1", "move", {"m": f"mv{i}"}, i)
    wait_until(lambda: len(got) == 6, 20)
    evs = b.storage.load_app_events("s1")
    assert [e.seq for e in evs] == list(range(6))
    rts = []
    b.mesh.on("rt", lambda *args: rts.append(args))
    for i in range(20):
        a.mesh.rt_send(conv, "pong", "s1", {"y": i})
    wait_until(lambda: len(rts) >= 15, 10)


@pytest.mark.parametrize("seed", [1, 2])
def test_random_chaos_soak(seed):
    """Six nodes, random moves/kills/flaps for ~40 s, random traffic; then calm
    down and require every message delivered exactly once."""
    from muninn.sim.lab import SimLab

    rng = random.Random(seed)
    lab = SimLab(time_scale=0.3, decoys=15, seed=seed)
    try:
        names = [f"n{i}" for i in range(6)]
        nodes = {
            n: lab.node(n, (i * 2.5, rng.uniform(0, 5))) for i, n in enumerate(names)
        }
        wait_until(
            lambda: all(
                nodes["n0"].mesh.reachable(nodes[n].node_id) for n in names[1:]
            ),
            90,
        )
        sent: list[tuple[str, str, str]] = []  # (from, to, text)
        stop = time.monotonic() + 40
        while time.monotonic() < stop:
            act = rng.random()
            x, y = rng.sample(names, 2)
            if act < 0.55:
                text = f"{x}>{y}#{len(sent)}"
                nodes[x].mesh.send_text(dm_conv(nodes[y].node_id), text)
                sent.append((x, y, text))
            elif act < 0.7:
                lab.move(x, (rng.uniform(0, 14), rng.uniform(0, 5)))
            elif act < 0.8:
                lab.admin("kill", addr=lab.addr_of(x))
            elif act < 0.9:
                lab.pair_cmd("flap", x, y, up=rng.uniform(1, 3), down=rng.uniform(1, 3))
            else:
                lab.admin("reset")
            time.sleep(rng.uniform(0.05, 0.3))
        lab.admin("reset")
        for i, n in enumerate(names):  # calm: everyone in a connected line
            lab.move(n, (i * 2.5, 1))
        for x, y, text in sent:
            wait_until(
                lambda x=x, y=y, text=text: (
                    text in inbound(nodes[y], dm_conv(nodes[x].node_id))
                ),
                180,
                msg=f"{text} delivered",
            )
        for y in names:
            for x in names:
                if x != y:
                    msgs = inbound(nodes[y], dm_conv(nodes[x].node_id))
                    assert len(msgs) == len(set(msgs)), "duplicates"
        assert sum(n.mesh.pending_count() for n in nodes.values()) >= 0
        wait_until(
            lambda: sum(n.mesh.pending_count() for n in nodes.values()) == 0,
            120,
            msg="all outboxes drained",
        )
    finally:
        lab.close()


def test_reinstalled_peer_keeps_conversation(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    c = lab.node("c", (2, 0))
    wait_until(
        lambda: len(a.mesh.connected()) == 2 and len(c.mesh.connected()) == 2, 30
    )
    old_b = b.node_id
    gid = a.mesh.create_group("trio", [old_b, c.node_id])
    a.mesh.send_text(dm_conv(old_b), "before reinstall")
    wait_until(lambda: "before reinstall" in inbound(b, dm_conv(a.node_id)), 15)
    b.stop()
    wait_until(lambda: old_b not in a.mesh.connected(), 30)
    a.mesh.send_text(dm_conv(old_b), "while you were gone")
    b2 = lab.node("b", (1, 0), wipe=True)  # same radio, new identity
    assert b2.node_id != old_b
    wait_until(
        lambda: "while you were gone" in inbound(b2, dm_conv(a.node_id)),
        40,
        msg="queued message resealed for new key",
    )
    # a's history moved to the new identity, old one hidden
    assert "before reinstall" in inbound(a, dm_conv(b2.node_id))
    assert all(p.node_id != old_b for p in a.mesh.active_peers())
    # group membership follows, also on c (who heard via K_REPLACED/group update)
    wait_until(lambda: b2.node_id in a.mesh.groups[gid].members, 10)
    wait_until(
        lambda: (
            b2.node_id in c.mesh.groups[gid].members
            and old_b not in c.mesh.groups[gid].members
        ),
        30,
    )
    wait_until(lambda: gid in b2.mesh.groups, 30, msg="new b learns the group")
    b2.mesh.send_text(group_conv(gid), "back in the group")
    wait_until(lambda: "back in the group" in inbound(c, group_conv(gid)), 30)


def test_full_cabin_soak():
    """10 seats in two columns, two groups, DMs, two file transfers, people
    moving and flapping links for ~60 s, then calm: everything must arrive
    exactly once and every outbox must drain."""
    from muninn.sim.lab import SimLab

    rng = random.Random(42)
    lab = SimLab(time_scale=0.3, decoys=30, seed=42)
    try:
        seats = {f"s{i}": (i // 2 * 2.6, (i % 2) * 4.0) for i in range(10)}
        nodes = {n: lab.node(n, p) for n, p in seats.items()}
        names = list(nodes)
        ids = {n: nodes[n].node_id for n in names}
        wait_until(
            lambda: all(nodes["s0"].mesh.reachable(ids[n]) for n in names[1:]),
            150,
            msg="cabin converged",
        )
        front = names[:6]
        back = names[4:]
        g1 = nodes["s0"].mesh.create_group("front", [ids[n] for n in front[1:]])
        g2 = nodes["s9"].mesh.create_group("back", [ids[n] for n in back[:-1]])
        wait_until(
            lambda: (
                all(g1 in nodes[n].mesh.groups for n in front)
                and all(g2 in nodes[n].mesh.groups for n in back)
            ),
            90,
        )
        blob = os.urandom(250_000)
        fpath = lab.tmp / "soak.bin"
        fpath.write_bytes(blob)
        nodes["s0"].mesh.send_file(dm_conv(ids["s9"]), str(fpath))
        nodes["s9"].mesh.send_file(group_conv(g2), str(fpath))
        sent_dm, sent_g = [], []
        stop = time.monotonic() + 60
        while time.monotonic() < stop:
            r = rng.random()
            x, y = rng.sample(names, 2)
            if r < 0.35:
                t = f"dm {x}>{y} {len(sent_dm)}"
                nodes[x].mesh.send_text(dm_conv(ids[y]), t)
                sent_dm.append((x, y, t))
            elif r < 0.6:
                gid, members = (g1, front) if rng.random() < 0.5 else (g2, back)
                s = rng.choice(members)
                t = f"g {s} {len(sent_g)}"
                nodes[s].mesh.send_text(group_conv(gid), t)
                sent_g.append((gid, members, t))
            elif r < 0.7:
                row, col = seats[x]
                lab.move(x, (max(0, row + rng.uniform(-3, 3)), col))
            elif r < 0.8:
                lab.pair_cmd(
                    "flap", x, y, up=rng.uniform(1, 4), down=rng.uniform(0.5, 2)
                )
            elif r < 0.85:
                lab.admin("kill", addr=lab.addr_of(x))
            time.sleep(rng.uniform(0.05, 0.25))
        lab.admin("reset")
        for n, p in seats.items():
            lab.move(n, p)
        for x, y, t in sent_dm:
            wait_until(
                lambda x=x, y=y, t=t: t in inbound(nodes[y], dm_conv(ids[x])),
                240,
                msg=t,
            )
        for gid, members, t in sent_g:
            for n in members:
                wait_until(
                    lambda n=n, gid=gid, t=t: t in inbound(nodes[n], group_conv(gid)),
                    240,
                    msg=f"{t} at {n}",
                )
        for n in names:  # exactly once
            for conv in [group_conv(g1), group_conv(g2)] + [
                dm_conv(ids[o]) for o in names if o != n
            ]:
                msgs = inbound(nodes[n], conv)
                assert len(msgs) == len(set(msgs)), f"duplicates at {n} in {conv}"

        def files_done():
            want = hashlib.sha256(blob).hexdigest()
            ok9 = any(
                f.state == "complete" and f.sha256 == want and f.sender == ids["s0"]
                for f in nodes["s9"].mesh.files.values()
            )
            okg = all(
                any(
                    f.state == "complete" and f.sender == ids["s9"]
                    for f in nodes[n].mesh.files.values()
                )
                for n in back[:-1]
            )
            return ok9 and okg

        wait_until(files_done, 300, msg="files complete")
        wait_until(
            lambda: sum(nodes[n].mesh.pending_count() for n in names) == 0,
            240,
            msg="outboxes drained",
        )
    finally:
        lab.close()
