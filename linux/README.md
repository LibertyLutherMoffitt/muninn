# Muninn — Linux Client

Encrypted peer-to-peer chat over Bluetooth Classic (RFCOMM). No internet required.

## Prerequisites

- Two Linux devices with Bluetooth
- Nix package manager

## Run

```
nix run .#muninn-linux
```

No flags needed — both devices scan for each other and connect automatically. Pairing is
handled on first connect; no OS-level pairing setup required.

## Usage

**Default (recommended):** run with no flags on both devices. Each scans for Muninn
services and listens for incoming connections simultaneously. Lower MAC initiates,
higher MAC waits — they find each other automatically.

```
muninn
```

**Listen only:** wait for an incoming connection, don't scan.

```
muninn --listen
```

**Connect only:** scan and connect (no address), or connect to a specific address.

```
muninn --connect
muninn --connect XX:XX:XX:XX:XX:XX
```

After the E2EE handshake, you get a `>` prompt. Type and press enter to send.
Incoming messages appear as `< message`.

```
E2EE established.
> hello from device A
< hello from device B
> nice, it works
```

Ctrl+C to quit.

## Dev Shell

```
nix develop
python -m muninn.cli --help
```

## What works today

- RFCOMM connect/listen via BlueZ D-Bus (`org.bluez.ProfileManager1`)
- Automatic pairing via `org.bluez.Device1.Pair()` — no OS UI needed
- X25519 key exchange + XSalsa20-Poly1305 encryption (NaCl Box)
- 1:1 encrypted messaging with CLI interface
- ACK per message — sender tracks delivery
- Auto-reconnect on BT disconnect (2s for lower MAC, 4s for higher MAC)
- Resend unacked messages after reconnect
- Receiver deduplicates by message ID

## What doesn't exist yet

- Groups / multi-device
- Relay through intermediary devices
- GUI (GTK/Qt)
- Message persistence (in-memory only — messages lost on process exit)
- Android client

## Troubleshooting

**`No Muninn service found`** — the other device isn't running Muninn yet, or BlueZ
hasn't cached its SDP record. Start Muninn on both devices; they will find each other.

**`No Bluetooth adapter found`** — check `bluetoothctl show` to verify adapter exists
and is powered on.
