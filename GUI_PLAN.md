# Muninn GUI — Plan

> Desktop GUI client for Muninn — modern, animated, Vim-native, dark.
> Ships as a second entry point alongside the existing CLI, sharing the full
> protocol / BT / crypto / storage core unchanged.

---

## Goals

- Modern, GPU-composited desktop chat UI.
- Fast, deliberate animations (chat switches, new-message arrivals, modal transitions).
- Extensive Vim keybindings on by default — both for text editing and for moving around.
- Dark mode, default and only for MVP.
- Reuses `ConnectionManager` / `GroupStore` / `Storage` / `bt.*` unchanged.
- Linux first; Windows follows once `bt/winrt.py` is hardware-validated.

## Non-Goals (MVP)

- File or image send.
- Emoji picker.
- Full-text search.
- Light theme / user theme switcher.
- Automated tests (`pytest-qt` deferred).
- i18n / translations.
- System tray integration.
- Draft persistence across restarts.

## Tech stack

- **PySide6** — LGPL-3.0 Python bindings over Qt 6.
- **Qt Quick / QML** — GPU-composited scene graph; animations via `Behavior`, `NumberAnimation`, `Transition`, `ParallelAnimation`.
- **Qt Quick Controls 2** — baseline controls; custom QML components for chat-specific UI.
- Runs Wayland-native, falls back to X11 (`QT_QPA_PLATFORM=wayland;xcb`).

## Architecture

### Layering

```
┌──────────────────────────────────────────────┐
│  QML views — PeerList, ChatView, Composer    │
├──────────────────────────────────────────────┤
│  ChatBridge (QObject) — signals + slots      │
├──────────────────────────────────────────────┤
│  ConnectionManager / GroupStore / Storage    │   (shared with CLI, unchanged)
├──────────────────────────────────────────────┤
│  bt.* backend (BlueZ / WinRT)                │
└──────────────────────────────────────────────┘
```

### Thread model

BT recv threads, BlueZ GLib mainloop, and the WinRT asyncio loop all exist today and stay untouched. A new `ChatBridge(QObject)` subscribes to the existing `ConnectionManager` callbacks:

- `on_message(group_id, sender, text, msg_id)`
- `on_peer_change(addr, connected)`
- `on_ack(msg_id, from_mac)`
- `on_read(msg_id, from_mac)`
- `on_profile(addr, name)`
- `on_group_setup(group)`

Each callback enters the Qt GUI thread via a QObject signal connected with `Qt.QueuedConnection`. QML reads only from ChatBridge-owned models / properties. This is the only thread-safety boundary the GUI introduces.

### Storage: single-writer, many-reader

First Muninn instance (CLI or GUI) to launch acquires an exclusive advisory lock on `~/.local/state/muninn/.writer.lock` via `fcntl.flock` (Linux) / `msvcrt.locking` (Windows). That instance becomes the **writer**: owns the BT stack, sends and receives, mutates `Storage`.

A second instance starts in **reader mode**:

- Cannot send, scan, pair, or set nick. UI greys or hides those affordances with a tooltip: *"Another Muninn instance holds the writer lock."*
- Polls `PRAGMA data_version` every 500 ms. On change, re-reads new rows from `messages` (both incoming and outgoing) since last-known `rowid` and fans them into the UI models. Same for `pubkeys`, `names`, `groups`.
- Receives display-only updates about ACKs / reads / profile changes the writer has persisted.
- Does **not** try to share in-memory state (seen-dedup set, unacked map, `indirect_via`) — those belong to the writer and are meaningless cross-process.

If the writer exits, readers do **not** auto-promote in MVP. The user restarts the one they want as the writer.

Rationale: correctness > slickness. Shared `ConnectionManager` across processes is a rabbit hole we don't need. Read-only observation is cheap and enough.

## UI layout

### Main window

