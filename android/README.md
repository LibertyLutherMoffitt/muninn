# Muninn — Android Client

Encrypted peer-to-peer chat over Bluetooth Classic (RFCOMM). No internet required.

Targets Android 12+ (API 31). Shares the wire protocol with the Python desktop client (see [`PROTOCOL.md`](../PROTOCOL.md)), so an Android phone and a Linux desktop running Muninn talk to each other directly.

**Status:** milestones 1–3 complete plus a working 1:1 chat UI — RFCOMM listener, X25519 handshake, NaCl Box encrypt/decrypt, in-app device discovery + pairing, and a Compose conversation view (send + receive). The ConnectionManager port, groups, relay, and on-disk storage are next.

## Quick start

**Prerequisites:**

- Android 12+ device with Bluetooth.
- Linux box with `nix` (flakes enabled) and `adb`.
  - NixOS users: Add yourself to `adbusers`. See the troubleshooting section.
- A peer to talk to: `nix run .#muninn-linux` on another machine.

Build and install the debug APK:

```bash
# From the repo root
nix develop
cd android
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The APK is unsigned debug, ~23 MB. `./gradlew :app:installDebug` builds and installs in one step once a device is connected.

Watch what the app does over Bluetooth:

```bash
adb logcat -s MuninnService PeerSession Pairing
```

## Pairing

Tap **Pair** in the top bar. The app runs its own Bluetooth Classic discovery and
shows a bottom sheet of nearby devices, flagging the ones that advertise the
Muninn RFCOMM service UUID with a **Muninn** badge. Tap a device to bond; Android
pops a one-tap Just Works confirm, and from then on the foreground service
connects to it automatically whenever both devices are in range.

**Why not CompanionDeviceManager:** CDM's `BluetoothDeviceFilter.addServiceUuid`
matches the 128-bit Muninn UUID against the classic-inquiry EIR, which BlueZ
usually omits — so the system sheet came up empty. Service UUIDs live in SDP, not
the inquiry response, so we discover devices ourselves and call
`fetchUuidsWithSdp()` on each (SDP *does* return 128-bit UUIDs) to build a
correct, Muninn-only list. See `BtDiscovery.kt`.

If the list is empty:

- Verify the Linux peer is running and discoverable (Muninn calls
  `bluetoothctl discoverable on` automatically — check `bluetoothctl show`).
- Flip **Show all nearby devices** to list non-Muninn devices too (in case SDP
  resolution lagged), or pair via Android **Settings → Bluetooth**. The app picks
  up bonded devices regardless of how they got bonded.

The Just Works confirm cannot be suppressed without the platform-signed
permission `BLUETOOTH_PRIVILEGED`. One tap on first bond is the floor for
non-system apps.

## Usage

The app:

1. On launch, requests `BLUETOOTH_CONNECT` / `BLUETOOTH_SCAN` / `BLUETOOTH_ADVERTISE` and `POST_NOTIFICATIONS`.
2. Starts a foreground service (`MuninnService`) that owns an RFCOMM listening socket on the Muninn service UUID.
3. Polls bonded devices every 15 s; if one advertises the Muninn UUID and isn't already connected, it dials.
4. On each accepted/dialed socket, exchanges X25519 handshake keys + wire ids, derives a NaCl Box, then loops on frames.
5. Shows the conversation in a Compose chat view: type and **Send**, incoming MSG frames decrypt into bubbles, each ACKed back to the sender. (Plaintext still mirrors to Logcat under tag `PeerSession`.)

Once a peer is connected, the top bar status flips to **Connected** and the
composer enables. Send a message from the phone, or from Linux:

```
nix run .#muninn-linux
# Wait for the handshake to complete with the Android peer.
/dm <android-wire-id>          # the wire id shown in the phone's status line when no peer is connected
hello from linux
```

The Linux side shows `⧗` then `✓` (delivered) once Android ACKs. The message
appears as a bubble in the phone's chat view.

## Identity

The app generates a static X25519 keypair on first launch and persists it in `SharedPreferences`. The same keypair is reused across restarts, matching the desktop client's `identity` table.

It also generates a random 6-byte **wire id** (locally-administered MAC pattern) and uses it in place of the device's hardware Bluetooth MAC. **API 31+ hides the real MAC from non-system apps** — `BluetoothAdapter.getAddress()` returns `02:00:00:00:00:00` — so we use a stable random ID instead. Linux peers store this as the Android device's "MAC". The protocol works fine; the field just no longer corresponds to a routable Bluetooth address.

## Dev shell

```bash
nix develop                       # provisions Android SDK 34, JDK 17, Kotlin, gradle, adb
cd android
./gradlew :app:assembleDebug      # build
./gradlew :app:installDebug       # build + adb install in one step
```

The nix dev shell sets `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `JAVA_HOME`, and the `aapt2FromMavenOverride` Gradle property so AGP uses the SDK's nix-patched aapt2 instead of the Maven-fetched binary (which won't run on NixOS without patching).

