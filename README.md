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
Step 3 — 1:1 messaging + CLI    ✅  done
Step 4 — Groups (up to 6)       ✅  done
Step 5 — Relay + delivery       ✅  done
Step 5b— SQLite persistence     ✅  done
Step 6 — Qt6/QML GUI            ✅  done
```

**Both the Linux CLI and Qt6/QML GUI are functional today.** Multi-peer
simultaneous connections, group messaging, relay through intermediaries,
delivery + read receipts, message history, Vim-modal composer with full
text-object / register / dot-repeat / count support, command palette
(`<space>f`), `:`-prefixed commands with tab completion, conversation cycling
(`Ctrl-N` / `Ctrl-P`), info popups for `:list` / `:peers` / `:known` / `:help`,
animated chat / palette / scan dialog transitions — all encrypted and
persisted across restarts.

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

| Client | Language | Targets | BT Stack | UI | Status |
|--------|----------|---------|----------|----|--------|
| Desktop — CLI | Python 3 | Linux, Windows | BlueZ / WinRT | readline | **Working (Linux); Windows backend written, not HW-tested** |
| Desktop — GUI | Python 3 | Linux, Windows | BlueZ / WinRT | Qt6/QML (PySide6) | **Working (Linux)** |
| Terminal | Go | Linux, Windows | BlueZ / WinRT | Bubble Tea | Planned |
| Android | Kotlin | Android | Android Bluetooth API | Jetpack Compose | Planned |
| WearOS | Kotlin | WearOS | via Android phone relay | Compose for Wear | Future |

The Desktop CLI and Desktop GUI ship as one Python package with two entry points — they
share the full protocol/BT/crypto core and differ only in the user-facing layer. See
[`DESIGN.md`](DESIGN.md#monorepo-structure) for the repo layout.

---

## Linux client — quick start

Requires NixOS or a system with Nix + flakes.

```bash
# Enter dev shell
nix develop

# Run CLI
nix run .#muninn-linux -- --help

# Run GUI
nix run .#muninn-gui
```

First connection pairs devices automatically via `org.bluez.Device1.Pair()` — no OS pairing dialog needed. Subsequent connects are fast.

---

## Windows — install & run GUI

Requires Python 3.11+ ([download](https://www.python.org/downloads/) — check "Add Python to PATH" during install).

Download and double-click [`install-gui.bat`](install-gui.bat). It installs Muninn + PySide6 and launches the GUI. After first install, just run `muninn-gui` from any terminal.

---

## Security model

Messages are end-to-end encrypted. Only message text is encrypted; metadata (sender MAC, timestamp, message ID) is plaintext to enable relay routing.

Static keypairs are generated once and persisted to SQLite — the same X25519 keypair is reused across restarts and reconnects, so the shared secret with each peer never changes. Simple and fast, but **no forward secrecy**. For an in-flight chat tool this tradeoff is acceptable.

No PKI. Vulnerable to MITM on first connect if an attacker can intercept the pubkey exchange. For manual verification, both parties can compare a short key fingerprint (display UI planned).

---

## Licenses

Muninn is **MIT** (see [`LICENSE`](LICENSE)). It dynamically links Qt 6 and
PySide6, both **LGPL-3.0**. The full third-party attribution and LGPL
compliance notes live in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

The GUI's default UI font is [JetBrains Mono](https://www.jetbrains.com/lp/mono/)
(SIL OFL-1.1). Muninn does not bundle it — Qt loads it from the system if
present and falls back to whatever the OS provides for the Monospace style
hint.

---

## Roadmap

- **Windows BT backend hardware validation** — `bt/winrt.py` exists but needs real-hardware testing
- **Go + Bubble Tea TUI** — single static binary, cross-compiles to Linux + Windows
- **Android client** — Kotlin + Jetpack Compose
- **WearOS thin client** — tethered to Android phone via Wearable Data Layer

---

## Wire format

```
[ 1 byte: type ][ 2 bytes: length ][ N bytes: payload ]
```

| Type        | Value  | Notes |
|-------------|--------|-------|
| Handshake   | `0x01` | 32-byte X25519 pubkey, plaintext |
| Message     | `0x02` | metadata plaintext, text Box-encrypted |
| ACK         | `0x03` | msg_id + sender MAC, plaintext |
| Group Setup | `0x04` | group_id + member MACs/pubkeys + name, plaintext |
| Read        | `0x05` | msg_id + reader MAC, plaintext (same shape as ACK) |
| Profile     | `0x06` | self-chosen UTF-8 display name, plaintext |
| Peer Annc   | `0x07` | list of known peer MACs + pubkeys, plaintext |

Full spec in [`PROTOCOL.md`](PROTOCOL.md). Architecture and decisions in [`DESIGN.md`](DESIGN.md).

---

## Name

Muninn (Old Norse: *memory*, *mind*) is one of the two ravens sent out by Óðinn each day. Huginn (*thought*) and Muninn fly over all the worlds and return to whisper what they have seen. Óðinn worries more for Muninn — that memory might not return.

Your messages, carried on short wings, between two ravens in the dark.

---

*Weekend project. Personal use. Don't over-engineer it.* 🪶
