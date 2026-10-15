# Muninn — Android Client

Encrypted chat over Bluetooth Classic (RFCOMM). No internet required. Android
12+ (API 31). Talks to the desktop client directly and relays for everyone.

## Layout

- `core/` — pure-Kotlin (JVM) port of the Muninn core: protocol v2, NaCl
  (TweetNaCl port, byte-identical to PyNaCl, no native libraries), mesh
  routing/relaying/retries, node dialer, game sessions, simulator transport.
  Also builds a headless JVM node (`./gradlew :core:installDist`) that the
  Python test-suite runs alongside Python nodes.
- `app/` — the app: `Runtime.kt` (process-wide node), `MuninnService.kt`
  (foreground service owning the radio), `BtTransport.kt` (insecure RFCOMM,
  background inquiry per scan mode), `AndroidStorage.kt` (SQLite, same schema
  as desktop), Compose screens, `assets/gamehost.html` (WebView game host).
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
find them on their own. Phone ↔ phone: one of them taps *Settings → Make me
visible* (Android allows 5 minutes) — or open *Nearby* and tap the other
device. After the first meeting both sides reconnect by themselves, and a
phone you've never met can still reach you through anyone in between.

Muninn never pairs with or prompts other people's devices. In *Settings →
Finding new people* choose how hard it looks (Eager / Balanced / Quiet).

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
