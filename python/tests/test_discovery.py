"""Discovery and politeness: find hidden Muninn peers, never bother others."""

import time

from muninn import node, scanpolicy
from muninn.mesh import dm_conv
from muninn.sim.lab import SimLab, wait_until


def test_finds_peer_whose_service_record_is_hidden():
    # Two laptops that never met; inquiry doesn't reveal Muninn on either.
    lab = SimLab(time_scale=0.3, decoys=25, seed=3)
    try:
        a = lab.node("a", (0, 0), advertise=False)
        b = lab.node("b", (1, 0), advertise=False)
        wait_until(lambda: b.node_id in a.mesh.connected(), 60, msg="probe found peer")
        a.mesh.send_text(dm_conv(b.node_id), "found you")
        wait_until(
            lambda: any(
                m.text == "found you"
                for m in b.storage.load_history(dm_conv(a.node_id))
            ),
            15,
        )
        # Good citizen: headsets, watches, peripherals were never contacted.
        st = lab.admin("state")
        attempts = st["connect_attempts"]
        for d in st["devices"]:
            if not d["muninn"] and d["kind"] not in ("phone", "computer"):
                assert attempts.get(d["addr"], 0) == 0, (
                    f"contacted a {d['kind']}: {d['name']}"
                )
        # Strangers' phones get at most a couple of polite probes, not a stream.
        for d in st["devices"]:
            if not d["muninn"]:
                assert attempts.get(d["addr"], 0) <= 2, d
    finally:
        lab.close()


def test_user_scans_stay_polite():
    # Someone mashes "look again" in a full cabin: each scan checks a few
    # strangers' phones, never headsets/watches, and doesn't hammer the same
    # phone over and over.
    lab = SimLab(time_scale=0.3, decoys=40, seed=5)
    try:
        lab.admin("decoys", n=60, hidden_phones=True)
        a = lab.node("a", (0, 0), start=False)
        a.set_scan_mode("quiet")  # only the user's scans
        a.start()
        for _ in range(3):
            a.scan_now(2.0)
            time.sleep(3)
        st = lab.admin("state")
        attempts = st["connect_attempts"]
        strangers = [d for d in st["devices"] if not d["muninn"]]
        for d in strangers:
            if d["kind"] not in ("phone", "computer"):
                assert attempts.get(d["addr"], 0) == 0, d
            assert attempts.get(d["addr"], 0) <= 1, d
        total = sum(attempts.get(d["addr"], 0) for d in strangers)
        assert node.MANUAL_PROBES < total <= 3 * node.MANUAL_PROBES
    finally:
        lab.close()


def test_relayed_neighbours_link_up_directly(lab):
    # a and c sit next to each other but never look around (think: two phones
    # that aren't discoverable). Both know b. b's address hints let them dial
    # each other directly instead of relaying forever.
    a = lab.node("a", (0, 0), start=False, advertise=False)
    c = lab.node("c", (0, 1), start=False, advertise=False)
    paired_style: list[str] = []
    for n in (a, c):
        n.set_scan_mode("quiet")
        real = n.transport.connect

        def connect(addr, *args, _real=real, _n=n, **kw):
            proven = any(a == addr and ok for _, a, ok in _n.storage.load_transports())
            if not proven:  # connect() may pair on real BT
                paired_style.append(addr)
            return _real(addr, *args, **kw)

        n.transport.connect = connect
        n.transport.probe = real  # sim probes are plain connects; don't count them
        n.start()
    b = lab.node("b", (1, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40, msg="relay route")
    wait_until(lambda: c.node_id in a.mesh.connected(), 40, msg="direct link")
    assert a.presence(c.node_id)["state"] == "direct"
    # Hints are hearsay: dialled like strangers (probe), never with connect()
    # — which on real Bluetooth may pair.
    hinted = {lab.addr_of("a"), lab.addr_of("c")}
    assert not hinted & set(paired_style), paired_style
    assert b  # b only relays


def test_quiet_mode_never_inquires_but_known_peers_reconnect(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    a.set_scan_mode("quiet")
    b.set_scan_mode("quiet")
    assert a.storage.get_setting(scanpolicy.SETTING_KEY) == "quiet"
    lab.admin("kill", addr=lab.addr_of("a"))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30, msg="known peer redialled")


def test_presence_states(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    assert a.presence(b.node_id)["state"] == "direct"
    assert a.presence(c.node_id)["state"] == "relay"
    assert "via b" in a.presence(c.node_id)["text"]
    lab.admin("offline", addr=lab.addr_of("c"))
    wait_until(lambda: a.presence(c.node_id)["state"] == "offline", 40)
    assert "last seen" in a.presence(c.node_id)["text"]


def test_scan_policies_match_kotlin():
    import re
    from pathlib import Path

    kt = (
        Path(__file__).resolve().parents[2]
        / "android/core/src/main/kotlin/com/muninn/core/ScanPolicy.kt"
    ).read_text()
    found = {
        m[0]: (float(m[2]), float(m[3]), int(m[4]), float(m[5]), float(m[6]))
        for m in re.findall(
            r'ScanPolicy\("(\w+)", "(\w+)", ([\d.]+), ([\d.]+), (\d+), ([\d.]+), ([\d.]+)\)',
            kt,
        )
    }
    assert set(found) == set(scanpolicy.POLICIES)
    for name, p in scanpolicy.POLICIES.items():
        assert found[name] == (
            p.inquiry_alone,
            p.inquiry_linked,
            p.probe_budget,
            p.probe_backoff,
            p.peer_backoff_max,
        ), name
    assert "DEFAULT = BALANCED" in kt and scanpolicy.DEFAULT is scanpolicy.BALANCED