```
┌──────────────┬───────────────────────────────────────────────┐
│  Peer list   │  Conversation header                          │
│  ──────────  │  ─────────────────────────────────────────    │
│  > alice •   │                                                │
│    bob       │    [scrollback — message bubbles]              │
│    craig ◦   │                                                │
│    #group-3  │                                                │
│              │  ─────────────────────────────────────────    │
│              │  Composer (Vim modal)                          │
├──────────────┴───────────────────────────────────────────────┤
│  nick: josh    peers: 3 connected    mode: WRITER             │
└───────────────────────────────────────────────────────────────┘
```

- **Peer list (left rail)** — scrollable. Ordered **last-activity first** (most recent message in or out, direct or relayed, floats to top). Row shows initial-avatar, display name, last-msg preview, unread badge, connection dot (direct / indirect / offline).
- **Chat pane (right)** — header (peer name, fingerprint, status), scrollback, composer.
- **Status bar** — local nick, connected-peer count, writer/reader indicator.
- **Command palette** — `<space>f` fuzzy picker for peers / commands (also `:palette` / `:find` / `:f`).

### Animations (GPU-composited, budget ≤150 ms each)

- **Chat switch** — cross-fade + 80 ms slide of scrollback + composer.
- **New incoming message** — bubble fade + 10 px slide-up, 120 ms.
- **Peer connect / disconnect** — color pulse on peer row (200 ms ease-out).
- **Modal open** — blur background + scale-in (100 ms).
- **Scroll-to-bottom on new msg** — `NumberAnimation` on contentY.
- **Vim mode change** — composer border-color `Behavior` transition (60 ms).

### Theme — dark only

| Role             | Hex        |
|------------------|------------|
| bg               | `#0f1115`  |
| surface          | `#151820`  |
| surface-raised   | `#1b1f2a`  |
| text-primary     | `#e5e7eb`  |
| text-muted       | `#9ca3af`  |
| accent           | `#7c3aed`  |
| incoming-bubble  | `#1f2330`  |
| outgoing-bubble  | `#3b2a6a`  |
| success          | `#10b981`  |
| error            | `#ef4444`  |

- **Font:** default UI font is **JetBrains Mono** (set globally via
  `QGuiApplication::setFont`). Falls back to whatever the OS picks for the
  Monospace style hint when JetBrains Mono is not installed. Not bundled —
  keeps the artifact lean.

## Vim keybindings

Two scopes:

1. **Global nav** — focus on peer list / chat pane / palette.
2. **Composer** — modal text editor.

### Modes (composer)

- Normal (default on open)
- Insert
- Visual / Visual-line
- Operator-pending
- Command-line (`:`)

### Motions

- `h` `j` `k` `l`
- `w` `W` `b` `B` `e` `E` `ge` `gE`
- `0` `^` `$`
- `gg` `G` `{n}G`
- `f{c}` `F{c}` `t{c}` `T{c}` `;` `,`
- `%` — matching bracket
- `Ctrl-D` `Ctrl-U` `Ctrl-F` `Ctrl-B`
- Count prefix on any motion: `{n}{motion}`

### Text objects

`i{obj}` (inside) and `a{obj}` (around):

- `w` / `W` — word / WORD
- `"` `'` `` ` `` — quoted strings
- `(` `)` `[` `]` `{` `}` `<` `>` — bracketed regions (and their closing variants)
- `p` — paragraph
- `t` — tag (skip for MVP if no HTML context)

### Operators

- `d` `c` `y` + motion / text object
- `dd` `cc` `yy`
- `D` `C` `Y`
- `x` `X` `s` `S`
- `r{c}` `R`
- `~` toggle case
- `>` `<` indent (spaces only)
- `.` — repeat last change

### Registers, yank / put

- `"{reg}` prefix — `"a`..`"z`, `"0`, `""` (unnamed), `"+` (system clipboard), `"*` (primary selection).
- `p` `P`
- Register store is a Python dict; `"+` / `"*` bridge through `QGuiApplication.clipboard()`.
- Default register uses the system clipboard, so `"+` does not have be done all the time

### Search (composer)

- `/pattern` `?pattern` `n` `N` — case-insensitive unless pattern has an uppercase char (smartcase).

### Insert-mode escapes

