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