If you're using Android Studio:

- Open the `android/` directory.
- Studio uses its own bundled JDK and downloads its own SDK, ignoring the nix shell. Both will work; the nix path is the canonical one for CI and reproducible builds.

## What works today

- Foreground service owns an RFCOMM listening socket on the Muninn service UUID
  (`320bcf9c-94fe-46f4-b9bf-83535cafcd55`).
- Outgoing connect attempts every 15 s to bonded devices that advertise the
  Muninn UUID, deduplicated against currently-active sessions.
- X25519 key exchange + XSalsa20-Poly1305 (NaCl Box) via `lazysodium-android` —
  wire-compatible with the Python client's PyNaCl.
- Static keypair + 6-byte wire id, persisted across restarts.
- In-app pairing — own classic discovery + per-device `fetchUuidsWithSdp()` to
  flag Muninn peers, then `createBond()` (see `BtDiscovery.kt`).
- Compose 1:1 chat UI — message bubbles, composer, live connection status,
  Material You theming.
- Bidirectional messaging — encrypt + send + ACK, decrypt incoming, fan-out via
  `ChatRepository`.
- Runtime Bluetooth permission grant + foreground-service notification.

## What doesn't exist yet

- ConnectionManager — multi-peer state, unacked tracking, dedup, reconnect
  resend (port of `peers.py`).
- Multi-conversation UI — today it is a single flat message log; no per-peer
  threads or peer list.
- Groups, group setup, relay, peer announcements.
- Profile (display name) frames in or out.
- Read receipts.
- Storage on disk — currently `SharedPreferences` only; migration to Room
  mirroring `storage.py` is on the roadmap.
- Mitigations for OEM background-killers (Xiaomi, Oppo, OnePlus).

## Troubleshooting

**"Bluetooth disabled" notification** — toggle Bluetooth on. The service today
does not re-arm its listen socket when Bluetooth comes back; restart the app
after enabling Bluetooth.

**Pair sheet shows no devices** — the Linux peer must be running and
discoverable, and its SDP record must include the Muninn service UUID. Hit the
refresh icon to rescan; flip **Show all nearby devices** if SDP resolution
lagged (device shows without the Muninn badge). As a last resort pair via
**Settings → Bluetooth**; the app picks up bonded devices regardless of how
they got bonded.

**Phone connects but messages don't decrypt** — confirm the Linux peer is
sending to the Android phone's **wire id** shown in the app's status line (when
no peer is connected), not a real Bluetooth MAC. The Linux side learns the wire
id on first handshake and stores it in its `pubkeys` table.

**`adb devices` shows "no permissions"** — on NixOS, set
`programs.adb.enable = true;` in your config and add yourself to `adbusers`,
then re-login. The flake-installed `adb` binary alone is not enough — udev
rules and group ownership are system-level.

**Build warning: `Unable to strip libsodium.so / libjnidispatch.so`** —
cosmetic. The flake does not include the NDK (~1.5 GB), so AGP cannot strip
the prebuilt native libraries shipped by lazysodium and JNA. The debug APK is
~1 MB heavier than it would be otherwise.

**Build warning: `aapt2FromMavenOverride is experimental`** — required on
NixOS. The Maven-distributed aapt2 binary won't run on Nix; the override
forces AGP to use the SDK's nix-patched aapt2. This is the standard
nix-android workaround.