- `Esc`
- `Ctrl-[`

### Enter semantics

- **Normal / Visual mode:** `Enter` sends the composer buffer (same as `:send`, `<C-Enter>`).
- **Insert mode:** `Enter` inserts a newline. `<C-Enter>` sends from any mode.

Rationale: keeps composition Vim-idiomatic (never accidentally send mid-paragraph), while making the one-liner case a single keystroke (`i`-type-`Esc`-`Enter`).

### Undo / redo

- `u` — undo (`QTextDocument` undo stack)
- `Ctrl-R` — redo

### Command-line (`:`)

The vim cmdline and the `<space>f` palette (raw `:` mode) share one dispatcher
in `ChatBridge.runCommand`. Tab-completes commands, peer names, and group
names. Action commands toast; data commands pop an `InfoMenu`.

| Command                         | Action                                                         |
|---------------------------------|----------------------------------------------------------------|
| `:send` / `Enter` (Normal/Visual) / `<C-Enter>` | Send composer buffer                           |
| `:dm <peer>`                    | Switch to a DM                                                 |
| `:group <name>`                 | Switch to a named group                                        |
| `:new <name> <peer1> [peer2…]`  | Create a group                                                 |
| `:nick <name>`                  | Set your display name                                          |
| `:nick <peer> <name>`           | Local override for a peer                                      |
| `:list` / `:peers` / `:known`   | Pop info menu — click-through opens the conversation           |
| `:history [N]`                  | Reload last N messages of the active conversation              |
| `:scan`                         | Open the scan dialog                                           |
| `:clear`                        | Clear visible messages (does not delete from DB)               |
| `:next` / `:prev`               | Cycle conversations (also `Ctrl-N` / `Ctrl-P`)                 |
| `:palette` / `:find` / `:f`     | Open the command palette (also `<space>f`)                     |
| `:help`                         | Pop info menu listing every command                            |
| `:w`                            | No-op (no save concept)                                        |
| `:wq` / `:x`                    | Send pending buffer, then quit                                 |
| `:q` / `:qa` / `ZZ`             | Exit app                                                       |

### Global nav — outside composer

| Keys                         | Action                                                |
|------------------------------|-------------------------------------------------------|
| `Ctrl-N` / `Ctrl-P`          | Cycle to next / previous conversation                 |
| `<space>f`                   | Open command palette (peers + commands)               |
| `<space>s`                   | Open scan dialog                                      |
| `Ctrl-H` / `Ctrl-L`          | Focus peer list / chat pane                           |
| `Esc`                        | Close any open overlay; otherwise leave Insert        |

Inside the palette / info menu / scan dialog: `Ctrl-N` / `Ctrl-P`, `j` / `k`,
or `Up` / `Down` to move selection; `Tab` to complete; `Enter` to activate;
`Esc` to close. Global shortcuts (`Ctrl-N/P`, `Ctrl-H/L`, `Esc`) are gated
while an overlay is open, so the overlay's local handlers always win.

### Implementation

`VimEditor` — a `QQuickTextEdit` subclass exposed as a QML component, owning a small state machine:

- `mode: enum { Normal, Insert, Visual, VisualLine, OpPending, CmdLine }`
- `pending: str` — chord buffer (`d`, `gi`, `3d`, ...)
- `count: int | None`
- `register: str | None`
- `last_change: tuple | None` — for `.`

Dispatch: lookup keyed on `(mode, chord_prefix)` → action callable. Motions operate on `QTextCursor` primitives; text objects resolved via regex + position pairs. `.` replays `(count, register, op, motion/text_obj)`.

Exit criterion for the Vim layer: a hand-written manual-test script exercises ≥95% of the documented bindings end-to-end.

## Packaging

### `pyproject.toml`

```toml
[project.optional-dependencies]
gui = ["PySide6"]
windows-build = ["pyinstaller"]

[project.scripts]
muninn      = "muninn.cli:main"
muninn-gui  = "muninn.gui.main:main"
```

CLI-only installs stay lean. `pip install muninn[gui]` (or the nix gui app) pulls PySide6.

