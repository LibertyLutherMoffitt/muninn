# Muninn — Design

## Overview

Encrypted chat for people who are physically near each other and digitally
cut off — an airplane cabin above all. Bluetooth Classic only; no internet,
cellular or Wi-Fi infrastructure, no accounts. Every device is also a relay,
so the cabin forms a mesh: a message hops seat to seat, or waits (persisted)
until a path appears.

What "just works" has to mean on a plane:

- people a couple of rows apart find each other without setup;
- links that drop every few seconds lose nothing and duplicate nothing;
- someone out of range still gets their messages, via others or later;
- restarts, reboots and reinstalls never strand a conversation;
- forty strangers' headsets, watches and phones are left alone.

## Architecture

```
            ┌───────────── UI ──────────────┐
            │ Qt/QML GUI · TUI/CLI · bot    │ Android Compose
            │ Bubble Tea (Go) ── stdio JSON ┤ (api.py)
            └──────────────┬────────────────┘
                           │ events / API
            ┌──────────────▼────────────────┐
            │ Node: identity, dialer,       │   scan policy, presence,
            │       discovery, presence     │   polite probing
            ├───────────────────────────────┤
            │ Mesh: links, routing, outbox, │   one implementation per
            │ relay store, gossip, groups,  │   language: Python mesh.py,
            │ files, apps, reinstalls       │   Kotlin Mesh.kt
            ├───────────────────────────────┤
            │ Storage (SQLite) · Crypto     │
            ├───────────────────────────────┤
            │ Transport: BlueZ · WinRT ·    │
            │ Android RFCOMM · simulator    │
            └───────────────────────────────┘
```

Two implementations of the core — Python (desktop CLI/GUI/bot) and Kotlin
(Android, also built for the JVM) — follow the same spec, [`PROTOCOL.md`](PROTOCOL.md),
and are tested against each other in one simulated cabin. Duplication is cheap
at this size and avoids threading a generated IDL through two ecosystems.

### Identity

A random 6-byte node id and an X25519 keypair per install. Identity is not
tied to a Bluetooth address (Android hides its own; laptops change adapters).
Keys are pinned on first contact. A reinstall (same radio address, same name,
new identity) is detected and the conversation migrated.

### Linked devices ("same person")

A laptop and a phone can act as one person. Each device keeps its own node id
and keypair (routing, ACKs and encryption stay per device); on top sits a
**device set**: `{account, version, devices}` where the account id is the
node id of the *primary* (the device the set was created on). The primary
signs the set; every other device signs a one-time *consent* to join that
account ("muninn-join", account, its id, join time). Both are XEdDSA
signatures made with the device's X25519 identity key, so peers verify them
against keys they already pinned — nobody can add someone else's device to an
account, and only the primary can change the set. Sets travel as a link frame
(DEVICES) and flood the mesh like the roster.

Everyone then speaks in *persons*: `person(device)` = the account the device
belongs to (itself if none). Stored history, senders, receipts, reactions,
group members and game players are person ids; when a set arrives, rows that
named a member device are rewritten to the account (like a reinstall
migration), so earlier separate chats with the laptop and the phone become one.
Sending fans out at the last moment: a message to a person goes to every one
of their devices, *and* to the sender's own other devices (a DM carries `to`,
the other person, so a sibling files it under the right chat). Delivered =
any device of that person ACKed; read = any device read it. Siblings mirror
read marks (receipts to siblings are always sent, even with read receipts
off), see their own messages as "you", and can edit/delete/react on each
other's behalf because the sender is the person. A new sibling first receives
each other device's history as a backup file over the normal file transfer;
after that, fan-out keeps them in step (offline devices catch up through the
outbox like anyone else).

Linking: on the primary (or a device on its own), `:link` shows a 6-digit
code for 5 minutes; on the other device `:link <name> <code>` sends a LINK
request (encrypted to the primary) carrying its consent. The primary checks
the code (5 tries), adds the device, signs the new version and floods it.
Unlinking: the primary publishes a version without the device; a device can
ask to leave, and stops treating itself as linked immediately. Losing the
primary means the set can't change any more (out of scope — keep it).

### Links

Any reliable byte stream. HELLO exchanges id, key and name; PING keeps the
link honest (dead after 15 s of silence, so half-open RFCOMM sockets don't
pretend to be connected). Each link has a reader and a writer thread; the
writer drains a priority queue (control > real-time > chat > bulk) so a file
transfer never delays a message and no reader ever blocks on a full socket.

### Routing and delivery

Distance-vector routing (full-table adverts every 4 s and on change, split
horizon + poisoned reverse, 13 s expiry, max 8 hops). Every reliable packet is
persisted in the sender's outbox and retried with back-off while the
destination is reachable — and immediately when it becomes reachable again —
until an end-to-end ACK arrives. Relays forward by next hop, deduplicate for
5 s (so retransmits still pass), and store-and-forward to disk when there is no
route. ACKs are routed back the same way. Receivers deduplicate by packet id
forever, so retries never show twice.

### Content

