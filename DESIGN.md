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
            │ Qt/QML GUI · CLI · bot │ Android Compose │
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
real-time datagram, typing, reinstall notice). Group messages are encrypted
per member. Files are pulled by the receiver in windows of chunks, from the
sender or any member that has the file, resumable and hash-verified.

### Games

A game is one JavaScript file with a small pure API (setup / turn / move /
status / render / input, optional real-time tick). Both GUIs host the same
file (QML Canvas, Android WebView canvas). The code travels inside the invite,
so all players run identical rules. State = setup folded over the ordered move
log; illegal or out-of-turn moves are ignored identically everywhere.

### Discovery (good Bluetooth citizen)

Known peers are redialled with a short, capped back-off. Inquiry is rationed
by a user-chosen scan mode (eager / balanced / quiet) because it disrupts
audio and links. Devices whose service record says Muninn are dialled; an
*unknown phone or computer* may get one pairing-free probe per look within a
budget, then is left alone for a long time. Headsets, watches and peripherals
are never contacted. BlueZ only authorizes Muninn's own service for paired
devices.

## Clients

| | Desktop (Python) | Android (Kotlin) |
|---|---|---|
| UI | Qt 6/QML GUI with Vim-modal composer, command palette; readline CLI | Jetpack Compose |
| Transport | BlueZ (D-Bus profile), WinRT (untested), simulator | insecure RFCOMM, simulator |
| Notifications | notify-send / tray | per-conversation, inline reply |
| Background | — | foreground service, boot start |

A Go TUI and a WearOS thin client were once planned; they are not on the
roadmap now (see TODO.md).

## Transport details

- RFCOMM service UUID `320bcf9c-94fe-46f4-b9bf-83535cafcd55`.
- BlueZ: profile registered via `ProfileManager1` (no authentication
  required); `ConnectProfile` is called asynchronously and the socket arrives
  through `NewConnection`. Known peers are paired via `Device1.Pair()` (link
  keys persist with `store_hint=1`); unconfirmed devices are never paired.
  Discoverability is re-asserted every 2 minutes.
- Android: `listenUsingInsecureRfcommWithServiceRecord` /
  `createInsecureRfcommSocketToServiceRecord` — no bonding, no prompts.
  Android can't be discovered unless the user allows it (≤ 5 min), but it
  finds and dials laptops and known peers on its own.
- Windows: discovery only sees paired devices; discoverability can't be set
  programmatically. Pair once in Settings.

## Security

NaCl box (X25519 + XSalsa20-Poly1305), static keys, no forward secrecy, TOFU
key pinning, routing metadata in the clear for relays. MITM at first contact
and storage limits are out of scope.

## Legal

Bluetooth in airplane mode is permitted on commercial flights (FAA / IATA
aligned). End-to-end encryption for personal use is legal in the USA and
Taiwan.
