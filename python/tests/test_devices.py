"""Linked devices: one person on several devices (DESIGN.md "Linked devices").

Cast: `lap` (laptop, first device = account) and `phone` belong to Ann;
`bob` and `cat` are other people. Positions put everyone in radio range of
each other unless a test moves someone away.
"""

import time

import pytest

from muninn import devices as D
from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import wait_until

FAR = (80, 80)


def texts(node, conv):
    return [m.text for m in node.storage.load_history(conv, 1000) if m.kind == "text"]


def msgs(node, conv):
    return [m for m in node.storage.load_history(conv, 1000) if m.kind == "text"]


def link(lap, phone, timeout=30):
    code = lap.mesh.link_code()
    phone.mesh.link_to(lap.node_id, code)
    wait_until(
        lambda: phone.mesh.me == lap.node_id and lap.mesh.siblings() == [phone.node_id],
        timeout,
        msg="link",
    )


def meet(lab, *nodes, timeout=40):
    wait_until(
        lambda: all(
            a.mesh.reachable(b.node_id) for a in nodes for b in nodes if a is not b
        ),
        timeout,
        msg="mesh",
    )


@pytest.fixture
def cast(lab):
    lap = lab.node("lap", (0, 0))
    phone = lab.node("phone", (1, 0))
    bob = lab.node("bob", (0, 1))
    meet(lab, lap, phone, bob)
    return lap, phone, bob


# --------------------------------------------------------------------- linking


def test_link_with_code_and_wrong_codes(lab, cast):
    lap, phone, bob = cast
    code = lap.mesh.link_code()
    events = []
    phone.mesh.on("link", lambda *a: events.append(a))
    wrong = "000000" if code != "000000" else "111111"
    phone.mesh.link_to(lap.node_id, wrong)
    wait_until(
        lambda: events and events[-1][0] == "error", 15, msg="wrong code refused"
    )
    assert phone.mesh.me == phone.node_id
    phone.mesh.link_to(lap.node_id, code)
    wait_until(lambda: phone.mesh.me == lap.node_id, 20, msg="linked")
    assert lap.mesh.siblings() == [phone.node_id]
    assert phone.mesh.siblings() == [lap.node_id]
    # everyone else learns it: one person, two devices
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    assert bob.mesh.devices_of(lap.node_id) == sorted([lap.node_id, phone.node_id])
    people = [p.node_id for p in bob.mesh.active_peers()]
    assert lap.node_id in people and phone.node_id not in people
    # the code is single-use
    with pytest.raises(ValueError):
        bob.mesh.link_code() and phone.mesh.link_code()  # phone isn't a first device


def test_code_locks_after_five_wrong_tries(lab, cast):
    lap, phone, bob = cast
    code = lap.mesh.link_code()
    errors = []
    bob.mesh.on("link", lambda *a: errors.append(a))
    for i in range(5):
        guess = f"{(int(code) + 1 + i) % 10**6:06d}"
        bob.mesh.link_to(lap.node_id, guess)
        wait_until(lambda n=i: len(errors) > n, 15)
    bob.mesh.link_to(lap.node_id, code)  # right code, but it's burnt
    wait_until(lambda: len(errors) > 5, 15)
    assert bob.mesh.me == bob.node_id and lap.mesh.siblings() == []


# --------------------------------------------------------------------- forged sets


def test_forged_sets_are_rejected(lab, cast):
    lap, phone, bob = cast
    mal = bob  # bob tries to claim phone as one of his devices
    pk = {n.node_id: n.mesh.pubkey for n in (lap, phone, bob)}

    def pinned(i):
        return pk.get(i)

    joined = int(time.time())
    # 1. no consent from the phone
    ds = D.DeviceSet(
        mal.node_id,
        1,
        [
            D.Entry(mal.node_id, pk[mal.node_id], 0, b""),
            D.Entry(phone.node_id, pk[phone.node_id], joined, b"\x00" * 64),
        ],
    )
    D.sign(ds, bytes(mal.mesh.private_key))  # sorts entries
    assert "consent" in (D.check(ds, pinned) or "")
    # 2. a consent the phone gave to someone else (lap) can't be reused for bob
    consent = D.make_consent(
        bytes(phone.mesh.private_key), lap.node_id, phone.node_id, joined
    )
    ds.entries = [
        D.Entry(phone.node_id, pk[phone.node_id], joined, consent)
        if e.node_id == phone.node_id
        else e
        for e in ds.entries
    ]
    D.sign(ds, bytes(mal.mesh.private_key))
    assert D.check(ds, pinned)
    # 3. a set for lap's account not signed by lap
    good = D.sign(
        D.DeviceSet(
            lap.node_id,
            1,
            [
                D.Entry(phone.node_id, pk[phone.node_id], joined, consent),
                D.Entry(lap.node_id, pk[lap.node_id], 0, b""),
            ],
        ),
        bytes(lap.mesh.private_key),
    )
    assert D.check(good, pinned) is None
    forged = D.DeviceSet(good.account, 2, good.entries)
    D.sign(forged, bytes(mal.mesh.private_key))
    assert D.check(forged, pinned) == "bad primary signature"
    # 4. a listed key that isn't the one we pinned
    other = D.DeviceSet(
        lap.node_id,
        3,
        sorted(
            [
                D.Entry(lap.node_id, pk[bob.node_id], 0, b""),
                D.Entry(phone.node_id, pk[phone.node_id], joined, consent),
            ],
            key=lambda e: e.node_id,
        ),
    )
    assert "key mismatch" in (D.check(other, pinned) or "")
    # delivered over the wire, a forged set changes nothing
    from muninn import protocol as P

    mal.mesh._broadcast(P.encode_frame(P.T_DEVICES, forged.to_json().encode()))
    time.sleep(2)
    assert lap.mesh.person(phone.node_id) == phone.node_id
    assert phone.mesh.me == phone.node_id


