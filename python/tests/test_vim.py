"""GUI vim editor: k from the composer's top line walks into the messages."""

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402

from muninn.gui.vim import VimEditor, _K  # noqa: E402


@pytest.fixture
def vim():
    _app = QCoreApplication.instance() or QCoreApplication([])
    v = VimEditor()
    v.navs, v.acts = [], []
    v.messageNav.connect(v.navs.append)
    v.messageAction.connect(v.acts.append)
    return v


def key(v, text="", code=0):
    v.handleKey(text, code, False, False, False)


def test_k_on_top_line_enters_messages(vim):
    key(vim, "k")
    assert vim.mode == "MESSAGES" and vim.navs == [0]
    key(vim, "k")
    key(vim, "j")
    assert vim.navs == [0, -1, 1]


def test_k_inside_multiline_draft_still_moves_the_cursor(vim):
    vim.setBuffer("line one\nline two")  # cursor ends on line two
    key(vim, "", _K["Escape"])
    key(vim, "k")
    assert vim.mode == "NORMAL" and vim.navs == []


def test_message_verbs(vim):
    key(vim, "k")
    key(vim, "3")
    key(vim, "d")
    assert "delete" not in vim.acts  # one d is not enough
    key(vim, "d")
    key(vim, "y")
    assert vim.acts == ["quick:3", "delete", "copy"]
    key(vim, "", _K["Return"])
    assert vim.acts[-1] == "reply" and vim.mode == "INSERT"


def test_escape_leaves_and_clears(vim):
    key(vim, "k")
    key(vim, "", _K["Escape"])
    assert vim.mode == "NORMAL" and vim.acts == ["clear"]
    key(vim, "k")
    vim.leaveMessages()
    assert vim.mode == "NORMAL"


def test_cw_keeps_the_space_like_vim(vim):
    vim.setBuffer("one two three")
    key(vim, "", _K["Escape"])
    for k in "0cw":
        key(vim, k)
    for k in "ONE":
        key(vim, k)
    key(vim, "", _K["Escape"])
    assert vim.text == "ONE two three"


def test_engine_runs_without_qt():
    """The terminal client reuses this engine on machines without PySide6."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys; sys.modules['PySide6'] = None\n"
        "from muninn.gui import vim\n"
        "v = vim.VimEditor(); out = []\n"
        "v.sendRequested.connect(out.append)\n"
        "for k in 'ihi': v.handleKey(k, ord(k.upper()), False, False, False)\n"
        "v.handleKey('', vim._K['Return'], False, False, False)\n"
        "assert out == ['hi'] and vim._QtGui is None, out\n"
    )
    src = Path(__file__).resolve().parents[1] / "src"
    r = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_cmdline_enter_runs_the_command(vim):
    cmds, sent = [], []
    vim.commandRequested.connect(cmds.append)
    vim.sendRequested.connect(sent.append)
    vim.setBuffer("draft")
    key(vim, "", _K["Escape"])
    vim.openCmdline()
    for k in "dm bob":
        key(vim, k)
    key(vim, "", _K["Return"])
    assert cmds == ["dm bob"] and sent == [] and vim.text == "draft"
