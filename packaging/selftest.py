"""`MUNINN_SELFTEST=1 <binary>`: prove a packaged build has everything it needs
(native crypto, core, bundled games/assets) without touching Bluetooth."""

import sys


def run(gui: bool) -> None:
    from nacl.public import Box, PrivateKey

    from muninn import gamehost, mesh, node, protocol  # noqa: F401
    from muninn.emoji import QUICK_REACTIONS

    a, b = PrivateKey.generate(), PrivateKey.generate()
    assert Box(b, a.public_key).decrypt(Box(a, b.public_key).encrypt(b"hi")) == b"hi"
    games = sorted(p.stem for p in gamehost.BUNDLED.glob("*.js"))
    assert "chess" in games and QUICK_REACTIONS, games
    if gui:
        from PySide6.QtQml import QQmlApplicationEngine  # noqa: F401

        from muninn.gui import bridge, main  # noqa: F401

        qml = main._QML_DIR / "Main.qml"
        assert qml.exists(), qml
    else:
        from muninn import tui  # noqa: F401
    print(f"selftest ok: {len(games)} games, crypto ok")
    sys.exit(0)
