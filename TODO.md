# Muninn — Gap Analysis & Work Plan

Living document. Goal: from a user's perspective Muninn "just works" on a plane —
devices a few rows apart, lots of unrelated Bluetooth noise, links that come and
go, people that need relaying — on Linux (CLI/GUI) and Android, 1:1 and groups,
plus rich messages (replies, reactions, emoji, files/images/GIFs) and an API for
offline multiplayer games.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **P0..P3** priority.

---

## 1. Status (2026-10-15)

### 1.1 Feature matrix

| Feature                                   | Qt GUI | Terminal (TUI) | Android |
|-------------------------------------------|:------:|:--------------:|:-------:|
| 1:1 + group chat, relayed multi-hop        | ✅ | ✅ | ✅ |
| delivery ✓ / read ✓✓ across relays         | ✅ | ✅ | ✅ |
| replies, reactions, edit, delete           | ✅ | ✅ | ✅ |
| typing indicator                           | ✅ | ✅ | ✅ |
| emoji picker / `:shortcodes:`              | ✅ / ✅ | search / ✅ | keyboard / ✅ |
| files, images, GIFs (resumable, relayed)   | ✅ inline | ✅ open externally | ✅ inline |
| offline sticker pack + save GIFs           | ✅ | send only | ✅ |
| games (11 bundled incl. ultimate², unlimited, 4-player chess; options; zoom) | ✅ | invites only | ✅ |
| real-time game channel (pong)              | ✅ | — | ✅ |
| group create / add / remove / rename / leave / members | ✅ | ✅ | ✅ |
| presence: connected / via relay / nearby / last seen | ✅ | ✅ | ✅ |
| nicknames for others, duplicate-name disambiguation | ✅ | ✅ | ✅ |
| notifications (+ mute per chat)            | ✅ notify-send/tray | bell | ✅ inline reply, private on lock screen |
| search                                     | ✅ | ✅ | ✅ |
| scan modes, polite discovery, /whoami      | ✅ | ✅ | ✅ |
| first-run name onboarding                  | ✅ | ✅ | ✅ |
| vim keys (shared engine), pick messages with k | ✅ | ✅ (default, `/vim off`) | — |
| swipe / hover / menu to reply              | hover bar + menu | k-mode | swipe right |
| app icon, self-update from GitHub releases  | — | — | ✅ |
| standalone builds (no Python/Nix)          | Linux + Windows (CI) | Linux + Windows (CI) | signed APK |
| background / boot / battery                | — | — | ✅ (verified in Doze) |

### 1.2 Verified

- 60+ Python tests: protocol, wire vectors, mesh, chaos (flapping relays,
  weak edges, walking peers, restarts, reinstalls, 40-decoy cabin, 10-seat
  full-cabin soak with files), discovery politeness, games, Python↔Kotlin
  interop incl. a mixed 6-seat soak.
- Kotlin core tests: NaCl vs PyNaCl vectors, wire vectors, UTF-8 cuts.
- Android in the simulated cabin: 12/12 scenarios (tools/android_scenarios.py).
- Android over the real Bluetooth stack (netsim, 3 emulators): automatic
  first contact via polite probe, 2-hop relay with delivery receipt.
- BlueZ on real hardware (magnus): registration, inquiry; nothing probed.
- Qt GUI, TUI and Android UIs driven and screenshotted headlessly.

### 1.3 Known limits

- Windows (WinRT) is untested on hardware.
- Phone ↔ phone first contact needs one side to be visible (Android allows
  5 min) — unless both already share a neighbour (e.g. a laptop), whose
  address hints (ADDRS) let them dial each other directly.
- BlueZ can only dial devices in its cache: a laptop can't dial a hidden
  phone it learned about by hint, but the phone dials the laptop.
- NFC tap-to-pair not implemented (emulators have no NFC to test with; it
  would only save the "Make me visible" tap).

---

## 2. Plan

### 2.1 Test infrastructure (P0)
- [x] `muninn.bt.sim` transport + `muninn-simhub` radio simulator:
  positions (seat rows), range-based links, flaky zone (drops/slow connects),
  latency + bandwidth caps, decoy devices (plane noise), scripted partitions.
- [x] Transport abstraction so the same core runs on bluez / winrt / sim.
- [x] `Node` class (storage + group store + conn manager + acceptor + dialer) shared
  by CLI, GUI, headless bots and tests.
