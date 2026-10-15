"""Encrypted PACKET bodies. See PROTOCOL.md § Body.

Plaintext layout (inside the NaCl box):
    [kind u8][meta_len u16 BE][meta: UTF-8 JSON object][blob: rest]

`meta` is a compact JSON object (short keys); `blob` carries binary data
(file chunks, thumbnails). Unknown kinds are ignored by receivers.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

K_TEXT = 1  # {g?, t, r?}                        reliable
K_REACT = 2  # {g?, m, e, x?}                    reliable
K_EDIT = 3  # {g?, m, t}                         reliable
K_DELETE = 4  # {g?, m}                          reliable
K_FILE = 5  # {g?, f, n, mt, s, h, cs, c, t?, r?} blob=thumbnail  reliable
K_CHUNK = 6  # {f, i} blob=data                  unreliable
K_FILE_REQ = 7  # {f, w:[idx...]}                unreliable
K_GROUP = 8  # {g, n, v, m:[[id, pubhex]...]}    reliable
K_RECEIPT = 9  # {r:[msgid...]}                  reliable
K_APP = 10  # {g?, a, s, y, b, q?}               reliable
K_RT = 11  # {a, s, b} blob?                     unreliable
K_TYPING = 12  # {g?}                            unreliable
K_GROUP_REQ = 13  # {g}                          unreliable

RELIABLE_KINDS = {K_TEXT, K_REACT, K_EDIT, K_DELETE, K_FILE, K_GROUP, K_RECEIPT, K_APP}

KIND_NAMES = {
    K_TEXT: "text",
    K_REACT: "react",
    K_EDIT: "edit",
    K_DELETE: "delete",
    K_FILE: "file",
    K_CHUNK: "chunk",
    K_FILE_REQ: "file_req",
    K_GROUP: "group",
    K_RECEIPT: "receipt",
    K_APP: "app",
    K_RT: "rt",
    K_TYPING: "typing",
    K_GROUP_REQ: "group_req",
}


@dataclass
class Body:
    kind: int
    meta: dict = field(default_factory=dict)
    blob: bytes = b""

    @property
    def reliable(self) -> bool:
        return self.kind in RELIABLE_KINDS


def encode_body(body: Body) -> bytes:
    meta = json.dumps(body.meta, separators=(",", ":"), ensure_ascii=False).encode()
    if len(meta) > 0xFFFF:
        raise ValueError("body meta too large")
    return struct.pack("!BH", body.kind, len(meta)) + meta + body.blob


def decode_body(data: bytes) -> Body:
    if len(data) < 3:
        raise ValueError("body too short")
    kind, mlen = struct.unpack("!BH", data[:3])
    if len(data) < 3 + mlen:
        raise ValueError("body meta truncated")
    meta = json.loads(data[3 : 3 + mlen].decode("utf-8")) if mlen else {}
    if not isinstance(meta, dict):
        raise ValueError("body meta not an object")
    return Body(kind, meta, data[3 + mlen :])
