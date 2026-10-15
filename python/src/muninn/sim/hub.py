"""muninn-simhub — a virtual Bluetooth radio for many Muninn nodes.

Every simulated device (Python node, JVM node, Android emulator in sim mode)
connects here over TCP. The hub decides who can "hear" whom from seat
positions, and shapes every connection with latency, per-device airtime
(shared bandwidth), connect failures and random drops that get worse with
distance — i.e. a cramped, noisy airplane cabin.

Wire protocol (JSON lines, then raw bytes on data connections):

  control:  -> {"op":"register","addr"?,"name","pos":[row,seat]}
            <- {"ok":true,"addr":"AA:.."}
            <- {"op":"incoming","id":N,"from":"AA:.."}          (async)
            -> {"op":"discover","rid":R}
            <- {"op":"discovered","rid":R,"devices":[{addr,name,muninn,rssi}]}
  data:     -> {"op":"connect","from":A,"to":B}  <- {"ok":true}|{"ok":false,"error"}
            -> {"op":"accept","id":N}            <- {"ok":true}
  admin:    -> {"op":"admin","cmd":...}          <- {"ok":true,...}

Distances are in seat rows (≈0.8 m). Defaults: solid ≤ 3 rows, degrading to
nothing at 7 rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {
    "range_good": 3.0,  # rows: perfect link
    "range_max": 7.0,  # rows: no link beyond
    "bandwidth": 90_000.0,  # bytes/s airtime per device at perfect quality
    "latency_ms": 12.0,  # per hop, perfect quality
    "latency_bad_ms": 80.0,  # added at zero quality
    "drop_rate": 0.25,  # drops/s on an almost-out-of-range link
    "connect_ms": 300.0,  # connect setup at perfect quality
    "connect_bad_ms": 2500.0,
    "page_timeout_ms": 2500.0,  # failed connect to absent / out-of-range device
    "inquiry_ms": 1500.0,  # discovery duration
    "decoys": 0,  # random non-Muninn devices (headphones, watches...)
    "time_scale": 1.0,  # multiplies all delays
    "seed": None,
}


def _rand_addr(rng: random.Random) -> str:
    return ":".join(f"{rng.randrange(256):02X}" for _ in range(6))


@dataclass
class Device:
    addr: str
    name: str
    pos: tuple[float, float]
    muninn: bool = True
    kind: str = "phone"
    advertise: bool = True  # False: service record not visible in inquiry
    writer: asyncio.StreamWriter | None = None  # control channel
    tx_free_at: float = 0.0  # shared airtime
    online: bool = True


@dataclass
class Conn:
    cid: int
    a: str  # initiator
    b: str
    ra: asyncio.StreamReader | None = None
    wa: asyncio.StreamWriter | None = None
    rb: asyncio.StreamReader | None = None
    wb: asyncio.StreamWriter | None = None
    accepted: asyncio.Future | None = None
    tasks: list = field(default_factory=list)
    closed: bool = False
    bytes: int = 0
    created: float = field(default_factory=time.monotonic)


class Hub:
    def __init__(self, **params):
        self.p: dict[str, Any] = dict(DEFAULTS)
        self.p.update({k: v for k, v in params.items() if v is not None})
        self.rng = random.Random(self.p["seed"])
        self.devices: dict[str, Device] = {}
        self.conns: dict[int, Conn] = {}
        self.blocked: set[frozenset] = set()
        self.forced: dict[frozenset, float] = {}  # pair -> quality override
        self.probes: dict[str, int] = {}  # addr -> connect attempts it received
        self.flaps: dict[frozenset, tuple[float, float]] = {}  # pair -> (up_s, down_s)
        self._next = 1
        self.events: list[str] = []
        self._make_decoys(int(self.p["decoys"]))

    # --- radio model ----------------------------------------------------------

    def _make_decoys(self, n: int) -> None:
        names = [
            "AirPods Pro",
            "Galaxy Buds2",
            "WH-1000XM4",
            "Pixel Watch",
            "JBL Flip 5",
            "Bose QC45",
            "Fitbit Charge",
            "iPhone",
            "Galaxy S22",
            "MX Master 3",
            "Kindle",
            "Garmin Venu",
            "Beats Studio",
            "Echo Buds",
            "OnePlus Nord",
        ]
        kinds = {
            "iPhone": "phone",
            "Galaxy S22": "phone",
            "OnePlus Nord": "phone",
            "Kindle": "other",
            "MX Master 3": "peripheral",
            "Pixel Watch": "wearable",
            "Garmin Venu": "wearable",
            "Fitbit Charge": "wearable",
        }
        for _ in range(n):
            addr = _rand_addr(self.rng)
            pos = (self.rng.uniform(0, 40), self.rng.uniform(0, 6))
            name = self.rng.choice(names)
            kind = kinds.get(name, "audio")
            d = Device(addr, name, pos, muninn=False, kind=kind)
            # Other passengers' phones/laptops: inquiry often doesn't reveal
            # their services, so they look exactly like a hidden Muninn peer.
            d.advertise = not (kind == "phone" and self.rng.random() < 0.6)
            self.devices[addr] = d

    def dist(self, a: Device, b: Device) -> float:
        return math.hypot(a.pos[0] - b.pos[0], (a.pos[1] - b.pos[1]) * 0.6)

    def quality(self, a: str, b: str) -> float:
        da, db = self.devices.get(a), self.devices.get(b)
        if da is None or db is None or not da.online or not db.online:
            return 0.0
        pair = frozenset((a, b))
        if pair in self.blocked:
            return 0.0
        if pair in self.flaps:
            up, down = self.flaps[pair]
            if (time.monotonic() % (up + down)) >= up:
                return 0.0
        if pair in self.forced:
            return self.forced[pair]
        d = self.dist(da, db)
        g, m = self.p["range_good"], self.p["range_max"]
        if d <= g:
            return 1.0
        if d >= m:
            return 0.0
        return 1.0 - (d - g) / (m - g)

    def ms(self, v: float) -> float:
        return v / 1000.0 * self.p["time_scale"]

    def log(self, s: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {s}"
        self.events.append(line)
        del self.events[:-500]

    # --- server ---------------------------------------------------------------

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            msg = json.loads(line)
        except Exception:
            writer.close()
            return
        op = msg.get("op")
        if op == "register":
            await self._control(msg, reader, writer)
        elif op == "connect":
            await self._connect(msg, reader, writer)
        elif op == "accept":
            await self._accept(msg, reader, writer)
        elif op == "admin":
            await self._admin_session(msg, reader, writer)
        else:
            writer.close()

    async def _send(self, writer: asyncio.StreamWriter, obj: dict) -> None:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()

    async def _control(self, msg, reader, writer) -> None:
        addr = (msg.get("addr") or _rand_addr(self.rng)).upper()
        pos = tuple(msg.get("pos") or (self.rng.uniform(0, 4), self.rng.uniform(0, 6)))
        dev = self.devices.get(addr)
        if dev is None:
            dev = Device(addr, msg.get("name", ""), pos)
            self.devices[addr] = dev
        else:
            dev.name = msg.get("name", dev.name)
            if msg.get("pos"):
                dev.pos = pos
        dev.writer = writer
        dev.online = True
        dev.muninn = True
        dev.kind = msg.get("kind", dev.kind)
        dev.advertise = bool(msg.get("advertise", True))
        self.log(f"register {addr} {dev.name} at {dev.pos}")
        await self._send(writer, {"ok": True, "addr": addr})
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                m = json.loads(line)
                if m.get("op") == "discover":
                    asyncio.ensure_future(self._discover(dev, m.get("rid")))
                elif m.get("op") == "name":
                    dev.name = m.get("name", dev.name)
        except Exception:
            pass
        finally:
            if dev.writer is writer:
                dev.writer = None
                self.log(f"unregister {addr}")
                self._drop_device(addr)

    async def _discover(self, dev: Device, rid) -> None:
        await asyncio.sleep(self.ms(self.p["inquiry_ms"]))
        out = []
        for other in list(self.devices.values()):
            if other.addr == dev.addr:
                continue
            q = self.quality(dev.addr, other.addr)
            listening = other.writer is not None or not other.muninn
            if q <= 0 or not listening:
                continue
            # Inquiry is unreliable at the edge of range.
            if q < 0.5 and self.rng.random() > q * 2:
                continue
            out.append(
                {
                    "addr": other.addr,
                    "name": other.name,
                    "muninn": other.muninn if other.advertise else None,
                    "kind": other.kind,
                    "rssi": int(-40 - 50 * (1 - q)),
                }
            )
        if dev.writer is not None:
            try:
                await self._send(
                    dev.writer, {"op": "discovered", "rid": rid, "devices": out}
                )
            except Exception:
                pass

    async def _connect(self, msg, reader, writer) -> None:
        a, b = msg["from"].upper(), msg["to"].upper()
        q = self.quality(a, b)
        target = self.devices.get(b)
        self.probes[b] = self.probes.get(b, 0) + 1
        if q <= 0 or target is None:
            await asyncio.sleep(self.ms(self.p["page_timeout_ms"]))
            return await self._fail(writer, "page timeout")
        if not target.muninn:
            await asyncio.sleep(self.ms(self.p["connect_ms"]))
            return await self._fail(writer, "service not found")
        if target.writer is None:
            await asyncio.sleep(self.ms(self.p["connect_ms"]))
            return await self._fail(writer, "connection refused")
        delay = self.p["connect_ms"] + (1 - q) * self.p["connect_bad_ms"]
        await asyncio.sleep(self.ms(delay) * self.rng.uniform(0.7, 1.3))
        if q < 1.0 and self.rng.random() > 0.35 + 0.65 * q:
            return await self._fail(writer, "connect failed (interference)")
        cid = self._next
        self._next += 1
        conn = Conn(cid, a, b, reader, writer)
        conn.accepted = asyncio.get_event_loop().create_future()
        self.conns[cid] = conn
        try:
            await self._send(target.writer, {"op": "incoming", "id": cid, "from": a})
            await asyncio.wait_for(conn.accepted, timeout=self.ms(5000) + 1)
        except Exception:
            self.conns.pop(cid, None)
            return await self._fail(writer, "peer did not accept")
        await self._send(writer, {"ok": True, "id": cid})
        self.log(f"conn {cid} {a} -> {b} q={q:.2f}")
        conn.tasks = [
            asyncio.ensure_future(self._pump(conn, conn.ra, conn.wb, a)),
            asyncio.ensure_future(self._pump(conn, conn.rb, conn.wa, b)),
        ]

    async def _fail(self, writer, err: str) -> None:
        try:
            await self._send(writer, {"ok": False, "error": err})
        finally:
            writer.close()

    async def _accept(self, msg, reader, writer) -> None:
        conn = self.conns.get(int(msg["id"]))
        if conn is None or conn.accepted is None or conn.accepted.done():
            writer.close()
            return
        conn.rb, conn.wb = reader, writer
        await self._send(writer, {"ok": True})
        conn.accepted.set_result(True)

    async def _pump(self, conn: Conn, reader, writer, src_addr: str) -> None:
        """Shape bytes src -> dst: shared airtime + latency, in order."""
        dst_addr = conn.b if src_addr == conn.a else conn.a
        pending: asyncio.Queue = asyncio.Queue()

        async def deliver():
            while True:
                release, data = await pending.get()
                if data is None:
                    return
                wait = release - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                if conn.closed:
                    return
                try:
                    writer.write(data)
                    await writer.drain()
                except Exception:
                    self.close(conn, "write error")
                    return

        dtask = asyncio.ensure_future(deliver())
        try:
            while not conn.closed:
                data = await reader.read(4096)
                if not data:
                    break
                q = max(self.quality(src_addr, dst_addr), 0.05)
                dev = self.devices.get(src_addr)
                now = time.monotonic()
                bw = self.p["bandwidth"] * (0.25 + 0.75 * q)
                airtime = len(data) / bw * self.p["time_scale"]
                start = max(now, dev.tx_free_at if dev else now)
                if dev:
                    dev.tx_free_at = start + airtime
                lat = self.ms(self.p["latency_ms"] + (1 - q) * self.p["latency_bad_ms"])
                conn.bytes += len(data)
                await pending.put((start + airtime + lat, data))
        except Exception:
            pass
        finally:
            await pending.put((0, None))
            self.close(conn, "eof")
            dtask.cancel()

    def close(self, conn: Conn, why: str) -> None:
        if conn.closed:
            return
        conn.closed = True
        self.conns.pop(conn.cid, None)
        self.log(f"close {conn.cid} {conn.a}<->{conn.b}: {why}")
        for w in (conn.wa, conn.wb):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        for t in conn.tasks:
            t.cancel()

    def _drop_device(self, addr: str) -> None:
        for conn in list(self.conns.values()):
            if addr in (conn.a, conn.b):
                self.close(conn, f"{addr} gone")

    async def radio_loop(self) -> None:
        """Break links that went out of range; random drops on weak links."""
        while True:
            await asyncio.sleep(0.25)
            for conn in list(self.conns.values()):
                if conn.wb is None:
                    continue
                q = self.quality(conn.a, conn.b)
                if q <= 0:
                    self.close(conn, "out of range")
                elif q < 1 and self.rng.random() < self.p["drop_rate"] * (
                    1 - q
                ) * 0.25 / max(self.p["time_scale"], 1e-3):
                    self.close(conn, f"radio drop (q={q:.2f})")

    # --- admin ------------------------------------------------------------------

    async def _admin_session(self, first, reader, writer) -> None:
        msg = first
        try:
            while msg is not None:
                try:
                    res = self.admin(msg)
                except Exception as e:
                    res = {"ok": False, "error": repr(e)}
                await self._send(writer, res)
                line = await reader.readline()
                msg = json.loads(line) if line else None
        except Exception:
            pass
        finally:
            writer.close()

    def admin(self, m: dict) -> dict:
        cmd = m.get("cmd")

        def pair():
            return frozenset((m["a"].upper(), m["b"].upper()))

        if cmd == "state":
            return {
                "ok": True,
                "params": self.p,
                "devices": [
                    {
                        "addr": d.addr,
                        "name": d.name,
                        "pos": d.pos,
                        "muninn": d.muninn,
                        "kind": d.kind,
                        "advertise": d.advertise,
                        "online": d.online and (d.writer is not None or not d.muninn),
                    }
                    for d in self.devices.values()
                ],
                "conns": [
                    {
                        "id": c.cid,
                        "a": c.a,
                        "b": c.b,
                        "bytes": c.bytes,
                        "q": round(self.quality(c.a, c.b), 2),
                    }
                    for c in self.conns.values()
                    if c.wb is not None
                ],
                "blocked": [sorted(p) for p in self.blocked],
                "connect_attempts": self.probes,
                "flaps": [[*sorted(p), *v] for p, v in self.flaps.items()],
                "events": self.events[-int(m.get("events", 30)) :],
            }
        if cmd == "move":
            self.devices[m["addr"].upper()].pos = tuple(m["pos"])
        elif cmd == "set":
            for k, v in m["params"].items():
                self.p[k] = v
        elif cmd == "block":
            self.blocked.add(pair())
        elif cmd == "unblock":
            self.blocked.discard(pair())
        elif cmd == "quality":
            if m.get("q") is None:
                self.forced.pop(pair(), None)
            else:
                self.forced[pair()] = float(m["q"])
        elif cmd == "flap":
            if m.get("up") is None:
                self.flaps.pop(pair(), None)
            else:
                self.flaps[pair()] = (float(m["up"]), float(m["down"]))
        elif cmd == "offline":
            d = self.devices[m["addr"].upper()]
            d.online = False
            self._drop_device(d.addr)
        elif cmd == "online":
            self.devices[m["addr"].upper()].online = True
        elif cmd == "kill":
            a = m["addr"].upper()
            for conn in list(self.conns.values()):
                if a in (conn.a, conn.b) and (
                    not m.get("b") or m["b"].upper() in (conn.a, conn.b)
                ):
                    self.close(conn, "admin kill")
        elif cmd == "hide":  # service record invisible to inquiry
            self.devices[m["addr"].upper()].advertise = not m.get("show")
        elif cmd == "decoys":
            self._make_decoys(int(m["n"]))
        elif cmd == "reset":
            self.blocked.clear()
            self.forced.clear()
            self.flaps.clear()
        else:
            return {"ok": False, "error": f"unknown cmd {cmd}"}
        self.log(f"admin {m}")
        return {"ok": True}


async def serve(host: str, port: int, **params) -> tuple[Hub, asyncio.AbstractServer]:
    hub = Hub(**params)
    server = await asyncio.start_server(hub.handle, host, port)
    asyncio.ensure_future(hub.radio_loop())
    return hub, server


def main() -> None:
    ap = argparse.ArgumentParser(description="Muninn virtual Bluetooth radio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7420)
    ap.add_argument("--decoys", type=int, default=0)
    ap.add_argument("--range-good", type=float)
    ap.add_argument("--range-max", type=float)
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()

    async def run():
        hub, server = await serve(
            a.host,
            a.port,
            decoys=a.decoys,
            range_good=a.range_good,
            range_max=a.range_max,
            seed=a.seed,
        )
        print(f"muninn-simhub listening on {a.host}:{a.port}", flush=True)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
