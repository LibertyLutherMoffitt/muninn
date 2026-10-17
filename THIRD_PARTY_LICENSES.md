# Third-party licenses

Muninn itself is MIT (see [`LICENSE`](LICENSE)). It dynamically links and/or
imports the libraries below at runtime. Their licenses are listed here for
attribution and compliance. None of their source is bundled inside the Muninn
source tree — they are pulled in as separate artifacts by `pip` (PyPI) or
`nix` (nixpkgs store paths).

| Library     | License            | Role in Muninn                                          |
|-------------|--------------------|---------------------------------------------------------|
| Qt 6        | LGPL-3.0-or-later  | UI toolkit (QtCore / QtGui / QtQml / QtQuick / QtQuick.Controls / QtQuick.Layouts) — dynamically linked via PySide6 |
| PySide6     | LGPL-3.0-only      | Python bindings to Qt 6                                 |
| PyNaCl      | Apache-2.0         | Python wrapper around libsodium (X25519 + XSalsa20-Poly1305) |
| libsodium   | ISC                | Bundled native library inside PyNaCl wheels             |
| dbus-python | MIT (also offered under Academic Free License 2.1) | D-Bus client (Linux BlueZ backend) |
| PyGObject   | LGPL-2.1-or-later  | GLib mainloop binding (Linux BlueZ backend)             |
| winrt-runtime, winrt-Windows.* | MIT | WinRT projection (Windows BT backend)            |
| JetBrains Mono | SIL Open Font License 1.1 | Default UI font (used if installed; otherwise the OS substitutes a monospace face) |
| Pillow      | MIT-CMU (HPND)     | Photo shrinking before sending (`media.py`)              |

### Bubble Tea client (`tea/`, statically linked into `muninn-tea`)

| Library | License | Role |
|---|---|---|
| Go standard library | BSD-3-Clause | runtime |
| charmbracelet/bubbletea, bubbles, lipgloss, harmonica, x/ansi, x/term, x/cellbuf, colorprofile | MIT | terminal UI framework, widgets, styling, spring animation |
| muesli/termenv, muesli/ansi, muesli/cancelreader | MIT | terminal colours and input |
| rivo/uniseg, mattn/go-runewidth, clipperhouse/* | MIT | text width / grapheme handling |
| lucasb-eyer/go-colorful | MIT | colour maths |
| mattn/go-isatty, mattn/go-localereader, erikgeiser/coninput, xo/terminfo, aymanbagabas/go-osc52, atotto/clipboard, sahilm/fuzzy | MIT | terminal / console helpers (some only on Windows) |

### Android app

| Library | License | Role |
|---|---|---|
| AndroidX / Jetpack Compose, Material 3, Kotlin standard library | Apache-2.0 | UI and runtime |
| TweetNaCl (ported to Kotlin in `android/core/`) | public domain | X25519, XSalsa20-Poly1305, Ed25519 group arithmetic |

### Build-time tools (not redistributed in the source tree)

These appear in `pyproject.toml` but are not part of the runtime artifact, so
they do not impose attribution obligations on end users of Muninn. Listed for
completeness.

| Tool        | License            | Role                                                    |
|-------------|--------------------|---------------------------------------------------------|
| setuptools  | MIT                | PEP 517 build backend (`[build-system]` requires)       |
| PyInstaller | GPL-2.0-or-later with bootloader exception | Optional `[windows-build]` extra used to produce a standalone `muninn.exe`. The bootloader exception explicitly permits bundling non-GPL applications; PyInstaller itself is not pulled into the resulting binary |

## LGPL compliance

Qt 6 and PySide6 are LGPL. Muninn complies as follows:

1. **Dynamic linking only.** The Muninn source tarball ships zero bytes of
   Qt or PySide6. They are loaded from independent files at runtime (the
   nixpkgs store paths or the PySide6 wheel installed by pip).
2. **Replaceability.** A user can swap in their own PySide6 / Qt build by
   either installing a different `pyside6` Python package into the same
   environment, or overriding the flake's `nixpkgs` input.
3. **Source availability.** Upstream sources are at:
   - Qt 6: <https://download.qt.io/official_releases/qt/>
   - PySide6: <https://pypi.org/project/PySide6/> and
     <https://code.qt.io/cgit/pyside/pyside-setup.git/>
4. **No modification.** Muninn does not patch Qt or PySide6.

If you redistribute Muninn alongside bundled Qt/PySide6 binaries (PyInstaller,
AppImage, `nix bundle`, etc.), keep the Qt libraries as separate files (do
**not** statically link), include the full LGPL-3.0 text, and reproduce a
notice with the bullets above. The Muninn source distribution is exempt from
those requirements because it does not ship those binaries.

## JetBrains Mono

The default UI font is JetBrains Mono (SIL Open Font License 1.1). It is
**not** bundled with Muninn — Qt loads it from the system if installed, and
otherwise substitutes a monospace face via `QFont::StyleHint::Monospace`.
Distributors who choose to bundle the font must include `OFL.txt` from
<https://github.com/JetBrains/JetBrainsMono>.

## libsodium / PyNaCl

PyNaCl wheels statically include libsodium (ISC). The combined license terms
are reproduced inside the installed PyNaCl package; nothing in this repo
modifies or redistributes libsodium directly.
