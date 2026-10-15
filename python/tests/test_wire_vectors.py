"""Byte-exact conformance with spec/wire-vectors.json (also checked by Kotlin)."""

import json
from pathlib import Path

from muninn import content as C
from muninn import protocol as P

V = json.loads(
    (Path(__file__).resolve().parents[2] / "spec/wire-vectors.json").read_text()
)


def b(h):
    return bytes.fromhex(h)


def test_hello():
    for v in V["hello"]:
        assert (
            P.encode_hello(
                b(v["pubkey"]), v["node_id"], v["name"], v["name_ts"], v["features"]
            ).hex()
            == v["frame"]
        )
        h = P.decode_hello(b(v["frame"])[3:])
        assert (h.node_id, h.name, h.name_ts, h.features) == (
            v["node_id"],
            v["decoded_name"],
            v["name_ts"],
            v["features"],
        )


def test_routes_roster_packet_ack():
    for v in V["routes"]:
        entries = [tuple(e) for e in v["entries"]]
        assert P.encode_routes(entries).hex() == v["frame"]
        assert P.decode_routes(b(v["frame"])[3:]) == entries
    for v in V["roster"]:
        es = [P.RosterEntry(e[0], b(e[1]), e[2], e[3]) for e in v["entries"]]
        assert P.encode_roster(es)[0].hex() == v["frame"]
        assert P.decode_roster(b(v["frame"])[3:]) == es
    for v in V["packet"]:
        pkt = P.decode_packet(b(v["frame"])[3:])
        assert (pkt.src, pkt.dst, pkt.ttl, pkt.flags, pkt.ts) == (
            v["src"],
            v["dst"],
            v["ttl"],
            v["flags"],
            v["ts"],
        )
        assert P.encode_packet(pkt).hex() == v["frame"]
        assert P.packet_with_ttl(b(v["frame"])[3:], 3).hex() == v["ttl3_frame"]
    for v in V["ack"]:
        a = P.decode_ack(b(v["frame"])[3:])
        assert (a.src, a.dst, a.ttl) == (v["src"], v["dst"], v["ttl"])
        assert P.encode_ack(a).hex() == v["frame"]


def test_bodies():
    for v in V["body"]:
        body = C.Body(v["kind"], v["meta"], b(v["blob"]))
        assert C.encode_body(body).hex() == v["bytes"]
        assert C.decode_body(b(v["bytes"])) == body