# --------------------------------------------------------------------- messaging


def test_dm_fans_out_and_mirrors(lab, cast):
    lap, phone, bob = cast
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    ann = lap.node_id
    bob.mesh.send_text(dm_conv(ann), "hi ann")
    for dev in (lap, phone):
        wait_until(lambda d=dev: "hi ann" in texts(d, dm_conv(bob.node_id)), 15)
    # reply from the phone: bob sees it from Ann; the laptop sees its own message
    phone.mesh.send_text(dm_conv(bob.node_id), "from my phone")
    wait_until(lambda: "from my phone" in texts(bob, dm_conv(ann)), 15)
    m = next(x for x in msgs(bob, dm_conv(ann)) if x.text == "from my phone")
    assert m.sender == ann
    wait_until(lambda: "from my phone" in texts(lap, dm_conv(bob.node_id)), 15)
    own = next(x for x in msgs(lap, dm_conv(bob.node_id)) if x.text == "from my phone")
    assert own.sender == lap.mesh.me
    assert (
        lap.storage.unread_counts().get(dm_conv(bob.node_id), 0) == 1
    )  # only "hi ann"
    # bob's display: one chat, one name
    assert bob.mesh.display_name(phone.node_id) == bob.mesh.display_name(ann)
    # delivered/read are per person
    mid = bob.mesh.send_text(dm_conv(ann), "read me")
    wait_until(lambda: "read me" in texts(phone, dm_conv(bob.node_id)), 15)
    phone.mesh.mark_read(dm_conv(bob.node_id))
    wait_until(lambda: bob.storage.get_message(mid).status.get(ann) == "read", 15)
    # …and the laptop mirrors the read mark
    wait_until(
        lambda: lap.storage.unread_counts().get(dm_conv(bob.node_id), 0) == 0, 15
    )


def test_earlier_separate_chats_merge(lab, cast):
    lap, phone, bob = cast
    bob.mesh.send_text(dm_conv(phone.node_id), "to the phone, before linking")
    bob.mesh.send_text(dm_conv(lap.node_id), "to the laptop, before linking")
    wait_until(
        lambda: texts(phone, dm_conv(bob.node_id)) and texts(lap, dm_conv(bob.node_id)),
        15,
    )
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    wait_until(lambda: len(texts(bob, dm_conv(lap.node_id))) == 2, 15)
    assert texts(bob, dm_conv(phone.node_id)) == []
    # each of Ann's devices ends up with both (history sync)
    for dev in (lap, phone):
        wait_until(lambda d=dev: len(texts(d, dm_conv(bob.node_id))) == 2, 30)


def test_history_sync_with_files_and_groups(lab, cast, tmp_path):
    lap, phone, bob = cast
    cat = lab.node("cat", (1, 1))
    meet(lab, lap, bob, cat)
    gid = lap.mesh.create_group("trip", [bob.node_id, cat.node_id])
    lap.mesh.send_text(group_conv(gid), "plans")
    f = tmp_path / "seat.txt"
    f.write_text("23C")
    lap.mesh.send_file(dm_conv(bob.node_id), str(f), "text/plain")
    wait_until(lambda: "plans" in texts(cat, group_conv(gid)), 15)
    link(lap, phone)
    wait_until(
        lambda: gid in phone.mesh.groups and "plans" in texts(phone, group_conv(gid)),
        40,
    )
    assert phone.mesh.me in phone.mesh.groups[gid].members
    rec = next(r for r in phone.storage.load_files() if r.name == "seat.txt")
    assert rec.state == "complete" and open(rec.path).read() == "23C"
    # the phone talks in the group as Ann
    phone.mesh.send_text(group_conv(gid), "booked")
    wait_until(lambda: "booked" in texts(cat, group_conv(gid)), 15)
    m = next(x for x in msgs(cat, group_conv(gid)) if x.text == "booked")
    assert m.sender == lap.node_id
    wait_until(lambda: "booked" in texts(lap, group_conv(gid)), 15)
    # syncing twice merges without duplicates
    n = len(phone.storage.load_history(group_conv(gid), 1000))
    lap.mesh._sync_to(phone.node_id)
    time.sleep(3)
    assert len(phone.storage.load_history(group_conv(gid), 1000)) == n


