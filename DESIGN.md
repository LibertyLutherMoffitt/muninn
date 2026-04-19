# Muninn — Design Document

## Overview

Native clients enabling encrypted peer-to-peer text communication over Bluetooth
Classic (RFCOMM), requiring no internet, cellular, or Wi-Fi infrastructure. Designed for
use in offline environments such as airplane cabins. Legal for personal use in USA, Taiwan,
and permitted on commercial flights per FAA guidelines.

---

## Clients

Three independent clients are planned. Each reimplements the wire protocol in its own
language — `PROTOCOL.md` is the only cross-client contract. Duplication is cheap at this
size and simpler than threading a generated IDL (protobuf/flatbuffers) through three
ecosystems.

### 1. Desktop Client — Python (CLI + Qt6 GUI in one package)

**Language:** Python 3
**Targets:** Linux (working), Windows (future)
**BT Stack:**
- **Linux** — BlueZ via D-Bus (`dbus-python`, `pygobject3`); RFCOMM profile registered
  via `org.bluez.ProfileManager1`. Lives in `python/src/muninn/bt/bluez.py`.
- **Windows** — WinRT Bluetooth APIs (`winsdk` / `pywinrt`). Will live in
  `python/src/muninn/bt/winrt.py`. Not yet written.

The `muninn.bt` package dispatches on `sys.platform` at import time. Everything above
(`crypto.py`, `protocol.py`, `peers.py`, `groups.py`) is platform-agnostic.

**Crypto:** PyNaCl (libsodium binding)

**Frontends — both in the same Python package, sharing the full core:**
- **CLI** (`cli.py`) — readline-based terminal UI. Working today on Linux.
- **Qt6/QML GUI** (`gui.py`, planned) — PySide6 + QML. GPU-composited, animation-friendly,
  flexible theming. Chosen over GTK4 for better keybinding support, superior animation
  story, and scalability to image sending and embedded widgets (future). Wayland-native
  on Linux (tested on Hyprland), native on Windows.

CLI and GUI import the same `ConnectionManager`, the same protocol, the same BT backend.
The only difference is the layer that talks to the user. Splitting them into separate
projects would duplicate 100% of the non-UI code, so they ship as one package with two
entry points (`muninn-cli`, `muninn-gui`).

### 2. Terminal Client — Go + Bubble Tea

**Language:** Go
**Targets:** Linux, Windows
**BT Stack:** Same two-backend pattern as the Python client, selected via Go build tags:
- **Linux** — BlueZ via D-Bus (`tui/internal/bt/bluez.go`, `//go:build linux`)
- **Windows** — WinRT (`tui/internal/bt/winrt.go`, `//go:build windows`)

Both files implement a common `bt.Transport` interface; the compiler picks the right one
at build time.

**Crypto:** `golang.org/x/crypto/nacl/box` — same X25519 + XSalsa20-Poly1305 primitive
as the Python client, wire-compatible.

**UI:** `charmbracelet/bubbletea` (Elm-architecture TUI) + `lipgloss` for styling.

**Why a Go TUI in addition to the Python CLI:** single static binary, cross-compiles
to Windows without a Python runtime, smoother rendering than readline for multi-pane
conversation views.

### 3. Android Client — Kotlin

**Language:** Kotlin
**Target:** Android (single platform — no separate BT backend needed)
**BT Stack:** Android Bluetooth API — `BluetoothAdapter`, `BluetoothServerSocket` /
`BluetoothSocket` via RFCOMM (stable since API 5)
**Crypto:** lazysodium-android (libsodium binding)
**UI:** Jetpack Compose

### 4. WearOS Client — Kotlin (future)

**Architecture:** Thin client via Wearable Data Layer API — watch relays messages through
paired Android phone, which handles all BT communication.
**UI:** Compose for Wear
**Note:** Requires paired Android phone running the Android client. Standalone direct BT
from watch is possible on some hardware (Galaxy Watch, Pixel Watch) but deprioritized due
to poor BT stack reliability and aggressive power management on WearOS.

---

## Monorepo Structure

