"""PyInstaller entry point for the desktop GUI (see .github/workflows/desktop.yml)."""

import os

if os.environ.get("MUNINN_SELFTEST"):
    from selftest import run

    run(gui=True)

from muninn.gui.main import main

main()
