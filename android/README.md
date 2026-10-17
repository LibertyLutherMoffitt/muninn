# Muninn — Android Client

Encrypted chat over Bluetooth Classic (RFCOMM). No internet required. Android
12+ (API 31). Talks to the desktop client directly and relays for everyone.

## Layout

- `core/` — pure-Kotlin (JVM) port of the Muninn core: protocol v2, NaCl
  (TweetNaCl port, byte-identical to PyNaCl, no native libraries) and XEdDSA
  signatures, mesh routing/relaying/retries, linked devices and history sync,
  node dialer, game sessions, simulator transport.
  Also builds a headless JVM node (`./gradlew :core:installDist`) that the
  Python test-suite runs alongside Python nodes.
- `app/` — the app: `Runtime.kt` (process-wide node), `MuninnService.kt`
  (foreground service owning the radio), `BtTransport.kt` (insecure RFCOMM,
  inquiry only in find mode), `AndroidStorage.kt` (SQLite, same schema as
  desktop), `TapPair.kt` (NFC tap to meet), `Updater.kt` (self-update from
  GitHub releases), `Backup.kt`, `Media.kt` (photo shrinking), Compose
  screens, `assets/gamehost.html` (WebView game host).
  Games, stickers and emoji data come from `python/src/muninn/` at build time.

## Build & install

```bash
nix develop
cd android
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb logcat -s Muninn MuninnGame
```

## First contact

Nothing to pair. Laptops are always discoverable while Muninn runs and phones
find them on their own. Phone ↔ phone: **tap the backs together** (NFC, from
the Nearby screen), or one of them taps *Settings → Make me visible* (Android
allows a few minutes) while the other opens *Nearby*. After the first meeting
both sides reconnect by themselves, and a phone you've never met can still
reach you through anyone in between. In a room, *Room info → Add people →
Tap phones* adds whoever you tap.

Muninn never pairs with or prompts other people's devices, and only scans
(*find mode*) for a few minutes when you ask or while you know nobody. Known
people are redialled directly; *Settings → Reconnecting* picks Fast or
Battery saver.

## Settings worth knowing

- **Linked devices** — use this phone and your laptop as one person: *Link
  another device* shows a code (or *Join…* a device that shows one).
- **Backup** — save chats and files to a file or restore them (the desktop
  reads the same file); with a password it also carries your identity to a
  new phone.
- **Updates** — the app updates itself from GitHub releases (same signing key).
- **Photos** — shrunk before sending unless *Send photos in full quality* is on.
- **Check before the flight** — Bluetooth, permissions, background, friends'
  versions.

## Background

The foreground service keeps the radio running with the screen off and
restarts after reboot. If messages stop when the screen is off, use
*Settings → Allow background* (battery optimisation exemption).

## Testing without a phone

```bash
nix run .#android-emu &                      # headless emulator
python tools/droid.py install
python tools/droid.py sim kim                # joins the simulated cabin
```

See [`../TESTING.md`](../TESTING.md).