```
muninn/
├── PROTOCOL.md              ← cross-client wire contract (source of truth)
├── DESIGN.md                ← architecture + decisions (this file)
├── README.md
├── flake.nix                ← Nix dev shell, builds all desktop clients
├── python/                  ← Desktop client: CLI + Qt6 GUI (Linux + Windows)
│   ├── pyproject.toml
│   └── src/muninn/
│       ├── bt/
│       │   ├── __init__.py  ← dispatches on sys.platform
│       │   ├── bluez.py     ← Linux backend (working)
│       │   └── winrt.py     ← Windows backend (future)
│       ├── crypto.py        ┐
│       ├── protocol.py      │  platform-agnostic core,
│       ├── peers.py         │  shared by CLI and GUI
│       ├── groups.py        │
│       ├── storage.py       ┘
│       ├── cli.py           ← readline frontend (working)
│       └── gui.py           ← Qt6/QML frontend (future)
├── tui/                     ← Go Bubble Tea TUI (Linux + Windows)
│   ├── go.mod
│   ├── cmd/muninn-tui/
│   │   └── main.go
│   └── internal/
│       ├── bt/
│       │   ├── bt.go        ← Transport interface
│       │   ├── bluez.go     ← //go:build linux
│       │   └── winrt.go     ← //go:build windows
│       ├── crypto/
│       ├── protocol/
│       ├── peers/
│       └── ui/              ← bubbletea model/update/view
├── android/                 ← Kotlin + Jetpack Compose (future)
│   └── …                    ← standard Gradle/Android Studio layout
└── wearos/                  ← Compose-for-Wear, tethered to android/ (future)
```

### Structural rules

- **`PROTOCOL.md` is the only shared artifact.** Every client reimplements framing,
  encoding, and state in its own language. There is no generated IDL.
- **BT backend is the only per-OS split inside a cross-platform client.** Two of our
  desktop clients (Python, Go) target both Linux and Windows — each isolates the platform
  difference to a single file inside a `bt/` subpackage (`bluez.*` vs `winrt.*`). Adding
  Windows support means writing one file, not branching the codebase. Android is
  single-OS and has no such split.
- **One Python client, two frontends.** CLI and Qt6 GUI share `crypto.py`,
  `protocol.py`, `peers.py`, `groups.py`, and `bt/`. They differ only in the user-facing
  layer.
- **Android lives in its own top-level directory.** Its toolchain (Gradle, Android SDK)
  and language (Kotlin) don't overlap with the desktop clients; sharing a build system
  would be more pain than win.

---

## Transport

**Protocol:** Bluetooth Classic — RFCOMM  
**Service UUID:** `320bcf9c-94fe-46f4-b9bf-83535cafcd55` (hardcoded on all clients)  
**Why not BLE:** RFCOMM is more stable for this use case. Linux GATT server (BLE peripheral
role) via BlueZ is painful and poorly documented. RFCOMM is well-supported on both
platforms with no manufacturer fragmentation issues.  
**Range:** ~10–30m open-air (Class 2). In-cabin through seats and bodies: realistically
~3–8 rows. Sufficient for nearby travel companions; not expected to span a full aircraft.  
**Pairing:** Handled automatically by the Linux client via `org.bluez.Device1.Pair()` with a registered `NoInputNoOutput` agent. No OS-level pairing UI required. Pairing happens on first connect and persists (link key stored with `store_hint=1`).

### Connection Initiation

Both devices register the RFCOMM service via SDP and listen simultaneously. Either user
can initiate a connection to any paired device — the initiating device does an SDP lookup
and connects; the other accepts. Works regardless of platform combination
(Linux↔Linux, Android↔Android, Linux↔Android).

**Simultaneous connect:** to avoid both sides calling `ConnectProfile` at the same time
(which deadlocks bluetoothd), the device with the higher MAC address waits up to 10 seconds
for the lower-MAC device to initiate. If an incoming connection arrives during that wait, it
is used directly. If both sides do end up connecting simultaneously anyway, two sockets form:
the socket initiated by the higher-MAC device is closed. Both sides apply this rule
deterministically, leaving exactly one connection.

### Reconnection

BT connections drop often (range, interference, phone sleep). On socket error or EOF, both
sides return to the listen+connect state. A new connection triggers a fresh E2EE handshake.
Static keys mean every handshake produces the same shared secret, so this is cheap.

**Resend unacked messages:** each side tracks which sent messages have received an ACK. On
reconnect, any message without an ACK is resent. The receiver deduplicates by `msg_id` —
if a message with a known `msg_id` arrives again, it is silently dropped and an ACK is sent
back. This gives reliable delivery across disconnects with no extra protocol complexity.

---

## Encryption — E2EE

Both clients use **libsodium** via platform-specific bindings (listed in Clients section).

### Key Exchange & Encryption Flow

```
Device A                              Device B
────────────────────────────────────────────────
generate X25519 keypair               generate X25519 keypair
send pubkey over RFCOMM  ──────────►  receive pubkey
receive pubkey           ◄──────────  send pubkey
ECDH → shared secret                  ECDH → shared secret
encrypt: XSalsa20-Poly1305            decrypt: XSalsa20-Poly1305
```

