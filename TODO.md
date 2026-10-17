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
- [x] Real-BT emulator↔emulator via netsim: `tools/netsim_lab.py` (up/meet/ping/bench).

### 2.2 Protocol v2 (P0) — additive, old clients ignore unknown frame types
- [x] `PING`/`PONG` keepalive + dead-link detection.
- [ ] `CAPS` frame (version + feature bits).
- [x] `ROUTES` distance-vector frame; route by next hop; expiring routes.
- [x] Rich envelope inside encrypted body: text+reply, reaction, edit, delete,
  file manifest, file chunk, file request, group info (reliable), app/game event.
- [x] `DATAGRAM` frame: encrypted, unreliable, TTL, for real-time games / typing.
- [x] PROTOCOL.md updated.

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
- [x] Chess, checkers (both clients).
- [x] Real-time demo + latency measurement (`:ping`/`/ping`, netsim ~20 ms RTT; see TESTING.md).

### 2.6 User requests (2026-10-15)
- [x] **Tap-to-pair (Android↔Android)**: NFC tap exchanges node id + BT address
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
- [x] Shared wire-vector conformance file checked by both languages, byte-offset
  protocol tests, storage/dialer unit tests.
- [ ] Android screenshot tests without an emulator (Paparazzi).
- [ ] GUI design tokens + shared PresenceDot/EmptyState, message runs.
- [x] CLI `/whoami`, presence in `/peers`, "held for …" for queued sends.
- [x] Docs: TESTING.md, PROTOCOL validation rules (UTF-8-safe name
  truncation). (A Linux-vs-Windows REVIEW.md waits for the Windows pass.)
- [x] Notifications VISIBILITY_PRIVATE on the lock screen.

### 2.6c User requests (2026-10-16) — in progress now
- [x] NFC tap-to-pair (Android↔Android) — built; needs a hardware test: HCE + reader mode exchange
  identity/key/address; fall back to one "visible" prompt + name search.
- [x] Delete chats / forget / block people, leave+delete rooms; multi-select
  on GUI + Android, commands in terminal.
- [x] Scoreboard: who won how many of each game (per conversation + overall).
- [x] Unsend a game invitation (creator cancels before/while others join).

### 2.6d Ideas (2026-10-16, nearer the backlog)
- [x] Extensive game testing, especially end/win conditions of every game (tools/gamecheck.py + hand-built end positions).
- [x] Spectators: everyone in a group can watch a 2-player game live.
- [x] Easy game import (pick a .js on desktop/phone) and one-tap "send this
  game to …" so the code spreads through Muninn itself.
- [x] GAME_API docs written for AIs (games/README.md, example kept valid by a test): one self-contained document with the
  protocol, plugin contract, canvas subset, examples and a checklist, so a
  model can write a game without reading the repo.
- [ ] Screen share for group watching (anime etc.), mostly from phones:
  adaptive low-res/low-fps video over the mesh (budget ~5–15 kB/s per
  hop; think 240p at 1–5 fps, keyframes + diffs).
- [x] Auto-update for the Linux/Windows executables (selfupdate.py, checksummed).
- [x] Rethink scan modes (done 2026-10-16: reconnect by address, fast/saver; temporary find mode): explain clearly whether they govern discovery of
  new people, reconnecting to known ones, or both. Proposal from the user:
  reconnecting to known people should just be fast and reliable (optionally
  a battery-saving "lazy" mode), while looking for *new* people becomes a
  temporary "Find people" mode (on for a minute or until turned off),
  because new contacts are rare.
- [x] Desktop GUI keeps running in the background with a tray icon (Linux,
  Windows, Nix) so messages arrive with the window closed.

### 2.6e Suggestions (2026-10-17)
- [ ] **Hardware session** (most valuable next step): tap-to-meet, find mode,
  laptop-as-middleman direct links, reconnect after walking away, file
  throughput, battery drain over a few hours with the screen off, and that
  no phone ever offers magnus as an audio device again.
- [x] **Bubble Tea terminal client** (`tea/`, Go, Linux + Windows): plain
  keys + mouse, Ctrl+K palette, bubbles with replies/reactions/ticks/file
  progress, emoji completion, toasts, spring scrolling, narrow layout. Drives
  the Python core via `muninn --api` (stdio JSON, `api.py`), so no protocol
  port. No games. Tests: Go unit tests + tmux end-to-end (`test_tea.py`).
- [ ] (later — everyone updates a week before the trip) **Protocol versioning** (`CAPS` / feature
  bits in HELLO): today a v2 node refuses any other version outright, so a
  friend who hasn't updated can't talk to you mid-flight. Make HELLO carry
  a feature set, accept older peers, and degrade features per link.
- [x] `:doctor` / Settings → "Check before the flight": Bluetooth on,
  permissions, battery optimisation off, background running, version vs
  friends', last time each friend was seen, whether find mode is needed.
- [x] Privacy toggles: read receipts and typing indicators on/off.
- [x] Export / import chat history (`:export`/`:import`, Settings → Backup).
- [x] Tap phones to add someone to a room (Room info → Add people → Tap phones; works before any radio link).
- [x] React / reply from the Android notification (👍 + reply).
- [x] Desktop zoom (Ctrl +/-/0, :zoom) and light theme (:theme light|dark|system).
- [x] Real-time latency: end-to-end `ping` over the game datagram path
  (relays included) in both cores; netsim numbers + playability note in
  TESTING.md. Real-air numbers: hardware session.
- [ ] Windows hardware pass (WinRT): dialing phones without a bond may not
  work — phones dial the PC; verify, plus tray/notifications.

### 2.7 Backlog (only after everything else is done & polished)
- [x] **Linked devices ("same person")** — phone + laptop act as one contact.
  Done as designed in DESIGN.md "Linked devices" (primary-signed sets +
  per-device consent, XEdDSA with the existing keys; persons everywhere;
  fan-out incl. own devices; history sync as a backup over file transfer).
  `:link`/`:unlink`/`:devices`, `/link…`, Android Settings → Linked devices.
  Tests: tests/test_devices.py (11), interop both directions, Kotlin unit.
  Original sketch:
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
- [x] Terminal client as a real TUI (comparable usability to the GUI).

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
- 2026-10-16/17 (v0.3 → v0.8): "Muninn never pairs" after a bond made a
  phone use the laptop as speakers; NFC tap to meet / to add someone to a
  room; forget / block / delete chats; pre-flight `:doctor`, privacy
  switches, find mode + reconnect speeds; checkers rules (Turkish default,
  6×6), ultimate², unlimited and 4-player chess, scores, spectators, unsend;
  self-update on every platform; photos shrunk before sending; light theme;
  backup / restore (same file on desktop and Android); `:ping` and the
  netsim lab (found two finders paging each other mid-inquiry → retry
  ladder + jitter); **linked devices** in both cores (XEdDSA-signed sets,
  persons everywhere, history sync); the **Bubble Tea client**.
- Review (2026-10-17): backup / history-sync imports used ids from the zip
  as directory names and file names could be `..` → ids must be hex, names
  sanitised, foreign thumbnail paths dropped (test added); Bubble Tea client
  could wait 60 s on exit and raced on the core's stderr → fixed.
- Next: the hardware session (real-air latency, battery, walking away),
  Windows hardware pass, protocol version negotiation before mixing versions.
