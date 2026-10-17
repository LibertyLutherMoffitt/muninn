# Muninn — desktop package (core, GUI, terminal, bot)

The Python package in `src/muninn/` is the desktop core and every desktop
front end:

| Entry point | What |
|---|---|
| `muninn-gui` | Qt 6 / QML GUI (Vim-modal composer, palette, light/dark themes, tray) |
| `muninn` | terminal client: full-screen curses TUI on a TTY, `--plain` line mode, `--api` JSON on stdio for the Bubble Tea client (`../tea/`) |
| `muninn-bot` | headless node with a JSON control port (tests, labs, a friend's laptop as a relay) |
| `muninn-simhub` | the simulated Bluetooth radio (see `../TESTING.md`) |

Bluetooth backends: BlueZ over D-Bus on Linux (`bt/bluez.py`), WinRT on
Windows (`bt/winrt.py`, not yet tested on hardware). Muninn **never pairs**:
links are open RFCOMM connections to the Muninn service; old bonds can be
removed with `/unpair` / `:unpair`.

## Run

```bash
nix run .#muninn-gui                 # GUI
nix run .#muninn-linux               # terminal client
nix run .#muninn-tea                 # Bubble Tea terminal client (Go front end, same core)
```

Standalone builds (no Python needed) are attached to every GitHub release —
see the top-level README. From source without Nix: `pip install ./python`
(`pip install "./python[gui]"` for the GUI), then `muninn` / `muninn-gui`.

Environment:

| Variable | Meaning |
|---|---|
| `MUNINN_NAME` | display name for this session |
| `MUNINN_DATA` | data directory (default `~/.local/share/muninn`, `%APPDATA%\muninn` on Windows) |
| `MUNINN_SIM=host:port` | use the simulator instead of Bluetooth |

Only one front end runs per data directory at a time (`instance.py`); a
second one exits with a clear message. Logs go to `<data dir>/muninn.log`.

## Using it

Type `/help` in the terminal or `:help` in the GUI (F1 in the Bubble Tea
client). The most useful commands:

```
/dm <name>              open a chat          /new <room> <people…>  new room
/find                   look for new people   /peers                 who's in range
/nick <name>            your name             /nick <who> <name>     local nickname
/file <path>            send a file/photo     /photos original       send photos unshrunk
/link                   link another device   /devices               your devices
/export [file] [pw=…]   back up               /import <file>         restore
/ping <name>            round-trip time       /doctor                check before the flight
/forget, /block         remove people         /privacy               receipts / typing
```

The GUI has the same commands with `:` and adds a palette (Space f / Ctrl+K),
drag & drop, image viewer, games, zoom and a tray icon. Its Vim keys are
described in `src/muninn/gui/GUI_PLAN.md`.

## Layout

```
src/muninn/
  protocol.py content.py   wire frames / encrypted bodies (../PROTOCOL.md)
  mesh.py                  routing, outbox, relays, gossip, groups, files, games, linked devices
  node.py                  identity + storage + mesh + transport, dialer, discovery, presence
  storage.py               SQLite (WAL), migrations
  devices.py xeddsa.py     linked-device sets and their signatures
  backup.py media.py       backups / history sync, photo shrinking
  gamehost.py games/       game sessions + JavaScript game plugins (games/README.md)
  transport.py bt/         Bluetooth backends; sim/ the simulator
  cli.py tui.py api.py     terminal clients and the stdio API
  gui/                     Qt bridge, models, Vim engine, QML
tests/                     pytest suites (../TESTING.md)
```

## Troubleshooting

- **Nobody shows up** — on a new phone, *Settings → Make me visible* once (or
  run `/find` on the laptop while the phone shows its Nearby screen). Known
  people reconnect by themselves afterwards.
- **`No Bluetooth adapter`** — Linux: `bluetoothctl show` (powered on?).
  Windows: Settings → Bluetooth.
- **A phone offers the laptop as speakers** — an old bond from Muninn ≤ 0.2:
  `/unpair` here and *Forget* the laptop on the phone.
- **"already running"** — another Muninn front end owns this data directory.
