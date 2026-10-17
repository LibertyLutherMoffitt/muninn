from muninn import content as C
from muninn import protocol as P


def test_hello_roundtrip():
    f = P.encode_hello(b"k" * 32, "02:AA:BB:CC:DD:EE", "Ålice", 123, 7)
    t, payload = f[0], f[3:]
    assert t == P.T_HELLO
    h = P.decode_hello(payload)
    assert (h.pubkey, h.node_id, h.name, h.name_ts, h.features) == (
        b"k" * 32,
        "02:AA:BB:CC:DD:EE",
        "Ålice",
        123,
        7,
    )


def test_packet_roundtrip_and_ttl():
    pkt = P.Packet(
        b"i" * 16,
        "02:00:00:00:00:01",
        "02:00:00:00:00:02",
        8,
        P.F_RELIABLE | P.F_BULK,
        99,
        b"p" * 32,
        b"x" * 60,
    )
    raw = P.encode_packet(pkt)[3:]
    back = P.decode_packet(raw)
    assert back == pkt
    assert P.decode_packet(P.packet_with_ttl(raw, 3)[3:]).ttl == 3


def test_roster_and_routes():
    es = [
        P.RosterEntry(f"02:00:00:00:00:{i:02X}", bytes([i]) * 32, f"n{i}", i)
        for i in range(5)
    ]
    (frame,) = P.encode_roster(es)
    assert P.decode_roster(frame[3:]) == es
    r = [("02:00:00:00:00:01", 1), ("02:00:00:00:00:02", 255)]
    assert P.decode_routes(P.encode_routes(r)[3:]) == r


def test_body_roundtrip():
    b = C.Body(C.K_FILE, {"n": "cat 🐱.gif", "s": 3}, b"\x00\x01")
    assert C.decode_body(C.encode_body(b)) == b


def test_malformed():
    import pytest

    for fn in (P.decode_packet, P.decode_ack, P.decode_hello):
        with pytest.raises(P.MalformedFrame):
            fn(b"\x00" * 5)


def test_utf8_cut_never_splits_a_codepoint():
    for name in ["é" * 200, "✈" * 100, "a" + "🐍" * 70, "plain"]:
        b = P.utf8_cut(name)
        assert len(b) <= 255
        b.decode("utf-8")  # must not raise
        assert name.startswith(b.decode("utf-8"))


def test_selfupdate_versions_and_asset_names(monkeypatch):
    import sys

    from muninn import selfupdate

    assert selfupdate.newer("0.10.0", "0.9.9") and not selfupdate.newer(
        "0.4.0", "0.4.0"
    )
    assert selfupdate.newer("v1.0", "0.99.99") and not selfupdate.newer("0.3.9", "0.4")
    assert selfupdate.asset_name() is None  # not a frozen build
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/muninn-gui-linux-x86_64")
    assert selfupdate.asset_name() == "muninn-gui-linux-x86_64"
    monkeypatch.setattr(
        sys, "executable", r"C:\Apps\muninn-windows-x86_64.exe".replace("\\", "/")
    )
    assert selfupdate.asset_name() == "muninn-windows-x86_64.exe"
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    assert selfupdate.asset_name() is None
