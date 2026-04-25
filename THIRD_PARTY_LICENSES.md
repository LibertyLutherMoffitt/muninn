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
| dbus-python | MIT / Academic Free License 2.1 | D-Bus client (Linux BlueZ backend)         |
| PyGObject   | LGPL-2.1-or-later  | GLib mainloop binding (Linux BlueZ backend)             |
| winrt-runtime, winrt-Windows.* | MIT | WinRT projection (Windows BT backend)            |
| JetBrains Mono | SIL Open Font License 1.1 | Default UI font (used if installed; otherwise the OS substitutes a monospace face) |

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
