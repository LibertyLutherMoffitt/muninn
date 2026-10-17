# Muninn — Wire Protocol v2

The cross-platform contract. Python (`python/src/muninn/protocol.py`,
`content.py`, `mesh.py`) and Kotlin (`android/core/.../Protocol.kt`,
`Mesh.kt`) implement it independently; `python/tests/test_interop.py` runs
both in one simulated cabin.

No compatibility with v1 is kept (v1 databases are wiped on first start).

## Conventions

- Big-endian integers; UTF-8 text; JSON is UTF-8, compact.
- **Node id** — 6 random bytes chosen once per install (shown as
  `AA:BB:CC:DD:EE:FF`), first byte `xxxxxx10` (locally administered). It is
  *not* a Bluetooth address: transports are independent of identity.
- **Keys** — X25519 static keypair per install. Encryption is NaCl `crypto_box`
  (X25519 + XSalsa20-Poly1305): `nonce(24) ‖ mac(16) ‖ ciphertext`.
- Names are at most 255 bytes, cut on a UTF-8 codepoint boundary.
- Unknown frame types and unknown body kinds are ignored; a malformed frame
  costs that frame, never the link.

## Framing

```
[ type u8 ][ length u16 ][ payload … ]      max payload 65 535 bytes
```

| Type | Name    | Scope      | Purpose |
|------|---------|------------|---------|
| 0x01 | HELLO   | link       | identity, key, name — first frame both ways |
| 0x02 | PING    | link       | keepalive (8-byte token) |
| 0x03 | PONG    | link       | echo of PING token (RTT) |
| 0x04 | ROUTES  | link       | distance-vector reachability |
| 0x05 | ROSTER  | link       | who-is-who gossip (ids, keys, names) |
| 0x06 | ADDRS   | link       | radio addresses of the sender's direct links |
| 0x10 | PACKET  | end-to-end | encrypted body, routed by destination |
| 0x11 | ACK     | end-to-end | delivery confirmation, routed to the sender |

Link frames are never forwarded. Mesh frames are forwarded hop by hop.

## Link frames

### HELLO (0x01)

```
pubkey(32) node_id(6) version u8 (=2) features u32 name_ts u32 name_len u8 name
```

Both sides send HELLO immediately on connect and read the peer's within
10 s, else close. Version ≠ 2 or own node id → close.
After HELLO each side sends its full ROSTER and ROUTES.

**Duplicate links** (both dialled at once): every node applies the same rule —
keep the link initiated by the numerically lower node id; if the existing link
has been silent > 10 s, the new link wins regardless.

**Reinstalls**: if a HELLO arrives on a radio address previously used by a
different, currently offline identity with the same (or no) name, the old
identity is *superseded*: history, receipts, reactions and group membership
move to the new id, queued packets are resealed for the new key, and a
REPLACED body is sent to everyone else.

### PING / PONG (0x02 / 0x03)

8-byte token. A node sends PING when it has transmitted nothing for 4 s. A link
that has received nothing for 15 s is dead and is closed (both ends redial).

### ROUTES (0x04)

```
count u8, then count × ( node_id(6) hops u8 )      hops 255 = unreachable
```

Sent on every topology change (debounced 0.3 s) and every 4 s. **Full-table
semantics**: any destination previously learned via this neighbour and absent
from the new frame is withdrawn. Split horizon with poisoned reverse (a route
whose next hop is the receiving neighbour is advertised as 255). Received hop
counts are +1; routes longer than 8 hops are ignored; entries not refreshed for
13 s expire. Direct links are always 1 hop and preferred.

### ROSTER (0x05)

```
count u8, then count × ( node_id(6) pubkey(32) name_ts u32 name_len u8 name )
```

Max 200 entries per frame. On receipt, a new id is added; for a known id the
name is replaced only if `name_ts` is newer. **Keys are pinned** — an entry
whose key differs from the stored key for that id is ignored (reinstalls are
handled by HELLO/REPLACED instead). Changed entries are re-gossiped to the
other links, so names and keys travel any number of hops.

### ADDRS (0x06)

```
count u8, then count × ( node_id(6) radio_addr(6) )
```

Where the sender's direct neighbours can be dialled (the address it sees on
each link). Sent with the initial sync (all current links) and, when a link
comes up, to every other neighbour (just the newcomer). Receivers store an
entry as a *dial hint* for a known peer that they reach only through relays,
and try it with a long back-off (10 s → 5 min) while that peer is ≤ 3 hops
away. Hints are never forwarded. They are what lets two phones that are
both linked to a laptop (but not discoverable) connect directly, and they
never count as evidence for reinstall detection. An entry for the receiver
itself tells it its own radio address — Android apps can't read their own
Bluetooth address, and need it for tap-to-meet.

## Mesh frames

### PACKET (0x10)

```
packet_id(16) src(6) dst(6) ttl u8 flags u8 ts u32 src_pubkey(32) | box
```

- `box = crypto_box(body, sender key → recipient key)`; carrying `src_pubkey`
  lets a recipient decrypt a packet from someone it has never heard of
  (first contact through a relay). TOFU: a packet whose `src_pubkey` differs
  from the pinned key for `src` is dropped.
- `flags`: `0x01` RELIABLE (ACK required), `0x02` BULK (file chunks — relays
  queue them behind chat), `0x04` REALTIME (games/typing — relays send first).
- `ts`: sender's wall clock (seconds). Receivers clamp it to now + 60 s and
  use it to order app events; chat messages are ordered by local arrival.

