# Muninn — Linux Client

Encrypted peer-to-peer chat over Bluetooth Classic (RFCOMM). No internet required.

## Prerequisites

- Two Linux devices with Bluetooth
- Nix package manager

## Pairing (one-time)

Devices must be paired at the OS level before Muninn can connect.

On both machines:

```
bluetoothctl
> power on
> scan on
        (wait for the other device to appear)
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> quit
```

You do **not** need to "connect" in bluetoothctl — Muninn handles the RFCOMM connection itself.

## Build

```
nix build .#muninn-linux
```

Binary is at `./result/bin/muninn`.

## Dev Shell

```
nix develop
```

Then run directly:

```
python -m muninn.cli --help
```

## Usage

**Device A (listener):**

```
muninn --listen
```

**Device B (connector):**

```
muninn --connect XX:XX:XX:XX:XX:XX
```

After connection and E2EE handshake, you get a `>` prompt. Type a message and press enter. Incoming messages appear as `< message`.

```
E2EE established.
> hello from device A
< hello from device B
> nice, it works
```

Ctrl+C to quit.

## What works today

- RFCOMM connect/listen via SDP service discovery
- X25519 key exchange + XSalsa20-Poly1305 encryption (NaCl Box)
- 1:1 encrypted messaging with CLI interface
- ACK per message — sender tracks delivery
- Auto-reconnect on BT disconnect (retries every 2s)
- Resend unacked messages after reconnect
- Receiver deduplicates by message ID

## What doesn't exist yet

- Groups / multi-device
- Relay through intermediary devices
- GUI (GTK/Qt)
- Message persistence (in-memory only — messages lost on process exit)
- Android client

## Troubleshooting

**`No Muninn service found`** — the listener isn't running yet, or SDP registration failed.
Run `--listen` first, then `--connect`.

**`FileNotFoundError: /sys/class/bluetooth/hci0/address`** — no Bluetooth adapter found.
Check `bluetoothctl show` to verify adapter exists and is powered on.

**`libbluetooth-dev` errors during install** — use `nix develop` or `nix build` to avoid
system dependency issues.
