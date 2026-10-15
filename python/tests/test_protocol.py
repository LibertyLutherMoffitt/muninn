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