**Forwarding** (dst ≠ me): drop if `ttl ≤ 1` or `src == me`; drop if this
`packet_id` was forwarded in the last 5 s; otherwise decrement TTL and send to
the best next hop (never back to the neighbour it came from). With no route, a
RELIABLE packet is **stored** (persistently) and handed over as soon as a route
to `dst` appears; unreliable ones are dropped.

**Delivery** (dst = me): decrypt; for RELIABLE send an ACK (also for
duplicates — the first ACK may have been lost) and process only the first copy
of each `packet_id` (persistent dedup); unreliable packets are deduplicated for
60 s.

**Retransmission**: every RELIABLE packet stays in the sender's persistent
outbox until ACKed. It is resent after 6 s, then with doubling back-off capped
at 60 s (±15 % jitter), but only while `dst` is reachable — and immediately
when `dst` becomes reachable again. Nothing ever expires from the outbox.

### ACK (0x11)

```
packet_id(16) src(6) dst(6) ttl u8         src = acknowledging node, dst = original sender
```

Routed toward `dst` like a packet (5 s dedup on `(packet_id, src)`), stored and
forwarded later when no route exists. On arrival the packet leaves the outbox
and the message is marked *delivered* for that recipient.

## Bodies (inside the box)

```
kind u8 meta_len u16 meta(JSON object) blob(rest)
```

| Kind | Name       | Reliable | Meta (blob) |
|-----:|------------|:--------:|-------------|
| 1  | TEXT       | ✓ | `i` msg id hex, `t` text, `g`? group id hex, `r`? reply-to msg id |
| 2  | REACT      | ✓ | `m` target msg, `e` emoji, `x`? 1 = remove, `g`? |
| 3  | EDIT       | ✓ | `m`, `t`, `g`? — only the original sender's edits apply |
| 4  | DELETE     | ✓ | `m`, `g`? — only the original sender's deletes apply |
| 5  | FILE       | ✓ | `i`, `f` file id, `n` name, `mt` mime, `s` size, `h` sha256 hex, `cs` chunk size, `c` chunks, `t`? caption, `r`?, `g`? (blob: JPEG thumbnail ≤ 12 KB) |
| 6  | CHUNK      |   | `f`, `i` index (blob: chunk bytes) |
| 7  | FILE_REQ   |   | `f`, `w` list of wanted indices |
| 8  | GROUP      | ✓ | `g`, `n` name, `v` version, `m` `[[id, pubkey hex], …]` |
| 9  | RECEIPT    | ✓ | `r` list of msg ids the reader has seen |
| 10 | APP        | ✓ | `i` event id, `a` app id, `s` session, `y` type, `b` any JSON, `q` seq, `g`? |
| 11 | RT         |   | `a`, `s`, `b` any JSON (blob optional) — real-time datagram |
| 12 | TYPING     |   | `g`? |
| 13 | GROUP_REQ  |   | `g` — "I got a message for a group I don't know" |
| 14 | REPLACED   | ✓ | `o` old node id, `n` new node id (reinstall notice) |

A group message is sent as one PACKET per member, each encrypted for that
member; all copies share the message id `i`. A DM has no `g`.

### Conversations and receipts

- DM conversation = the peer's node id; group conversation = `g`.
- *delivered* = ACK from that recipient; *read* = RECEIPT from that recipient
  (sent when the conversation is on screen). A group message shows the worst
  state across recipients.

### Groups

Any member may change a group by sending GROUP with `v` + 1 to the union of
old and new members. Receivers accept a GROUP only if `v` is higher than what
they have and (for an existing group) the sender is a current member. A member
that receives a message for an unknown group creates a placeholder and sends
GROUP_REQ; any member answers with GROUP. Leaving = removing yourself.

### Files

Receiver-driven, resumable, relay-friendly. The FILE offer is reliable. The
receiver (automatically up to 15 MB) asks the sender — or, if unreachable, any
other conversation member who has the complete file — for up to 12 chunks at a
time with FILE_REQ; CHUNKs are unreliable BULK packets. Outstanding requests are
re-issued after a 5 s stall or when the source becomes reachable again. Chunk
size 16 000 bytes; files ≤ 64 MB; the result is verified against `h` and
re-downloaded on mismatch.

### Apps and games

APP events form a per-session log; RT datagrams are fire-and-forget. The game
host built on them (invite / join / decline / start / move / resign, state =
`setup` folded over moves) is specified in
`python/src/muninn/games/README.md`.

RT app `~ping` is reserved for end-to-end round trips: `b = "ping"` (blob =
optional padding) is answered with an RT to the sender with the same `a`/`s`,
`b = "pong"` and the same blob. Not delivered to apps.

## Transports

Muninn runs over anything that yields a reliable byte stream:

- **Bluetooth Classic RFCOMM**, service UUID `320bcf9c-94fe-46f4-b9bf-83535cafcd55`.
  Android listens and dials with *insecure* RFCOMM (no bonding). BlueZ
  registers an RFCOMM profile without authentication; known peers may be
  paired (link keys persist), unconfirmed devices are only ever probed
  without pairing.
- **NFC tap-to-meet** (Android, first contact only): the reading phone
  sends `SELECT AID F04D554E494E4E` then `80 10 00 00 Lc <info>`; the other
  phone (host card emulation, unlocked) answers `<info> 90 00`. `info` is
  JSON `{i: node id, k: pubkey hex, n: name, a: own BT address or "", b:
  Bluetooth device name}`. Both sides pin the key (as if gossiped) and dial
  the address if they got one; otherwise the reader becomes discoverable and
  both search by Bluetooth name.
- **Simulator** (`muninn-simhub`): TCP + JSON-lines control protocol, see
  `python/src/muninn/sim/hub.py`.
