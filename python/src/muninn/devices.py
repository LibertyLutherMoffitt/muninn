"""Linked devices: signed device sets and the device → person index.

Wire format and rules: PROTOCOL.md "DEVICES (0x07)"; design: DESIGN.md
"Linked devices". Mirrored in Kotlin (android/core/.../Devices.kt).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from muninn import xeddsa

MAX_DEVICES = 8


@dataclass(frozen=True)
class Entry:
    node_id: str
    pubkey: bytes
    joined: int  # 0 for the primary
    consent: bytes  # b"" for the primary


@dataclass
class DeviceSet:
    account: str
    version: int
    entries: list[Entry]
    sig: bytes = b""

    @property
    def devices(self) -> list[str]:
        return [e.node_id for e in self.entries]

    def entry(self, node_id: str) -> Entry | None:
        return next((e for e in self.entries if e.node_id == node_id), None)

    def body(self) -> bytes:
        """What the primary signs."""
        out = f"muninn-devices\n{self.account}\n{self.version}\n"
        for e in self.entries:
            out += f"{e.node_id} {e.pubkey.hex()} {e.joined} {e.consent.hex()}\n"
        return out.encode()

    def to_json(self) -> str:
        return json.dumps(
            {
                "a": self.account,
                "v": self.version,
                "d": [
                    [e.node_id, e.pubkey.hex(), e.joined, e.consent.hex()]
                    for e in self.entries
                ],
                "s": self.sig.hex(),
            },
            separators=(",", ":"),
        )

    @staticmethod
    def from_json(s: str | bytes) -> DeviceSet:
        j = json.loads(s)
        entries = [
            Entry(str(d[0]), bytes.fromhex(d[1]), int(d[2]), bytes.fromhex(d[3]))
            for d in j["d"]
        ]
        return DeviceSet(str(j["a"]), int(j["v"]), entries, bytes.fromhex(j["s"]))


def consent_body(account: str, node_id: str, joined: int) -> bytes:
    return f"muninn-join\n{account}\n{node_id}\n{joined}".encode()


def make_consent(priv: bytes, account: str, node_id: str, joined: int) -> bytes:
    return xeddsa.sign(priv, consent_body(account, node_id, joined))


def leave_body(account: str, node_id: str, joined: int) -> bytes:
    return f"muninn-leave\n{account}\n{node_id}\n{joined}".encode()


@dataclass(frozen=True)
class Leave:
    """A device's own signed statement that it left `account` (the membership
    that started at `joined`). Valid without the first device, so a phone can
    stand alone again even if the laptop it was linked to is lost."""

    account: str
    node_id: str
    joined: int
    sig: bytes

    def to_json(self) -> str:
        return json.dumps(
            {
                "leave": {
                    "a": self.account,
                    "n": self.node_id,
                    "j": self.joined,
                    "s": self.sig.hex(),
                }
            },
            separators=(",", ":"),
        )

    @staticmethod
    def from_obj(j: dict) -> Leave:
        return Leave(str(j["a"]), str(j["n"]), int(j["j"]), bytes.fromhex(j["s"]))


def make_leave(priv: bytes, account: str, node_id: str, joined: int) -> Leave:
    return Leave(
        account,
        node_id,
        joined,
        xeddsa.sign(priv, leave_body(account, node_id, joined)),
    )


def check_leave(lv: Leave, pinned: Callable[[str], bytes | None]) -> bool:
    pk = pinned(lv.node_id)
    return pk is not None and xeddsa.verify(
        pk, leave_body(lv.account, lv.node_id, lv.joined), lv.sig
    )


def sign(ds: DeviceSet, priv: bytes) -> DeviceSet:
    ds.entries = sorted(ds.entries, key=lambda e: e.node_id)
    ds.sig = xeddsa.sign(priv, ds.body())
    return ds


def check(ds: DeviceSet, pinned: Callable[[str], bytes | None]) -> str | None:
    """None if valid, else why not. `pinned(id)` → the key we pinned for an
    id (None if we don't know it yet)."""
    ids = ds.devices
    if ds.version < 1 or not 1 <= len(ids) <= MAX_DEVICES:
        return "bad size/version"
    if len(set(ids)) != len(ids) or ids != sorted(ids):
        return "devices not unique/sorted"
    prim = ds.entry(ds.account)
    if prim is None:
        return "account not listed"
    for e in ds.entries:
        known = pinned(e.node_id)
        if known is not None and known != e.pubkey:
            return f"key mismatch for {e.node_id}"
        if e.node_id == ds.account:
            if e.joined != 0 or e.consent:
                return "primary entry carries a consent"
        elif not xeddsa.verify(
            e.pubkey, consent_body(ds.account, e.node_id, e.joined), e.consent
        ):
            return f"no valid consent from {e.node_id}"
    if not xeddsa.verify(prim.pubkey, ds.body(), ds.sig):
        return "bad primary signature"
    return None


@dataclass
class Index:
    """All valid sets we know, and which person each device is."""

    sets: dict[str, DeviceSet] = field(default_factory=dict)
    # signed leaves: (account, device) -> the join ts the device walked away from
    revoked: dict[tuple[str, str], int] = field(default_factory=dict)

    def newer(self, ds: DeviceSet) -> bool:
        cur = self.sets.get(ds.account)
        return cur is None or ds.version > cur.version

    def owner(self, node_id: str) -> str:
        """The person (account id) a device belongs to."""
        best, best_ts = node_id, -1
        for ds in self.sets.values():
            e = ds.entry(node_id)
            if e is None or len(ds.entries) < 2:
                continue
            if self.revoked.get((ds.account, node_id), -1) >= e.joined:
                continue  # it left (a newer consent would bring it back)
            if e.joined > best_ts:
                best, best_ts = ds.account, e.joined
        return best

    def devices(self, person: str) -> list[str]:
        ds = self.sets.get(person)
        if ds is None:
            return [person]
        out = [d for d in ds.devices if self.owner(d) == person]
        return out or [person]
