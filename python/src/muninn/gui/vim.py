"""VimEditor — modal text editor state machine exposed to QML.

QML's TextEdit is read-only; Python owns the buffer. Key events are forwarded
via handleKey(); Python emits bufferUpdated(text, cursor) to drive the view.
"""

from __future__ import annotations

import enum
from PySide6.QtCore import Property, QObject, Signal, Slot


class VimMode(enum.Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL_LINE"
    OP_PENDING = "OP_PENDING"
    CMD_LINE = "CMDLINE"


# Qt key codes (int values)
_K = {
    "Escape": 0x01000000,
    "Return": 0x01000005,
    "Enter": 0x01000005,
    "Backspace": 0x01000003,
    "Delete": 0x01000007,
    "Left": 0x01000012,
    "Right": 0x01000014,
    "Up": 0x01000013,
    "Down": 0x01000015,
    "Home": 0x01000010,
    "End": 0x01000011,
    "PageUp": 0x01000016,
    "PageDown": 0x01000017,
    "Tab": 0x01000001,
}

_BRACKETS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_BRACKETS_REV = {v: k for k, v in _BRACKETS.items()}
_INDENT = "    "  # 4 spaces


class VimEditor(QObject):
    # Signals consumed by QML
    bufferUpdated = Signal(str, int)  # text, cursor_pos
    modeChanged = Signal(str)  # "NORMAL", "INSERT", etc.
    selectionChanged = Signal(int, int)  # start, end (visual)
    selectionCleared = Signal()
    cmdLineChanged = Signal(str)  # ":" + content while in CMD_LINE
    scrollRequested = Signal(float)  # 0.0–1.0
    sendRequested = Signal(str)  # text to send
    quitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = ""
        self._pos = 0
        self._mode = VimMode.NORMAL
        self._pending = ""  # chord accumulation: "g", "f", '"', op+qualifier
        self._count: int | None = None
        self._reg = '"'  # active register name
        self._registers: dict[str, str] = {}
        self._visual_anchor = 0
        self._last_find: tuple[str, str] | None = None  # (op, char): f/F/t/T + char
        self._search_pat = ""
        self._search_fwd = True
        self._cmd_buf = ""
        self._undo_stack: list[tuple[str, int]] = []
        self._redo_stack: list[tuple[str, int]] = []
        self._last_change: tuple | None = None  # (op, count, reg, ...args)
        self._last_col = 0  # remembered column for j/k

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode.name

    @Property(str, notify=cmdLineChanged)
    def cmdLine(self) -> str:
        return self._cmd_buf

    @Property(str)
    def text(self) -> str:
        return self._buf

    @Property(int)
    def cursorPosition(self) -> int:
        return self._pos

    # ------------------------------------------------------------------
    # Public slots
    # ------------------------------------------------------------------

    @Slot(str, int, bool, bool, bool)
    def handleKey(
        self,
        key_text: str,
        key_code: int,
        ctrl: bool,
        shift: bool,
        alt: bool,
    ) -> None:
        # Global: Enter/Return always sends in this specific chat context
        if key_code in (_K["Return"], _K["Enter"]):
            self._do_send()
            return

        if key_code == _K["Escape"]:
            self._enter_mode(VimMode.NORMAL)
            self._pending = ""
            self._count = None
            self._emit()
            return

        if self._mode == VimMode.INSERT:
            self._handle_insert(key_text, key_code, ctrl)
        elif self._mode == VimMode.CMD_LINE:
            self._handle_cmdline(key_text, key_code, ctrl)
        elif self._mode in (VimMode.VISUAL, VimMode.VISUAL_LINE):
            self._handle_visual(key_text, key_code, ctrl, shift)
        else:
            self._handle_normal(key_text, key_code, ctrl, shift)

    @Slot()
    def clear(self) -> None:
        self._push_undo()
        self._buf = ""
        self._pos = 0
        self._mode = VimMode.NORMAL
        self._pending = ""
        self._count = None
        self._reg = '"'
        self._emit()
        self.modeChanged.emit(self._mode.name)

    # ------------------------------------------------------------------
    # Insert mode
    # ------------------------------------------------------------------

    def _handle_insert(self, key_text: str, key_code: int, ctrl: bool) -> None:
        if key_code == _K["Escape"] or (ctrl and key_text.lower() == "["):
            self._enter_mode(VimMode.NORMAL)
            if self._pos > self._line_start(self._pos):
                self._pos -= 1
            self._emit()
            return

        if ctrl and key_text.lower() == "m":  # Ctrl-M = Return
            self._do_send()
            return

        if key_code == _K["Backspace"]:
            if self._pos > 0:
                self._push_undo()
                self._buf = self._buf[: self._pos - 1] + self._buf[self._pos :]
                self._pos -= 1
                self._emit()
            return

        if key_code == _K["Delete"]:
            if self._pos < len(self._buf):
                self._push_undo()
                self._buf = self._buf[: self._pos] + self._buf[self._pos + 1 :]
                self._emit()
            return

        if key_code == _K["Left"]:
            self._pos = max(0, self._pos - 1)
            self._emit()
            return
        if key_code == _K["Right"]:
            self._pos = min(len(self._buf), self._pos + 1)
            self._emit()
            return

        if key_text and not ctrl:
            self._push_undo()
            self._buf = self._buf[: self._pos] + key_text + self._buf[self._pos :]
            self._pos += len(key_text)
            self._emit()

    # ------------------------------------------------------------------
    # Normal mode
    # ------------------------------------------------------------------

    def _handle_normal(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        # Count accumulation
        if not self._pending and key_text.isdigit():
            digit = int(key_text)
            if digit != 0 or self._count is not None:
                self._count = (self._count or 0) * 10 + digit
                return

        # Simple prefixes
        if not self._pending and not ctrl and key_text in "gfrdcy><zZ":
            self._pending = key_text
            return

        # Process pending
        if self._pending:
            p = self._pending
            self._pending = ""
            count = self._pop_count()

            if p == "g":
                if key_text == "g":
                    self._pos = 0
                elif key_text == "e":
                    for _ in range(count):
                        self._pos = self._word_end_back(self._pos, False)
                elif key_text == "E":
                    for _ in range(count):
                        self._pos = self._word_end_back(self._pos, True)
                self._emit()
                return

            if p == "r":
                if self._pos < len(self._buf) and key_text:
                    self._push_undo()
                    self._buf = (
                        self._buf[: self._pos]
                        + key_text[0]
                        + self._buf[self._pos + 1 :]
                    )
                    self._emit()
                return

            if p == "Z":
                if key_text == "Z":
                    self.quitRequested.emit()
                return

        # Main normal mode commands
        count = self._pop_count()

        # Navigation
        if key_text == "h" or key_code == _K["Left"]:
            for _ in range(count):
                self._pos = self._move_left(self._pos)
            self._emit()
        elif key_text == "l" or key_code == _K["Right"]:
            for _ in range(count):
                self._pos = self._move_right_normal(self._pos)
            self._emit()
        elif key_text == "j" or key_code == _K["Down"]:
            for _ in range(count):
                self._pos = self._move_down_col(self._pos, self._last_col)
            self._emit()
        elif key_text == "k" or key_code == _K["Up"]:
            for _ in range(count):
                self._pos = self._move_up_col(self._pos, self._last_col)
            self._emit()

        # Mode switches
        elif key_text == "i":
            self._enter_mode(VimMode.INSERT)
        elif key_text == "I":
            self._pos = self._line_first_nonws(self._pos)
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "a":
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._pos += 1
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "A":
            self._pos = self._line_end(self._pos)
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "o":
            self._push_undo()
            end = self._line_end(self._pos)
            self._buf = self._buf[:end] + "\n" + self._buf[end:]
            self._pos = end + 1
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == ":":
            self._enter_cmdline(":")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pop_count(self) -> int:
        c = self._count if self._count is not None else 1
        self._count = None
        return c

    def _enter_mode(self, mode: VimMode) -> None:
        if self._mode != mode:
            self._mode = mode
            self.modeChanged.emit(mode.name)

    def _enter_cmdline(self, prefix: str) -> None:
        self._mode = VimMode.CMD_LINE
        self._cmd_buf = prefix
        self.modeChanged.emit(self._mode.name)
        self.cmdLineChanged.emit(self._cmd_buf)

    def _emit(self) -> None:
        self.bufferUpdated.emit(self._buf, self._pos)

    def _do_send(self) -> None:
        text = self._buf.strip()
        if text:
            self.sendRequested.emit(text)
            self._buf = ""
            self._pos = 0
            self._emit()

    def _push_undo(self) -> None:
        self._undo_stack.append((self._buf, self._pos))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    # Stub motions
    def _move_left(self, pos: int) -> int:
        return max(0, pos - 1)

    def _move_right_normal(self, pos: int) -> int:
        if pos >= len(self._buf) or self._buf[pos] == "\n":
            return pos
        return pos + 1

    def _line_start(self, pos: int) -> int:
        while pos > 0 and self._buf[pos - 1] != "\n":
            pos -= 1
        return pos

    def _line_end(self, pos: int) -> int:
        while pos < len(self._buf) and self._buf[pos] != "\n":
            pos += 1
        return pos

    def _line_first_nonws(self, pos: int) -> int:
        pos = self._line_start(pos)
        while (
            pos < len(self._buf) and self._buf[pos].isspace() and self._buf[pos] != "\n"
        ):
            pos += 1
        return pos

    def _move_down_col(self, pos: int, col: int) -> int:
        return pos

    def _move_up_col(self, pos: int, col: int) -> int:
        return pos

    def _word_end_back(self, pos: int, big: bool) -> int:
        return pos

    def _handle_cmdline(self, key_text: str, key_code: int, ctrl: bool) -> None:
        pass

    def _handle_visual(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        pass