### `flake.nix`

Two derivations sharing one `python/` source tree:

- `muninn-linux` (alias `cli`) — CLI-only; unchanged deps.
- `muninn-gui` (alias `gui`) — same pyproject plus `pyside6`, `qt6.qtbase`, `qt6.qtdeclarative`, `qt6.qtsvg`.

Apps:

```
nix run .#muninn-linux -- --help   # or .#cli
nix run .#muninn-gui               # or .#gui
```

Dev shell includes: `pyside6`, `qt6.qtbase`, `qt6.qtdeclarative`, `qt6.qtwayland`. Enough to `import PySide6` and launch a QML app under `nix develop`.

## License compliance

### Library license matrix

| Library      | License      | Role                                   |
|--------------|--------------|----------------------------------------|
| Qt 6         | LGPL-3.0     | Dynamically linked via PySide6         |
| PySide6      | LGPL-3.0     | Python bindings                        |
| PyNaCl       | Apache-2.0   | crypto                                 |
| libsodium    | ISC          | bundled by PyNaCl                      |
| dbus-python  | MIT / AFL    | Linux BT D-Bus                         |
| PyGObject    | LGPL-2.1     | Linux GLib mainloop                    |
| winrt-*      | MIT          | Windows BT                             |

### Our license

- Stays **MIT**, copyright **Joshua Hammer**. MIT code + dynamic link to LGPL libraries is compliant and standard.
- Mentioning other licenses inside our `LICENSE` file does **not** satisfy LGPL — LGPL requires the full license text and a source offer. That's what `THIRD_PARTY_LICENSES.md` + `licenses/LGPL-3.0.txt` + the GUI About dialog deliver.

### How each distribution mode handles LGPL

**Default modes (nix + pip) — compliant out of the box, no extra work:**

- `nix run .#gui` — nixpkgs supplies `pyside6` and `qt6` as independent store paths with their own license files preserved. Dynamic linking only. User can swap in a different PySide6 by overriding the flake input.
- `pip install muninn[gui]` — pip fetches PySide6 wheel from PyPI, co-located but a separate artifact with its own license metadata. Dynamic linking only.

In both modes our source tarball ships **zero bytes of Qt or PySide6**; we only declare the dependency. That is the cleanest LGPL posture possible.

**Bundled modes (PyInstaller, AppImage, `nix bundle`) — require care:**

When we co-distribute Qt binaries alongside our code, we must:

1. Keep Qt libs as **separate files**, not statically linked. PyInstaller `--onedir` (default) satisfies this. Avoid `--onefile` if practical; if used, document that extraction is dynamic at runtime.
2. Ship the **full LGPL-3.0 license text** inside the bundle.
3. Include a **notice** stating Qt and PySide6 are used under LGPL-3.0.
4. Offer the **Qt / PySide6 source** — a URL in the notice is sufficient (`https://download.qt.io/official_releases/qt/`, `https://pypi.org/project/PySide6/`).
5. Document how a user can **replace** the bundled Qt libs (e.g. "drop replacement PyQt6 .so files into `_internal/PySide6/Qt/lib/`").
6. Never distribute a **modified** Qt or PySide6 unless we publish the corresponding source.

### Deliverables (MVP)

- ✅ `THIRD_PARTY_LICENSES.md` at repo root — the library matrix above and
  the LGPL compliance notes for Qt 6 + PySide6.
- ✅ `LICENSE` — MIT, copyright **Joshua Hammer** (2026).
- ✅ `README.md` — has a `Licenses` section pointing to `THIRD_PARTY_LICENSES.md`.
- 🔲 `licenses/LGPL-3.0.txt` — full LGPL-3.0 text. Skipped while we ship
  source-only; required if we ever cut a bundle (PyInstaller / AppImage /
  `nix bundle`).
- 🔲 About dialog in the GUI — version + LGPL notice + source links.

### Deferred until we ship a bundle

- `licenses/BUNDLE-NOTICE.md` — prepended to PyInstaller / AppImage artifacts
  with the 1–6 bullets above. Only needed once we start releasing non-source
  artifacts.

