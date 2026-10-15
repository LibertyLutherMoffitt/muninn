# Muninn — Gap Analysis & Work Plan

Living document. Goal: from a user's perspective Muninn "just works" on a plane —
devices a few rows apart, lots of unrelated Bluetooth noise, links that come and
go, people that need relaying — on Linux (CLI/GUI) and Android, 1:1 and groups,
plus rich messages (replies, reactions, emoji, files/images/GIFs) and an API for
offline multiplayer games.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **P0..P3** priority.

---

## 1. Audit findings (state at commit `fb0a241`)

### 1.1 Feature matrix

| Feature                                  | Python core | CLI | GUI | Android |
|------------------------------------------|:-----------:|:---:|:---:|:-------:|
| Handshake / E2EE 1:1                     | ✅ | ✅ | ✅ | ✅ |
| ACK tracking + delivery ticks            | ✅ | ✅ | ✅ | ❌ (logs only) |
| Read receipts                            | ✅ | ✅ | ❌ **never sent** | ❌ |
| Retransmit unacked                       | ⚠️ only on *direct* reconnect of dest | | | ❌ |
| Periodic retry / backoff                 | ❌ | | | ❌ |
| Persistent history                       | ✅ | ✅ | ✅ | ❌ (RAM only) |
| Multiple conversations                   | ✅ | ✅ | ✅ | ❌ (one flat log, sends to *every* peer) |
| Groups                                   | ✅ | ✅ | ✅ | ❌ |
| Relay / multi-hop                        | ⚠️ (see 1.2) | | | ❌ drops non-addressed frames |
| Peer roster gossip (PEER_ANNC)           | ⚠️ 1 hop only | | | ❌ |
| Display names (PROFILE)                  | ✅ | ✅ | ✅ | ❌ |
| Link keepalive / dead-link detection     | ❌ | | | ❌ |
| Auto reconnect to known peers            | ⚠️ discovery-dependent | | | ✅ (15 s loop) |
| Message notifications                    | n/a | ❌ | ✅ notify-send | ❌ |
| Replies / quotes                         | ❌ | ❌ | ❌ | ❌ |
| Reactions                                | ❌ | ❌ | ❌ | ❌ |
| Emoji input                              | n/a | terminal | ❌ no picker / shortcodes | ✅ keyboard |
| Edit / delete                            | ❌ | ❌ | ❌ | ❌ |
| Files / images / GIFs                    | ❌ (64 KB frame cap) | ❌ | ❌ | ❌ |
| Games API                                | ❌ | ❌ | ❌ | ❌ |
| Real-time (unreliable) channel           | ❌ | | | ❌ |
| Typing indicator                         | ❌ | | | ❌ |
| Simulation / automated tests             | ❌ none | | | ❌ |

### 1.2 Reliability bugs in the Python core (peers.py)

1. **Relayed messages are never retried.** Unacked frames are only resent in
   `add_peer()` *for the destination itself*. If A→B→C and the first forward is
   lost, A never retries unless C connects to A directly.
2. **`seen_relayed` never expires.** A relay that forwarded `(msg, dest)` once drops
   every retransmit of it forever — even if the first forward died in a queue.
3. **`seen_acks` / `seen_reads` never expire.** If an ACK was flooded while the
   originator was unreachable, the receiver's re-ACK (sent on duplicate) is dropped
   by every relay that already saw it → originator retransmits forever, message
   shows "sent" forever.
4. **Routing picks the first connected peer, blindly.** `_route_frame` hands the
   frame to any peer, not one that can reach the destination; multi-hop beyond a
   line topology is luck. `indirect_via` is learned but unused for routing.
5. **Relay queue is in-memory and unbounded**; lost on restart, grows forever.
6. **PEER_ANNC only propagates one hop.** In A–B–C–D, D never learns A's pubkey
   until a reconnect → can't DM A even though a path exists.
7. **GROUP_SETUP is fire-and-forget.** If a member is unreachable when the group is
   created and the queued frame is lost (restart), they never get the group;
   messages for it arrive as an unknown `group:<hex>` conversation.
8. **No keepalive.** Half-open RFCOMM sockets (peer walked away / radio glitch) are
   only detected on the next write; reads block indefinitely. Peer shows
   "connected" while it's gone and the queue isn't used.
9. **Sends happen on recv threads.** `sendall()` under a lock from recv loops: two
   nodes whose send buffers fill simultaneously can deadlock each other (both recv
   threads blocked sending). Large frames block chat for everyone.
10. **Scanner dials only devices whose UUIDs BlueZ already has cached** → after the
    first contact, reconnection still depends on discovery luck. Transport
    addresses of peers (esp. Android, whose wire id ≠ BT address) are not persisted.