All user content is a typed, encrypted body (text with reply-to, reaction,
edit, delete, file offer/chunk/request, group info, read receipt, app event,
real-time datagram, typing, reinstall notice, device link). Group messages are encrypted
per member. Files are pulled by the receiver in windows of chunks, from the
sender or any member that has the file, resumable and hash-verified.

### Games

A game is one JavaScript file with a small pure API (setup / turn / move /
status / render / input, optional real-time tick). Both GUIs host the same
file (QML Canvas, Android WebView canvas). The code travels inside the invite,
so all players run identical rules. State = setup folded over the ordered move
log; illegal or out-of-turn moves are ignored identically everywhere.

### Discovery (good Bluetooth citizen)

What Muninn does to a device that isn't running Muninn — the complete list
(audited 2026-10-15; `tests/test_discovery.py` enforces the numbers):

| Device | What can happen | How often |
|---|---|---|
| Headset, watch, mouse, speaker, "other" | nothing — never dialled, probed, paired or SDP-queried | never |
| Stranger's phone/laptop, *visible* and not revealing its services | one pairing-free probe (SDP lookup of the Muninn UUID; no prompt, no bond) — **only in find mode** | ≤ 3 per look; after a miss it is retried once ~45 s later (a busy radio is the usual cause), then after 10 min, then left alone for 6 h |
| …same, user taps "look again" | probe the 6 strongest unknowns | a device at most once a minute |
| Stranger's phone that is *not* visible (most phones) | nothing — it never shows up in an inquiry | never |
| Any device | hears our inquiry (a broadcast; every phone's BT settings screen does the same) | **only in find mode**: every 15–23 s (jittered, so two phones both looking drift apart instead of paging each other mid-inquiry) for 5 min after you open Nearby / ⟳ / `:find`, or while you know nobody; otherwise never |

**Muninn never pairs** — no bonds, no "Pair with …?" prompts, no Trusted
flag, no pairing agent. A bond is what lets a phone treat a laptop as a
headset/speaker (that happened with Muninn ≤ 0.2, which paired as a
fallback and marked the peer Trusted); Muninn's RFCOMM profile is open on
both sides, so links never need one. On start, Muninn clears Trusted on its
peers; `:unpair` / `/unpair` removes old bonds. The laptop is discoverable
but **not pairable**, its previous discoverable state is restored on exit.
Android uses insecure RFCOMM throughout (never bonds), becomes discoverable
only when the user asks, and never cancels another app's Bluetooth scan
(e.g. the user pairing headphones in Settings) — it skips its own look
instead.

Known Muninn peers are redialled by address with a short, capped back-off
(*fast*: ≤ 15 s, *battery saver*: ≤ 60 s) — no scanning involved; that only
pages devices that ran Muninn with us before. Finding *new* people is a
separate, temporary **find mode**, because new contacts are rare.

## Clients

| | Desktop (Python core) | Android (Kotlin) |
|---|---|---|
| UI | Qt 6/QML GUI (Vim-modal composer, palette, light/dark); curses TUI + line CLI; Bubble Tea client (Go, plain keys, `tea/`) | Jetpack Compose |
| Transport | BlueZ (D-Bus profile), WinRT (untested), simulator | insecure RFCOMM, NFC tap to meet, simulator |
| Notifications | notify-send / tray | per-conversation, inline reply + 👍 |
| Background | tray (`:background`), autostart | foreground service, boot start |
| Updates | self-update of the standalone builds (checksummed) | in-app updater (same signing key) |

The Bubble Tea client holds no protocol logic: it starts the Python core as
`muninn --api` and speaks newline-delimited JSON with it over stdio
(`api.py`), so one tested core serves every desktop front end and the Go
binary cross-compiles for Windows next to the CLI it drives.

## Transport details

- RFCOMM service UUID `320bcf9c-94fe-46f4-b9bf-83535cafcd55`.
- BlueZ: profile registered via `ProfileManager1` (no authentication
  required); `ConnectProfile` is called asynchronously and the socket arrives
  through `NewConnection`. Nothing is ever paired (see above); the adapter is
  discoverable but not pairable while Muninn runs, re-asserted every 2
  minutes and restored on exit.
- Android: `listenUsingInsecureRfcommWithServiceRecord` /
  `createInsecureRfcommSocketToServiceRecord` — no bonding, no prompts.
  Android can't be discovered unless the user allows it (≤ 5 min), but it
  finds and dials laptops and known peers on its own.
- Windows: discovery only sees paired devices and discoverability can't be
  set programmatically, so phones dial the PC (it listens on the Muninn
  service). Untested on hardware.

## Security

NaCl box (X25519 + XSalsa20-Poly1305), static keys, no forward secrecy, TOFU
key pinning, routing metadata in the clear for relays. MITM at first contact
and storage limits are out of scope. Linked-device sets are XEdDSA-signed
with the same X25519 keys (the first device signs the set, every other
device its own consent), so peers verify them against keys they already
pinned. Backups include the identity key only with a password (PBKDF2-SHA256
→ XSalsa20-Poly1305); importing never writes outside the files directory.

## Legal

Bluetooth in airplane mode is permitted on commercial flights (FAA / IATA
aligned). End-to-end encryption for personal use is legal in the USA and
Taiwan.