- [x] Headless bot (`muninn-bot`) with JSON control socket for scripted scenarios.
- [x] pytest suite: protocol, storage, routing, scenarios over the sim.
- [x] Kotlin core as a pure-JVM module + JVM node that joins the same sim hub →
  cross-language interop tests without emulators.
- [x] Android app sim transport (debug) → emulators join the sim hub (10.0.2.2).
- [ ] Real-BT emulator↔emulator via netsim (done manually; script it).

### 2.2 Protocol v2 (P0) — additive, old clients ignore unknown frame types
- [x] `PING`/`PONG` keepalive + dead-link detection.
- [ ] `CAPS` frame (version + feature bits).
- [x] `ROUTES` distance-vector frame; route by next hop; expiring routes.
- [x] Rich envelope inside encrypted body: text+reply, reaction, edit, delete,
  file manifest, file chunk, file request, group info (reliable), app/game event.
- [x] `DATAGRAM` frame: encrypted, unreliable, TTL, for real-time games / typing.
- [ ] PROTOCOL.md updated.

### 2.3 Core reliability (P0, both clients)
- [x] Per-peer writer thread + priority queue (control > chat > bulk).
- [x] Periodic retransmit with backoff for every unacked (any route).
- [x] Expiring dedup sets (relay / ack / read).
- [x] Store-and-forward relay queue, bounded, flushed when a route appears.
- [x] Roster gossip multi-hop.
- [x] Reliable group info + member updates (add / leave / rename).
- [x] Persist peer transport addresses; redial known peers without discovery.
- [x] Deterministic duplicate-link tiebreak.
- [x] Robust frame decoding.

### 2.4 Features (P1–P2)
- [x] Replies, reactions, edit/delete, emoji shortcodes + picker (GUI).
- [x] Files / images / GIFs: chunked, resumable, relay-friendly, sha256 verified.
- [x] Typing indicators.
- [x] Android: conversation list, groups, nick, ticks, notifications w/ reply,
  battery-optimisation prompt, boot start, BT on/off handling.
- [x] GUI: read receipts, pending/failed states, route info, images inline.

### 2.5 Games (P2–P3)
- [x] Game session API over reliable app events + datagrams.
- [ ] Chess, checkers (both clients).
- [ ] Real-time demo + latency measurement.

### 2.6 User requests (2026-10-15)
- [ ] **Tap-to-pair (Android↔Android)**: NFC tap exchanges node id + BT address
  (HCE on one side, reader mode on the other), then auto-dial. Fallback: QR code.
- [x] **Send game code over Muninn**: games are self-contained JS plugins, so a
  game can be shared as a file and installed by the receiver (sandboxed).
- [x] **Offline GIFs**: bundled GIF/sticker pack that works with no internet,
  plus "save received GIF" to a local favourites library.
- [x] **Good BT citizen audit** (2026-10-15, table in DESIGN.md): fixed hint
  dials that could pair, laptop permanently pairable, Trusted on paired
  peers, unbounded "look again" probing, 20-min re-probing of strangers.
- [ ] Windows parity (WinRT transport on the shared core; notifications).
- [x] Game API generic (any turn-based game; real-time when latency allows).
- [x] Explicit flapping-link tests (weak links several rows apart, on/off).

### 2.6b Worth porting from origin/master (parallel v1 line, pending user decision)
- [x] Key change / reinstall: reseal queued messages to a peer's new key
  (v2 currently pins the first key and refuses a changed one forever).
