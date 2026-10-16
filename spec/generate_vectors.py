#!/usr/bin/env python3
"""Generate spec/wire-vectors.json from the Python codecs.

Both cores are tested against this file (python/tests/test_wire_vectors.py,
android/core WireVectorsTest): every vector must decode to the given fields
and re-encode to exactly the given bytes. Regenerate after an intentional
protocol change:

    cd python && PYTHONPATH=src python ../spec/generate_vectors.py
"""

import json
from pathlib import Path

from muninn import content as C
from muninn import protocol as P

A = "02:11:22:33:44:55"
B = "06:AA:BB:CC:DD:EE"
PK = bytes(range(32))


def hx(b: bytes) -> str:
    return b.hex()


def main() -> None:
    v: dict = {
        "hello": [],
        "routes": [],
        "roster": [],
        "addrs": [],
        "packet": [],
        "ack": [],
        "body": [],
    }
    for name, ts in [
        ("", 0),
        ("Ålice ✈", 1760000000),
        ("x" * 300, 4294967295),
        ("é" * 140, 7),
    ]:
        v["hello"].append(
            {
                "pubkey": hx(PK),
                "node_id": A,
                "name": name,
                "name_ts": ts,
                "features": 5,
                "frame": hx(P.encode_hello(PK, A, name, ts, 5)),
                "decoded_name": P.decode_hello(
                    P.encode_hello(PK, A, name, ts, 5)[3:]
                ).name,
            }
        )
    for entries in [[], [(A, 1), (B, 255)], [(B, 7)]]:
        v["routes"].append(
            {
                "entries": [[i, h] for i, h in entries],
                "frame": hx(P.encode_routes(entries)),
            }
        )
    roster = [P.RosterEntry(A, PK, "Bob", 12), P.RosterEntry(B, bytes(32), "", 0)]
    v["roster"].append(
        {
            "entries": [[e.node_id, hx(e.pubkey), e.name, e.name_ts] for e in roster],
            "frame": hx(P.encode_roster(roster)[0]),
        }
    )
    for entries in [[], [(A, "98:98:FB:38:87:EF"), (B, "00:1A:7D:DA:71:13")]]:
        v["addrs"].append(
            {
                "entries": [[i, a] for i, a in entries],
                "frame": hx(P.encode_addrs(entries)),
            }
        )
    pkt = P.Packet(
        bytes(range(16)),
        A,
        B,
        8,
        P.F_RELIABLE | P.F_BULK,
        1760000000,
        PK,
        bytes(range(40)),
    )
    v["packet"].append(
        {
            "packet_id": hx(pkt.packet_id),
            "src": A,
            "dst": B,
            "ttl": 8,
            "flags": pkt.flags,
            "ts": pkt.ts,
            "src_pubkey": hx(PK),
            "box": hx(pkt.box),
            "frame": hx(P.encode_packet(pkt)),
            "ttl3_frame": hx(P.packet_with_ttl(P.encode_packet(pkt)[3:], 3)),
        }
    )
    ack = P.Ack(bytes(range(16)), B, A, 8)
    v["ack"].append(
        {
            "packet_id": hx(ack.packet_id),
            "src": B,
            "dst": A,
            "ttl": 8,
            "frame": hx(P.encode_ack(ack)),
        }
    )
    for kind, meta, blob in [
        (C.K_TEXT, {"i": "00" * 16, "t": "hi ✈"}, b""),
        (C.K_CHUNK, {"f": "ab" * 16, "i": 3}, bytes(range(10))),
        (
            C.K_APP,
            {"a": "chess", "s": "s1", "y": "move", "b": {"m": "e2e4"}, "q": 1},
            b"",
        ),
    ]:
        b = C.Body(kind, meta, blob)
        v["body"].append(
            {
                "kind": kind,
                "meta": meta,
                "blob": hx(blob),
                "bytes": hx(C.encode_body(b)),
            }
        )
    out = Path(__file__).resolve().parent / "wire-vectors.json"
    out.write_text(json.dumps(v, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