## App icon

Feather 🪶 from the README. Render once as SVG in accent plum `#7c3aed` (monochrome), export rasters:

```
assets/muninn.svg
assets/muninn-256.png
assets/muninn-128.png
assets/muninn-64.png
```

Windows `.ico` bundle later. Loaded via `QIcon("assets/muninn.svg")` — Qt has built-in SVG.

## File layout

```
python/src/muninn/gui/
├── __init__.py
├── main.py              # entrypoint: QGuiApplication + default font + QML engine + theme dict
├── bridge.py            # ChatBridge: signals/slots, runCommand dispatcher, completeCommand
├── vim.py               # VimEditor state machine + key dispatcher
├── writer_lock.py       # advisory lock + reader polling
├── models.py            # PeerListModel, MessageListModel (QAbstractListModel)
└── qml/
    ├── Main.qml         # window + status bar + global shortcuts
    ├── PeerList.qml
    ├── ChatView.qml     # top-to-bottom message list, auto-scroll, bubble delegate
    ├── Composer.qml     # vim-edited composer + cmdline strip
    ├── ScanDialog.qml
    ├── CommandPalette.qml  # <space>f — fuzzy + raw `:` mode
    ├── InfoMenu.qml     # popup for :list / :peers / :known / :help
    └── Transitions.qml  # reusable animation primitives (singleton)
```

Note: Theme colors are defined as a Python dict in `main.py` and exposed to
QML as context properties, not as a separate `Theme.qml` singleton.

## Implementation milestones

1. ✅ **Scaffold** — skeleton `main.py` brings up an empty dark window; Theme singleton wired. `gui` extra installs. Flake dev shell runs `muninn-gui` end-to-end.
2. ✅ **ChatBridge** — expose peers + currently-selected-chat message list to QML. Hook ConnectionManager callbacks via QueuedConnection. Read-only of existing Storage.
3. ✅ **Basic chat view** — PeerList, ChatView, bubble delegate. Composer.
4. ✅ **VimEditor** — modal composer. Motions, operators, text objects, registers (linewise/charwise), counts, `.` repeat, cmdline with tab completion.
5. ✅ **Global nav keymaps** — `Ctrl-N/P` cycle, `<space>f` palette, `<space>s` scan, `Ctrl-H/L` focus.
6. ✅ **Animations** — palette/scan/info fade+scale, mode-border transitions, peer-row pulse, scroll-to-bottom on new message.
7. ✅ **Scan dialog** — discover / scan / pair flow; keyboard navigable.
8. ✅ **Writer lock + reader poll** — second-instance detection, disabled UI, `PRAGMA data_version` polling.
9. ✅ **Command palette + InfoMenu** — fuzzy peers/commands, raw `:` mode, tab completion via shared `bridge.runCommand` / `bridge.completeCommand`. Data commands open `InfoMenu` with click-through to a conversation.
10. ✅ **Packaging + license** — flake outputs, `THIRD_PARTY_LICENSES.md`, README license section. About dialog + icon still TODO.
11. 🔲 **Icon + About dialog.**
12. 🔲 **Polish + manual QA pass.**

## Resolved decisions

- **Read-only UI affordance** — send controls are **greyed + tooltip'd**, not hidden. More discoverable than silent disappearance.
- **Window geometry persistence** — **deferred.** Window opens at a sensible default size each run; revisit post-MVP.
- **Render backend fallback** — **ship and ignore.** If the user has no GPU accel, animations degrade gracefully; no special "reduce motion" path.
- **About-dialog placement** — both `:about` command and a menu entry.

## TODO
- cursor trail faster, more fps, smoother 
    - trail to go to where the cursor is in the palette not the center of the palette
- have the message preview under each chat work always (currently only has one if a message has been sent/received since opening the app)
- desktop notifications
- have a button to manually open the palette in addition to the keybinds
- when switching to a next chat, start at the bottom instead of at the top (it's annoying to have to scroll down to the most recent message every time I go back to a dm)