- [x] Richer presence: "nearby but won't connect", "last seen 3h ago", sightings.
- [x] Unattended first contact on Linux when BlueZ's cache lacks the UUID
  (theirs probes every visible device; v2 must do it without bothering
  strangers' devices — e.g. SDP-only probe of phone/computer class devices).
- [x] Android background discovery + discoverability prompt flow.
- [x] User-set scan mode (aggressive / balanced / conservative) on all clients.
- [ ] Shared wire-vector conformance file checked by both languages, byte-offset
  protocol tests, storage/dialer unit tests.
- [ ] Android screenshot tests without an emulator (Paparazzi).
- [ ] GUI design tokens + shared PresenceDot/EmptyState, message runs.
- [ ] CLI `/whoami`, presence in `/peers`, "held for …" for queued sends.
- [ ] Docs: TESTING.md, REVIEW.md (Linux vs Windows behaviour), PROTOCOL
  validation rules (UTF-8-safe name truncation — v2 has the same bug).
- [ ] Notifications VISIBILITY_PRIVATE on the lock screen.

### 2.7 Backlog (only after everything else is done & polished)
- [ ] **Linked devices ("same person")** — phone + laptop act as one contact.
  Sketch:
  - Each device keeps its own node id + keypair. A *device set* record
    `{account: <primary node id>, devices: [ids], v, sig}` signed by every
    member's key (cross-signature = consent) is created by a LINK flow:
    device A shows a short code / QR, device B sends `LINK_REQ` with the code,
    A's user approves, both sign the record.
  - The record gossips in ROSTER (new field). Everyone then treats a DM with
    any member as a DM with the *account*: `dm:<account>` fans out to every
    device (like a tiny group); delivered = any device ACKed; read = any read.
  - Sibling devices mirror each other: own sent messages (`K_MIRROR`), read
    marks, reactions, group membership, game moves — so switching devices
    mid-conversation is seamless and others never notice.
  - On link, the new device pulls history from the sibling via the existing
    chunked file transfer (DB export), then stays in sync incrementally.
  - Unlink: re-signed record without the device (old device can't read new
    messages since senders stop encrypting to it).
- [ ] Terminal client as a real TUI (comparable usability to the GUI).

---

## 3. Progress log

(newest last)

- **11e8e76** Python: protocol v2 (HELLO/PING/ROUTES/ROSTER, routed PACKET+ACK),
  Mesh core, Node, sim hub + SimLab, 18 sim tests incl. chaos/flapping/soak,
  GUI rich chat (replies, reactions, edit/delete, emoji, images/GIFs, files,
  games), bot.
- **4b13df3** Kotlin core (pure-JVM, TweetNaCl port == PyNaCl), JVM sim node,
  Python↔Kotlin interop tests (DM, relays both ways, mixed group, files, flaps).
- **36ea102** Android app rewritten on the core; verified in emulator against the
  sim hub (chat w/ bots + Qt GUI, cross-platform tic-tac-toe).
- **d736061** reinstalled peers keep their conversation; UTF-8-safe names.
- **4512eb0** v2 became master (v1 line merged with `-s ours`, history kept).
- **4f0891a** (mislabelled "style: formatter pass") polite discovery of hidden
  peers, scan modes, presence, BlueZ agent hardening, Android background
  inquiry. 34 Python tests + Kotlin tests green.
- **4836779** six more games + plugin test harness. **ded58cd** docs rewritten
  for v2. **6f42b57** byte-exact wire vectors in both languages.
- **b44715c** Android emulator in the simulated cabin: 12/12 scenarios (relay
  both ways, receipts via relay, group across relay, burst over a flapping
  link, GIF).
- Real Bluetooth (netsim, 3 emulators, no simulator): bonded pair reconnects
  in ~2 s; a *fresh* phone finds a visible phone by itself (inquiry → polite
  pairing-free probe), then reaches the third phone 2 hops away; message
  delivered with ✓.
- Real hardware (2026-10-15, Pixel 10 Pro + Pixel 9 + magnus TUI): chat,
  files, relaying through a wall all worked. Found and fixed: phones relayed
  forever instead of linking directly (→ ADDRS address hints, both cores);
  Android's Nearby screen and background scanner cancelled each other's
  inquiries (→ one scan owner, user scans check every phone in sight, ignore
  stale "not Muninn" verdicts); "Make me visible" gave no feedback (→
  countdown card on Nearby + Settings); TUI corrupted by stray prints from the
  BlueZ backend (→ logging to `<data>/muninn.log`, help/command output in a
  scrollable pane, Ctrl-L full repaint); GUI died on NixOS when the flake's
  Mesa ≠ system driver (→ software-renderer fallback); a second front end
  fought the first over BlueZ (→ single-instance lock in Node, clear error).
- 2026-10-16: GUI audit with tools/gui_lab.sh (focus stack, vim MESSAGES
  mode, hover bar, dark context menu, sticky scroll, People-nearby dialog,
  restyled menus/palette/game overlay); four new games + game options +
  host zoom; TUI on the GUI's vim engine (Qt-optional); Android raven icon,
  swipe-to-reply, self-update; signed releases + desktop CI builds.
- Next: more games (chess, checkers, connect4, reversi, real-time demo),
  BT good-citizen audit (bluez agent), Python scan policy, Windows check,
  Android notification/background testing, netsim real-BT test of new app,
  NFC tap-to-pair, docs (PROTOCOL/DESIGN/README), CLI polish.
