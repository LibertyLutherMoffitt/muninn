# Muninn — Design Document

## Overview

A pair of native clients enabling encrypted peer-to-peer text communication over Bluetooth
Classic (RFCOMM), requiring no internet, cellular, or Wi-Fi infrastructure. Designed for
use in offline environments such as airplane cabins. Legal for personal use in USA, Taiwan,
and permitted on commercial flights per FAA guidelines.

---

## Clients

### 1. Linux Client — Python

**Language:** Python 3  
**BT Stack:** BlueZ via PyBluez2 (RFCOMM)  
**Crypto:** PyNaCl (libsodium binding)  
**UI Options:**
- GTK (PyGObject) or Qt (PyQt6 / PySide6) desktop GUI
- **Terminal/CLI mode** — fully operable from the command line without a GUI, for headless
  use, SSH sessions, or developer preference. CLI and GUI share the same core protocol layer.

### 2. Android Client — Kotlin

**Language:** Kotlin  
**BT Stack:** Android Bluetooth API — `BluetoothAdapter`, `BluetoothServerSocket` /
`BluetoothSocket` via RFCOMM (stable since API 5)  
**Crypto:** lazysodium-android (libsodium binding)  
**UI:** Jetpack Compose

### 3. WearOS Client — Kotlin (future / phase 3)

**Architecture:** Thin client via Wearable Data Layer API — watch relays messages through
paired Android phone, which handles all BT communication.  
**UI:** Compose for Wear  
**Note:** Requires paired Android phone running the Android client. Standalone direct BT
from watch is possible on some hardware (Galaxy Watch, Pixel Watch) but deprioritized due
to poor BT stack reliability and aggressive power management on WearOS.

---

## Transport

**Protocol:** Bluetooth Classic — RFCOMM  
**Service UUID:** `320bcf9c-94fe-46f4-b9bf-83535cafcd55` (hardcoded on all clients)  
**Why not BLE:** RFCOMM is more stable for this use case. Linux GATT server (BLE peripheral
role) via BlueZ is painful and poorly documented. RFCOMM is well-supported on both
platforms with no manufacturer fragmentation issues.  
**Range:** ~10–30m (more than sufficient for in-cabin use)  
**Pairing:** Standard OS-level Bluetooth pairing required once before first use.

### Connection Initiation

Both devices register the RFCOMM service via SDP and listen simultaneously. Either user
can initiate a connection to any paired device — the initiating device does an SDP lookup
and connects; the other accepts. Works regardless of platform combination
(Linux↔Linux, Android↔Android, Linux↔Android).

**Simultaneous connect:** if both users initiate at the same time, two sockets form. The
socket where the local BT MAC address is higher is dropped. Both sides apply this rule
deterministically, leaving exactly one connection.

---

## Encryption — E2EE

Both clients use **libsodium** via language-specific bindings. Primitives are identical on
both sides, guaranteeing full interoperability.

| Side    | Library          | Underlying lib |
|---------|------------------|----------------|
| Linux   | PyNaCl           | libsodium      |
| Android | lazysodium-android | libsodium    |

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

XSalsa20 is a stream cipher. Encrypting two messages with the same key and same nonce
leaks plaintext (two-time pad attack — attacker XORs ciphertexts to get XOR of plaintexts).
Key complexity does not prevent this; it is a property of stream ciphers independent of
key strength.

**Scheme:** generate 24 cryptographically random bytes per message, prepend to ciphertext.
Recipient reads first 24 bytes as nonce, remainder as ciphertext.

```
[ 24 bytes: random nonce ][ N bytes: Box ciphertext ]
```

Collision probability is negligible at chat scale (~2^96 nonce space).

---

## Conversations (Groups & DMs)

Every conversation is a **group**. A 1:1 DM is a group of 2. There is no separate DM
protocol — this eliminates a code path and keeps the implementation uniform.

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

## Architecture

### Shared (per client, not shared code)

Each client independently implements the same protocol:

- **Protocol layer** — framing, pubkey exchange handshake, message serialization
- **Crypto layer** — libsodium Box encrypt/decrypt
- **BT layer** — RFCOMM connect/listen/read/write
- **UI layer** — platform-specific (GTK/Qt/CLI on Linux, Compose on Android)

No shared codebase. Protocol spec is the contract between clients.

### Wire Format

All frames:
```
[ 1 byte: type ][ payload... ]
```

Type byte values — TBD (to be defined when handshake sequence is finalized).

Message/ACK frames carry:
```
[ 1 byte: type ]
[ 16 bytes: group_id ]
[ 16 bytes: msg_id ]
[ 6 bytes: final_dest (BT MAC of intended recipient) ]
[ 4 bytes: payload_length ]
[ 24 bytes: nonce ][ N bytes: Box ciphertext ]
```

Handshake frames use a different type byte and their own payload structure (TBD).

---

## Connectivity Model

```
Android phone  ──── RFCOMM ────  Linux laptop
     │
     │  (Wearable Data Layer — phase 3)
     │
  WearOS watch
```

```
Group relay example:
  Device A ──── RFCOMM ──── Device B ──── RFCOMM ──── Device C
```

No server. No relay infrastructure. All traffic stays on local BT links between devices.

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

**Step 1 — RFCOMM socket**
- Two devices connect over RFCOMM
- Raw bytes flow both directions
- No crypto, no framing, no protocol

**Step 2 — Wire framing**
- Type byte + length prefix + payload
- Basic frame parsing on both clients
- Type byte values defined here

**Step 3 — E2EE handshake**
- X25519 pubkey exchange on connect
- All subsequent frames: Box encrypted with random nonce prepended
- Verify decrypt works cross-platform (Linux ↔ Android)

**Step 4 — 1:1 messaging**
- Send/receive encrypted text end-to-end
- CLI functional on Linux
- This is a complete, shippable thing — two people, encrypted chat, no internet

**Step 5 — Groups**
- Add group_id, msg_id, sender_id, final_dest to frame
- All conversations become groups (1:1 = group of 2)
- Group formation + member pubkey distribution

**Step 6 — Delivery ACK**
- ACK frame type
- Sender tracks delivery state per message per recipient
- End-to-end receipt (ACK from final recipient, not relay)

**Step 7 — Gossip / relay**
- Persistent relay for non-directly-connected members
- Delivery state sync between connected peers (monotonic: delivered > pending)
- Any device holding an undelivered message retries when recipient becomes reachable

---

## UI Polish (any point after Step 4)
- Linux GTK or Qt GUI
- Android Compose UI polish
- Key fingerprint display for manual MITM verification

## Future
- Multi-device sync (same person, two devices — merge message history via msg_id deduplication)
- WearOS thin client via Wearable Data Layer API (requires Android client as hub)

---

## Known Pain Points

- **Pairing UX** — users must pair devices in OS settings before first use (one-time)
- **Linux pybluez2 install** — may require `libbluetooth-dev` system package
- **Android background** — RFCOMM socket killed by aggressive OEM power management;
  foreground service required (phase 1)
- **WearOS standalone BT** — deprioritized; tethered model is reliable, standalone is not
- **Group key distribution** — all members need all pubkeys before messaging; distribution
  through intermediaries adds complexity at group formation time