def test_offline_device_catches_up(lab, cast):
    lap, phone, bob = cast
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    lab.move("phone", FAR)
    wait_until(lambda: not bob.mesh.reachable(phone.node_id), 30)
    mid = bob.mesh.send_text(dm_conv(lap.node_id), "while your phone is away")
    wait_until(
        lambda: bob.storage.get_message(mid).status.get(lap.node_id) == "delivered", 15
    )
    lab.move("phone", (1, 0))
    wait_until(
        lambda: "while your phone is away" in texts(phone, dm_conv(bob.node_id)), 40
    )


def test_edit_react_delete_across_devices(lab, cast):
    lap, phone, bob = cast
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    conv = dm_conv(bob.node_id)
    mid = lap.mesh.send_text(conv, "typo heer")
    wait_until(
        lambda: (
            "typo heer" in texts(phone, conv)
            and "typo heer" in texts(bob, dm_conv(lap.node_id))
        ),
        15,
    )
    phone.mesh.edit(conv, mid, "typo here")  # edit the laptop's message on the phone
    wait_until(lambda: "typo here" in texts(bob, dm_conv(lap.node_id)), 15)
    wait_until(lambda: "typo here" in texts(lap, conv), 15)
    bid = bob.mesh.send_text(dm_conv(lap.node_id), "nice")
    wait_until(lambda: "nice" in texts(lap, conv), 15)
    lap.mesh.react(conv, bid, "👍")
    wait_until(
        lambda: bob.storage.get_message(bid).reactions == {"👍": [lap.node_id]}, 15
    )
    wait_until(
        lambda: phone.storage.get_message(bid).reactions == {"👍": [lap.node_id]}, 15
    )
    phone.mesh.react(conv, bid, "👍", remove=True)  # same person: removes it
    wait_until(lambda: bob.storage.get_message(bid).reactions == {}, 15)


def test_games_either_device_moves(lab, cast):
    from muninn.gamehost import GameRegistry

    lap, phone, bob = cast
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    regs = {n: GameRegistry(n) for n in (lap, phone, bob)}
    sid = regs[bob].invite(dm_conv(lap.node_id), "tictactoe")
    wait_until(lambda: regs[phone].session_info(sid).get("invitedMe"), 15)
    regs[phone].act(sid, "join", {})  # join on the phone
    wait_until(lambda: regs[lap].session_info(sid).get("started"), 15)
    info = regs[lap].session_info(sid)
    assert lap.node_id in info["players"] and info["amPlayer"]
    assert phone.node_id not in info["players"]
    # bob (seat 0) moves; then Ann moves from the laptop
    regs[bob].act(sid, "move", 4)
    wait_until(lambda: len(regs[lap].session_info(sid)["moves"]) == 1, 15)
    regs[lap].act(sid, "move", 0)
    wait_until(lambda: len(regs[bob].session_info(sid)["moves"]) == 2, 15)
    wait_until(lambda: len(regs[phone].session_info(sid)["moves"]) == 2, 15)
    assert regs[bob].session_info(sid)["moves"][1]["sender"] == lap.node_id


# --------------------------------------------------------------------- unlinking


def test_unlink_and_leave(lab, cast):
    lap, phone, bob = cast
    link(lap, phone)
    wait_until(lambda: bob.mesh.person(phone.node_id) == lap.node_id, 15)
    lap.mesh.unlink(phone.node_id)
    wait_until(lambda: phone.mesh.me == phone.node_id, 15)
    wait_until(lambda: bob.mesh.person(phone.node_id) == phone.node_id, 15)
    bob.mesh.send_text(dm_conv(lap.node_id), "only the laptop now")
    wait_until(lambda: "only the laptop now" in texts(lap, dm_conv(bob.node_id)), 15)
    time.sleep(2)
    assert "only the laptop now" not in texts(phone, dm_conv(bob.node_id))
    # link again, then the phone leaves by itself
    link(lap, phone)
    phone.mesh.unlink(phone.node_id)
    assert phone.mesh.me == phone.node_id  # at once, locally
    wait_until(lambda: lap.mesh.siblings() == [], 15)
    wait_until(lambda: bob.mesh.person(phone.node_id) == phone.node_id, 15)


def test_restart_keeps_link(lab, cast):
    lap, phone, bob = cast
    link(lap, phone)
    lab.stop_node("phone")
    phone2 = lab.node("phone", (1, 0))
    assert phone2.mesh.me == lap.node_id
    bob.mesh.send_text(dm_conv(lap.node_id), "after restart")
    wait_until(lambda: "after restart" in texts(phone2, dm_conv(bob.node_id)), 40)
