"""PyInstaller entry point for the terminal client."""

import os

if os.environ.get("MUNINN_SELFTEST"):
    from selftest import run

    run(gui=False)

from muninn.cli import main

main()