**Primitive:** NaCl `Box` = X25519 key exchange + XSalsa20-Poly1305 AEAD  
**MITM note:** No PKI or certificate authority. Vulnerable to MITM if an attacker can
intercept the initial pubkey exchange. For casual use this is acceptable. For verification,
both parties can verbally compare a short key fingerprint.

### Nonces

24 random bytes generated per message, prepended to ciphertext. Recipient reads first
24 bytes as nonce, remainder as ciphertext. Collision probability negligible at chat
scale (~2^96 nonce space).

---

## Conversations (Groups & DMs)

Every conversation is a **group**. A 1:1 DM is a group of 2 using a zeroed `group_id`.
There is no separate DM protocol — this eliminates a code path and keeps the
implementation uniform.

**Group size:** up to 6 members.

### Group Formation

One device acts as group creator. At formation time, all members' public keys are
distributed to all other members (routed through intermediaries if needed). Every member
must end up with the full member list + pubkeys before the group is usable.

### Encryption in Groups

Pairwise E2EE is used. When a device sends a message to a group, it encrypts a separate
copy for each recipient using that recipient's pubkey (X25519 ECDH shared secret). Each
encrypted payload is addressed to its specific final destination. Relay nodes forward
payloads addressed to others without decrypting.

This requires every sender to hold pubkeys for every group member, even those not directly
reachable — obtained during group formation.

### Relay

Devices are not required to have direct BT connections to all group members. A device that
is connected to an intermediary can send messages through that intermediary. Relay is
**persistent** — messages destined for an unreachable member are held and forwarded when
a path becomes available.

```
Example topology:
  A ──── B ──── C
  (A and C have no direct connection)
```

A sends two payloads to B: one encrypted for B, one encrypted for C. B decrypts its copy,
forwards C's payload to C unchanged.

### Delivery Guarantees

Every message has a `msg_id` (random UUID). Each sender tracks delivery state per message
per recipient. Delivery is confirmed only by **end-to-end receipt** — an ACK from the
final recipient routed back to the original sender.

```
A → B → C
         C → B: ACK(msg_id, from=C)
         B → A: ACK(msg_id, from=C)   ← A marks C:delivered only here
```

ACKs propagate back through the relay path. B's receipt of the message is not sufficient
for A to mark it delivered to C.

**Delivery gossip:** each device maintains and shares delivery state. Any device that holds
a message and learns that a recipient hasn't received it will attempt delivery when that
recipient becomes reachable — not just the original sender. This means relay devices share
responsibility for delivery completion.

---

## Wire Format

All frames share a universal header:
```
[ 1 byte: type ][ 2 bytes: payload_length ][ N bytes: payload ]
```

**Byte order:** big-endian (network order) for all multi-byte integers.  
**Text encoding:** UTF-8 for all plaintext message content.  
Maximum payload size: 65,535 bytes (uint16).

The receiver loop is type-agnostic: read 1 byte (type), read 2 bytes (length), read N bytes
(payload), then dispatch by type.

### Type Bytes

| Type        | Value  | Encrypted |
|-------------|--------|-----------|
| Handshake   | `0x01` | No (pre-key-exchange) |
| Message     | `0x02` | Partially (metadata plaintext, message text encrypted) |
| ACK         | `0x03` | No (contains only msg_id + sender MAC) |
| Group Setup | `0x04` | No (group_id + member MACs + pubkeys + name) |
| Read        | `0x05` | No (msg_id + reader MAC; same shape as ACK) |
| Profile     | `0x06` | No (self-chosen display name, UTF-8) |
| Peer Annc   | `0x07` | No (list of known peer MACs + pubkeys + names) |

Message frame payload:
```
[ 16 bytes: group_id ]
[ 16 bytes: msg_id (UUID v4) ]
[ 6 bytes: sender_id (BT MAC of originating device) ]
[ 6 bytes: final_dest (BT MAC of intended recipient) ]
[ 4 bytes: timestamp (uint32 unix seconds) ]
[ 24 bytes: nonce ][ N bytes: Box ciphertext ]
```

ACK frame payload:
```
[ 16 bytes: msg_id ]
[ 6 bytes: from (BT MAC of acknowledging device) ]
```

Read frame payload (identical shape to ACK; signals user actually viewed the message):
```
[ 16 bytes: msg_id ]
[ 6 bytes: from (BT MAC of reading device) ]
```