11. **Duplicate links** resolved by last-wins; both sides can churn.
12. GUI: read receipts never sent (`send_read` only called by CLI).
13. `decode_*` functions trust lengths → malformed frames raise inside recv loop
    (caught as generic Exception → link dropped). Fine, but should be explicit.

### 1.3 Android gaps

Android is at "milestone 3": a single PeerSession per socket, flat message list,
`send()` fans out to *every* connected peer. No storage, no ACK state, no
retransmit, no groups, no relay, no names, no notifications for messages, no
conversation list. All of it must be ported.

### 1.4 Plane-readiness verdict (before this work)

**Not ready.** Two devices in range work. Anything involving a third device, a
link drop at the wrong time, an app restart while messages are queued, or the
Android side, will lose or stall messages silently.

---

## 2. Plan

### 2.1 Test infrastructure (P0)
- [ ] `muninn.bt.sim` transport + `muninn-simhub` radio simulator:
  positions (seat rows), range-based links, flaky zone (drops/slow connects),
  latency + bandwidth caps, decoy devices (plane noise), scripted partitions.
- [ ] Transport abstraction so the same core runs on bluez / winrt / sim.
- [ ] `Node` class (storage + group store + conn manager + acceptor + dialer) shared
  by CLI, GUI, headless bots and tests.
- [ ] Headless bot (`muninn-bot`) with JSON control socket for scripted scenarios.
- [ ] pytest suite: protocol, storage, routing, scenarios over the sim.
- [ ] Kotlin core as a pure-JVM module + JVM node that joins the same sim hub →
  cross-language interop tests without emulators.
- [ ] Android app sim transport (debug) → emulators join the sim hub (10.0.2.2).
- [ ] Real-BT emulator↔emulator via netsim (done manually; script it).

### 2.2 Protocol v2 (P0) — additive, old clients ignore unknown frame types
- [ ] `PING`/`PONG` keepalive + dead-link detection.
- [ ] `CAPS` frame (version + feature bits).
- [ ] `ROUTES` distance-vector frame; route by next hop; expiring routes.
- [ ] Rich envelope inside encrypted body: text+reply, reaction, edit, delete,
  file manifest, file chunk, file request, group info (reliable), app/game event.
- [ ] `DATAGRAM` frame: encrypted, unreliable, TTL, for real-time games / typing.
- [ ] PROTOCOL.md updated.

### 2.3 Core reliability (P0, both clients)
- [ ] Per-peer writer thread + priority queue (control > chat > bulk).
- [ ] Periodic retransmit with backoff for every unacked (any route).
- [ ] Expiring dedup sets (relay / ack / read).
- [ ] Store-and-forward relay queue, bounded, flushed when a route appears.
- [ ] Roster gossip multi-hop.
- [ ] Reliable group info + member updates (add / leave / rename).
- [ ] Persist peer transport addresses; redial known peers without discovery.
- [ ] Deterministic duplicate-link tiebreak.
- [ ] Robust frame decoding.

### 2.4 Features (P1–P2)
- [ ] Replies, reactions, edit/delete, emoji shortcodes + picker (GUI).
- [ ] Files / images / GIFs: chunked, resumable, relay-friendly, sha256 verified.
- [ ] Typing indicators.
- [ ] Android: conversation list, groups, nick, ticks, notifications w/ reply,
  battery-optimisation prompt, boot start, BT on/off handling.
- [ ] GUI: read receipts, pending/failed states, route info, images inline.

### 2.5 Games (P2–P3)
- [ ] Game session API over reliable app events + datagrams.
- [ ] Chess, checkers (both clients).
- [ ] Real-time demo + latency measurement.

### 2.6 User requests (2026-10-15)
- [ ] **Tap-to-pair (Android↔Android)**: NFC tap exchanges node id + BT address
  (HCE on one side, reader mode on the other), then auto-dial. Fallback: QR code.
- [ ] **Send game code over Muninn**: games are self-contained JS plugins, so a
  game can be shared as a file and installed by the receiver (sandboxed).
- [ ] **Offline GIFs**: bundled GIF/sticker pack that works with no internet,
  plus "save received GIF" to a local favourites library.
- [ ] **Good BT citizen audit**: never pair with / dial / prompt devices that
  don't advertise the Muninn UUID; pairing agent must not accept arbitrary
  devices or authorize non-Muninn profiles; no repeated inquiry storms;
  document guarantees.
- [ ] Windows parity (WinRT transport on the shared core; notifications).
- [ ] Game API generic (any turn-based game; real-time when latency allows).
- [ ] Explicit flapping-link tests (weak links several rows apart, on/off).

---

## 3. Progress log

(newest last)
