<div align="center">

# 🪶 Muninn

*Memory. Encrypted. Carried on dark wings.*

</div>

> *Muninn* (Old Norse: *memory*, *mind*) — one of Óðinn's two ravens. Each dawn he flies out over all the worlds; each dusk he returns and whispers what he has seen. Óðinn fears Muninn's loss above all else — that memory might not come back.

---

## What is this?

**Encrypted peer-to-peer chat over Bluetooth Classic.** No internet. No cell signal. No Wi-Fi. No accounts.

Designed for use where other communication fails: airplane cabins, remote areas, anywhere you're physically near someone but digitally cut off. Two devices pair once, and from then on they can exchange end-to-end encrypted messages whenever they're within Bluetooth range (~10–30m open-air; realistically 3–8 rows through an aircraft cabin).

Legal for personal use in the USA and Taiwan, and permitted on commercial flights under FAA guidelines (Airplane Mode + Bluetooth on).

---

## Status

```
Step 1 — RFCOMM socket          ✅  done
Step 2 — Wire framing + E2EE    ✅  done
Step 3 — 1:1 messaging + CLI    ✅  done  ← current
Step 4 — Groups (up to 6)          planned
Step 5 — Relay + delivery          planned
Step 6 — Qt6/QML GUI               planned
```

**The Linux CLI client is functional today.** Two people, encrypted chat, no infrastructure. Steps 4–6 are on the roadmap but not yet built.

---

## How it works

```
Device A                              Device B
────────────────────────────────────────────────
         ◄──── RFCOMM connect ────►

send Handshake(pubkey_A)  ──────────►
                          ◄──────────  send Handshake(pubkey_B)

         ECDH → shared secret (X25519)

send Message(encrypted)   ──────────►  decrypt → display
                          ◄──────────  send ACK(msg_id)
```

- **Transport:** Bluetooth Classic RFCOMM — stable, well-supported, no BLE fragmentation issues
- **Crypto:** NaCl `Box` — X25519 key exchange + XSalsa20-Poly1305 AEAD (via libsodium)
- **Reliability:** messages are ACK'd; unACK'd messages resent automatically on reconnect
- **Deduplication:** receiver tracks `msg_id` (UUID v4) — retransmits are silently dropped

---

## Clients

| Platform | Language | BT Stack | UI | Status |
|----------|----------|----------|----|--------|
| Linux | Python 3 | BlueZ via D-Bus | CLI (Qt6/QML planned) | **Working** |
| Android | Kotlin | Android Bluetooth API | Jetpack Compose | Planned |
| WearOS | Kotlin | via Android phone relay | Compose for Wear | Future |

---

## Linux client — quick start

Requires NixOS or a system with Nix + flakes.

```bash
# Enter dev shell
nix develop

# Run
nix run .#muninn-linux -- --help
```

First connection pairs devices automatically via `org.bluez.Device1.Pair()` — no OS pairing dialog needed. Subsequent connects are fast.

---

## Security model

Messages are end-to-end encrypted. Only message text is encrypted; metadata (sender MAC, timestamp, message ID) is plaintext to enable future relay routing.

Static keypairs are generated once per process and reused across reconnects. This means every handshake produces the same shared secret — simple and fast, but **no forward secrecy**. For an in-flight chat tool this tradeoff is acceptable.

No PKI. Vulnerable to MITM on first connect if an attacker can intercept the pubkey exchange. For manual verification, both parties can compare a short key fingerprint (display UI planned).

---

## Roadmap

- **Groups** — up to 6 members, pairwise E2EE to each recipient
- **Relay** — messages hop through intermediaries (`A ── B ── C`) so all group members don't need direct BT connections
- **Qt6/QML GUI** — Wayland-native desktop UI (PySide6), GPU-composited, animation-friendly
- **Android client** — Kotlin + Jetpack Compose
- **WearOS thin client** — tethered to Android phone via Wearable Data Layer

---

## Wire format

```
[ 1 byte: type ][ 2 bytes: length ][ N bytes: payload ]
```

| Type | Value | Notes |
|------|-------|-------|
| Handshake | `0x01` | 32-byte X25519 pubkey, plaintext |
| Message | `0x02` | metadata plaintext, text Box-encrypted |
| ACK | `0x03` | msg_id + sender MAC, plaintext |

Full spec in [`PROTOCOL.md`](PROTOCOL.md). Architecture and decisions in [`DESIGN.md`](DESIGN.md).

---

## Name

Muninn (Old Norse: *memory*, *mind*) is one of the two ravens sent out by Óðinn each day. Huginn (*thought*) and Muninn fly over all the worlds and return to whisper what they have seen. Óðinn worries more for Muninn — that memory might not return.

Your messages, carried on short wings, between two ravens in the dark.

---

*Weekend project. Personal use. Don't over-engineer it.* 🪶