Group Setup frame payload:
```
[ 16 bytes: group_id (UUID v4) ]
[  1 byte:  member_count ]
For each member:
    [  6 bytes: member_mac ]
    [ 32 bytes: member_pubkey ]
[  2 bytes: name_length ]
[  N bytes: name (UTF-8) ]
```

Handshake frame payload:
```
[ 32 bytes: X25519 public key ]
```

Profile frame payload:
```
[ N bytes: display_name (UTF-8; empty = no self-chosen name) ]
```

Sent immediately after handshake, and re-sent to all connected peers when the local user
changes their name via `/nick <name>`. Profile frames are not forwarded directly — but
when a relay node (B) receives a Profile from A, it re-announces A's updated name to all
other connected peers via a Peer Annc frame. This causes name updates to propagate to
indirect peers. Local users may override a peer's self-chosen name with `/nick <peer>
<name>`; overrides win on display.

Handshake frames are sent in plaintext (before shared secret exists). After both sides
exchange pubkeys and derive the shared secret, message text is Box-encrypted. ACK and
message metadata (group_id, msg_id, sender, dest, timestamp) remain plaintext. The
universal header (type + length) is always plaintext.

---

## Legal Status

| Jurisdiction | E2EE personal use | BT in-flight |
|---|---|---|
| USA | ✅ Legal | ✅ FAA-approved |
| Taiwan | ✅ Legal | ✅ FAA/IATA aligned |
| Airlines (general) | ✅ No restriction | ✅ Airplane mode + BT on |

---

## Implementation Order

Complexity is layered. Each step is testable before the next begins. E2EE is established
early because it defines the wire format and handshake — retrofitting it later would break
everything built on top.

**Step 1 — RFCOMM socket** ✅
- Two devices connect over RFCOMM
- Raw bytes flow both directions
- No crypto, no framing, no protocol

**Step 2 — Wire framing + E2EE handshake** ✅
- Universal frame header: `[type][length][payload]`
- Handshake frame: exchange X25519 pubkeys on connect
- All post-handshake frames: Box encrypted with random nonce prepended
- Verify decrypt works cross-platform (Linux ↔ Android)

**Step 3 — 1:1 messaging** ✅
- Send/receive encrypted text messages
- ACK per message — sender tracks unacked messages
- Auto-reconnect on disconnect — fresh handshake, resend unacked messages
- Receiver deduplicates by msg_id
- CLI functional on Linux
- This is a complete, shippable thing — two people, encrypted chat, no internet

**Step 4 — Groups** ✅
- group_id, sender_id, final_dest become meaningful (ignored in 1:1)
- All conversations become groups (1:1 = group of 2)
- Group formation + member pubkey distribution (`GROUP_SETUP` frame, `0x04`)
- Simultaneous multi-peer connections via `ConnectionManager`

**Step 5 — Group relay + delivery** ✅
- Relay for non-directly-connected members (per-peer `final_dest` routing)
- End-to-end ACK routing through relay path (flood-back, dedup'd by `(msg_id, from)`)
- Relay queue holds frames for currently-unreachable peers, flushed on reconnect
- Read receipts (`READ` frame, `0x05`) — flood-back when user views a conversation
- `seen_relayed` keyed on `(msg_id, dest_bytes)` — prevents relay storms without blocking
  retransmits to different destinations
- Nick propagation to indirect peers via Peer Annc re-announcement on Profile receipt

**Step 5b — SQLite persistence** ✅
- `storage.py`: WAL-mode SQLite, `threading.Lock` serialization, schema versioned via
  `PRAGMA user_version`
- `GroupStore` write-through cache: all mutations update in-memory dicts and the DB atomically
- Identity (keypair) persisted — same X25519 pubkey across restarts
- Messages, groups, pubkeys, display names, unacked state, and seen-dedup table all
  survive process exit
- Unacked messages rebuilt on startup from DB (re-encrypted with fresh nonces, same msg_id)

---

## UI Polish (any point after Step 4)
- Linux Qt6/QML GUI (PySide6)
- Android Compose UI polish
- Key fingerprint display for manual MITM verification

## Future
- Multi-device sync (same person, two devices — merge message history via msg_id deduplication)
- WearOS thin client via Wearable Data Layer API (requires Android client as hub)

---

## Known Pain Points

- **Pairing UX** — first connection is slower as `Device1.Pair()` runs; subsequent connects are fast
- **Android background** — RFCOMM socket killed by aggressive OEM power management;
  foreground service required (Step 1)
- **WearOS standalone BT** — deprioritized; tethered model is reliable, standalone is not
- **Group key distribution** — all members need all pubkeys before messaging; distribution
  through intermediaries adds complexity at group formation time
