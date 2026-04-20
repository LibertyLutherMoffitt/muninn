"""VimEditor — modal text editor state machine exposed to QML.

QML's TextEdit is read-only; Python owns the buffer. Key events are forwarded
via handleKey(); Python emits bufferUpdated(text, cursor) to drive the view.
"""

from __future__ import annotations

import enum
import re
from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication


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
_CLOSE_BRACKETS = {v: k for k, v in _BRACKETS.items()}
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

    _cmdLineText = Signal(str)

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
        return self._mode.value

    @Property(str, notify=_cmdLineText)
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
        self.modeChanged.emit(self._mode.value)

    # ------------------------------------------------------------------
    # Insert mode
    # ------------------------------------------------------------------

    def _handle_insert(self, key_text: str, key_code: int, ctrl: bool) -> None:
        if key_code == _K["Escape"] or (ctrl and key_text.lower() == "["):
            self._enter_mode(VimMode.NORMAL)
            # Move left (Vim: cursor backs off insert position)
            if self._pos > self._line_start(self._pos):
                self._pos -= 1
            self._emit()
            return

        if ctrl and key_text.lower() == "m":  # Ctrl-M = Return
            key_code = _K["Return"]
            ctrl = False

        if key_code in (_K["Return"], _K["Enter"]):
            if ctrl:
                self._do_send()
            else:
                self._insert_text("\n")
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
            if self._pos > 0:
                self._pos -= 1
                self._emit()
            return

        if key_code == _K["Right"]:
            if self._pos < len(self._buf):
                self._pos += 1
                self._emit()
            return

        if key_code == _K["Up"]:
            self._pos = self._move_up(self._pos)
            self._emit()
            return

        if key_code == _K["Down"]:
            self._pos = self._move_down(self._pos)
            self._emit()
            return

        if key_text and not ctrl and len(key_text) == 1 and ord(key_text) >= 32:
            self._insert_text(key_text)

    # ------------------------------------------------------------------
    # Normal mode
    # ------------------------------------------------------------------

    def _handle_normal(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        # Ctrl-Enter → send from any mode
        if ctrl and key_code in (_K["Return"], _K["Enter"]):
            self._do_send()
            return

        # Count accumulation (1-9 always, 0 only if count already started)
        if not self._pending:
            if key_text.isdigit():
                digit = int(key_text)
                if digit != 0 or self._count is not None:
                    self._count = (self._count or 0) * 10 + digit
                    return
                # 0 with no count: treat as line-start motion below

        # Register prefix
        if not self._pending and key_text == '"':
            self._pending = '"'
            return
        if self._pending == '"':
            self._reg = key_text
            self._pending = ""
            return

        # g-prefix
        if not self._pending and not ctrl and key_text == "g":
            self._pending = "g"
            return
        if self._pending == "g" and not ctrl:
            self._pending = ""
            count = self._pop_count()
            if key_text == "g":
                self._pos = 0
                self._last_col = 0
            elif key_text == "e":
                for _ in range(count):
                    self._pos = self._word_end_back(self._pos, big=False)
            elif key_text == "E":
                for _ in range(count):
                    self._pos = self._word_end_back(self._pos, big=True)
            self._emit()
            return

        # f/F/t/T prefix
        if not self._pending and not ctrl and key_text in "fFtT":
            self._pending = key_text
            return
        if self._pending in "fFtT" and not ctrl:
            op = self._pending
            self._pending = ""
            count = self._pop_count()
            self._last_find = (op, key_text)
            for _ in range(count):
                self._pos = self._find_char(op, key_text, self._pos)
            self._emit()
            return

        # r prefix (replace single char)
        if not self._pending and not ctrl and key_text == "r":
            self._pending = "r"
            return
        if self._pending == "r" and not ctrl:
            self._pending = ""
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._push_undo()
                self._buf = (
                    self._buf[: self._pos] + key_text + self._buf[self._pos + 1 :]
                )
                self._emit()
            self._reset_change()
            return

        # Operator prefix (d c y > <)
        if not self._pending and not ctrl and key_text in "dcy><":
            self._pending = key_text
            return

        # Operator + motion / text object
        if self._pending and self._pending[0] in "dcy><" and not ctrl:
            op = self._pending[0]

            # Double → line operation
            if key_text == op:
                self._pending = ""
                count = self._pop_count()
                self._op_line(op, count)
                return

            # Text object qualifier (i/a)
            if key_text in "ia" and len(self._pending) == 1:
                self._pending = op + key_text
                return

            # Text object second char
            if len(self._pending) == 2 and self._pending[1] in "ia":
                qual = self._pending[1]
                self._pending = ""
                count = self._pop_count()
                start, end = self._text_object(qual, key_text)
                if start is not None and end is not None:
                    self._apply_op(op, start, end)
                    if op == "c":
                        self._enter_mode(VimMode.INSERT)
                return

            # Motion applied to operator
            self._pending = ""
            count = self._pop_count()
            start, end = self._motion_range(key_text, key_code, ctrl, count)
            if start is not None and end is not None and start != end:
                s, e = min(start, end), max(start, end)
                self._apply_op(op, s, e)
                if op == "c":
                    self._enter_mode(VimMode.INSERT)
            else:
                self._count = None
            return

        # Simple commands
        count = self._pop_count()

        if ctrl and key_text.lower() == "d":
            self.scrollRequested.emit(0.25)
            return
        if ctrl and key_text.lower() == "u":
            self.scrollRequested.emit(-0.25)
            return
        if ctrl and key_text.lower() == "f":
            self.scrollRequested.emit(0.5)
            return
        if ctrl and key_text.lower() == "b":
            self.scrollRequested.emit(-0.5)
            return
        if ctrl and key_text.lower() == "r":
            self._redo()
            return

        if key_code == _K["Escape"]:
            self._pending = ""
            self._count = None
            self._reg = '"'
            return

        if key_code in (_K["Return"], _K["Enter"]) and not ctrl:
            self._do_send()
            return

        # Motions
        if key_text == "h" or key_code == _K["Left"]:
            for _ in range(count):
                self._pos = self._move_left(self._pos)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "l" or key_code == _K["Right"]:
            for _ in range(count):
                self._pos = self._move_right_normal(self._pos)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "j" or key_code == _K["Down"]:
            for _ in range(count):
                self._pos = self._move_down_col(self._pos, self._last_col)
            self._emit()
        elif key_text == "k" or key_code == _K["Up"]:
            for _ in range(count):
                self._pos = self._move_up_col(self._pos, self._last_col)
            self._emit()
        elif key_text == "0" or key_code == _K["Home"]:
            self._pos = self._line_start(self._pos)
            self._last_col = 0
            self._emit()
        elif key_text == "^":
            self._pos = self._line_first_nonws(self._pos)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "$" or key_code == _K["End"]:
            end = self._line_end(self._pos)
            self._pos = max(self._line_start(self._pos), end - 1) if end > 0 else 0
            self._last_col = 9999  # sticky end
            self._emit()
        elif key_text == "G":
            if count != 1 or self._count is not None:
                # Jump to line N (already popped)
                lines = self._buf.split("\n")
                n = min(count, len(lines)) - 1
                s = sum(len(ln) + 1 for ln in lines[:n])
                self._pos = min(s, len(self._buf))
            else:
                self._pos = max(0, len(self._buf) - 1)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "w":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, big=False)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "W":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, big=True)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "b":
            for _ in range(count):
                self._pos = self._word_bwd(self._pos, big=False)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "B":
            for _ in range(count):
                self._pos = self._word_bwd(self._pos, big=True)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "e":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, big=False)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "E":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, big=True)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == "%":
            self._pos = self._match_bracket(self._pos)
            self._last_col = self._col_of(self._pos)
            self._emit()
        elif key_text == ";":
            if self._last_find:
                for _ in range(count):
                    self._pos = self._find_char(*self._last_find, self._pos)
                self._emit()
        elif key_text == ",":
            if self._last_find:
                rev = {"f": "F", "F": "f", "t": "T", "T": "t"}[self._last_find[0]]
                for _ in range(count):
                    self._pos = self._find_char(rev, self._last_find[1], self._pos)
                self._emit()
        # Actions
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
        elif key_text == "O":
            self._push_undo()
            start = self._line_start(self._pos)
            self._buf = self._buf[:start] + "\n" + self._buf[start:]
            self._pos = start
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "x":
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._push_undo()
                ch = self._buf[self._pos]
                self._buf = self._buf[: self._pos] + self._buf[self._pos + 1 :]
                self._yank_to_reg(ch)
                if self._pos >= len(self._buf):
                    self._pos = max(0, len(self._buf) - 1)
                self._emit()
        elif key_text == "X":
            if self._pos > self._line_start(self._pos):
                self._push_undo()
                ch = self._buf[self._pos - 1]
                self._buf = self._buf[: self._pos - 1] + self._buf[self._pos :]
                self._pos -= 1
                self._yank_to_reg(ch)
                self._emit()
        elif key_text == "s":
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._push_undo()
                ch = self._buf[self._pos]
                self._buf = self._buf[: self._pos] + self._buf[self._pos + 1 :]
                self._yank_to_reg(ch)
                self._enter_mode(VimMode.INSERT)
                self._emit()
        elif key_text == "S":
            ls = self._line_start(self._pos)
            le = self._line_end(self._pos)
            self._push_undo()
            yanked = self._buf[ls:le]
            self._yank_to_reg(yanked)
            self._buf = self._buf[:ls] + self._buf[le:]
            self._pos = ls
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "D":
            le = self._line_end(self._pos)
            self._push_undo()
            yanked = self._buf[self._pos : le]
            self._yank_to_reg(yanked)
            self._buf = self._buf[: self._pos] + self._buf[le:]
            self._clamp_pos()
            self._emit()
        elif key_text == "C":
            le = self._line_end(self._pos)
            self._push_undo()
            yanked = self._buf[self._pos : le]
            self._yank_to_reg(yanked)
            self._buf = self._buf[: self._pos] + self._buf[le:]
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "Y":
            ls = self._line_start(self._pos)
            le = self._line_end(self._pos)
            self._yank_to_reg(self._buf[ls:le] + "\n")
        elif key_text == "p":
            self._paste(after=True)
        elif key_text == "P":
            self._paste(after=False)
        elif key_text == "~":
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._push_undo()
                ch = self._buf[self._pos]
                toggled = ch.upper() if ch.islower() else ch.lower()
                self._buf = (
                    self._buf[: self._pos] + toggled + self._buf[self._pos + 1 :]
                )
                self._pos = min(self._pos + 1, self._line_end(self._pos))
                self._emit()
        elif key_text == "R":
            self._enter_mode(VimMode.INSERT)
        elif key_text == "u":
            self._undo()
        elif key_text == "v":
            self._visual_anchor = self._pos
            self._enter_mode(VimMode.VISUAL)
        elif key_text == "V":
            self._visual_anchor = self._line_start(self._pos)
            self._pos = self._line_end(self._pos)
            self._enter_mode(VimMode.VISUAL_LINE)
            self._emit_selection()
        elif key_text == "/":
            self._search_fwd = True
            self._enter_cmdline("/")
        elif key_text == "?":
            self._search_fwd = False
            self._enter_cmdline("?")
        elif key_text == "n":
            self._search_next(forward=self._search_fwd)
        elif key_text == "N":
            self._search_next(forward=not self._search_fwd)
        elif key_text == ":":
            self._enter_cmdline(":")
        elif key_text == "Z" and not ctrl:
            # ZZ handled as two-key chord; first Z goes to pending
            if self._pending == "Z":
                self._pending = ""
                self.quitRequested.emit()
            else:
                self._pending = "Z"

    # ------------------------------------------------------------------
    # Visual mode
    # ------------------------------------------------------------------

    def _handle_visual(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        if key_code == _K["Escape"] or key_text == "v" or key_text == "V":
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            return

        if ctrl and key_code in (_K["Return"], _K["Enter"]):
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._do_send()
            return

        # Operators on selection
        if key_text in "dxy":
            s = min(self._visual_anchor, self._pos)
            e = max(self._visual_anchor, self._pos) + 1
            if self._mode == VimMode.VISUAL_LINE:
                s = self._line_start(s)
                e = self._line_end(e - 1) + 1
                if e < len(self._buf):
                    e += 1  # include newline
            yanked = self._buf[s:e]
            self._yank_to_reg(yanked)
            if key_text != "y":
                self._push_undo()
                self._buf = self._buf[:s] + self._buf[e:]
                self._pos = s
                self._clamp_pos()
            else:
                self._pos = s
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
            return

        if key_text == "c":
            s = min(self._visual_anchor, self._pos)
            e = max(self._visual_anchor, self._pos) + 1
            yanked = self._buf[s:e]
            self._yank_to_reg(yanked)
            self._push_undo()
            self._buf = self._buf[:s] + self._buf[e:]
            self._pos = s
            self._clamp_pos()
            self._enter_mode(VimMode.INSERT)
            self.selectionCleared.emit()
            self._emit()
            return

        # Motions in visual — move cursor, extend selection
        count = self._pop_count()
        new_pos = self._visual_motion(key_text, key_code, ctrl, count)
        if new_pos is not None:
            self._pos = new_pos
            self._emit_selection()
            self._emit()

    def _visual_motion(
        self, key_text: str, key_code: int, ctrl: bool, count: int
    ) -> int | None:
        pos = self._pos
        if key_text == "h" or key_code == _K["Left"]:
            for _ in range(count):
                pos = self._move_left(pos)
        elif key_text == "l" or key_code == _K["Right"]:
            for _ in range(count):
                pos = self._move_right_normal(pos)
        elif key_text == "j" or key_code == _K["Down"]:
            for _ in range(count):
                pos = self._move_down(pos)
        elif key_text == "k" or key_code == _K["Up"]:
            for _ in range(count):
                pos = self._move_up(pos)
        elif key_text == "w":
            for _ in range(count):
                pos = self._word_fwd(pos, big=False)
        elif key_text == "b":
            for _ in range(count):
                pos = self._word_bwd(pos, big=False)
        elif key_text == "e":
            for _ in range(count):
                pos = self._word_end_fwd(pos, big=False)
        elif key_text == "$" or key_code == _K["End"]:
            pos = self._line_end(pos)
        elif key_text == "0" or key_code == _K["Home"]:
            pos = self._line_start(pos)
        elif key_text == "G":
            pos = max(0, len(self._buf) - 1)
        else:
            return None
        return pos

    # ------------------------------------------------------------------
    # Command-line mode
    # ------------------------------------------------------------------

    def _enter_cmdline(self, prefix: str) -> None:
        self._cmd_buf = prefix
        self._enter_mode(VimMode.CMD_LINE)
        self.cmdLineChanged.emit(self._cmd_buf)
        self._cmdLineText.emit(self._cmd_buf)

    def _handle_cmdline(self, key_text: str, key_code: int, ctrl: bool) -> None:
        if key_code == _K["Escape"]:
            self._enter_mode(VimMode.NORMAL)
            self.cmdLineChanged.emit("")
            return

        if key_code in (_K["Return"], _K["Enter"]):
            cmd = self._cmd_buf
            self._enter_mode(VimMode.NORMAL)
            self.cmdLineChanged.emit("")
            self._exec_cmdline(cmd)
            return

        if key_code == _K["Backspace"]:
            if len(self._cmd_buf) > 1:
                self._cmd_buf = self._cmd_buf[:-1]
            else:
                self._cmd_buf = ""
                self._enter_mode(VimMode.NORMAL)
                self.cmdLineChanged.emit("")
                self._cmdLineText.emit("")
                return
            self.cmdLineChanged.emit(self._cmd_buf)
            self._cmdLineText.emit(self._cmd_buf)
            return

        if key_text and not ctrl and len(key_text) == 1:
            self._cmd_buf += key_text
            self.cmdLineChanged.emit(self._cmd_buf)
            self._cmdLineText.emit(self._cmd_buf)

    def _exec_cmdline(self, cmd: str) -> None:
        if cmd.startswith("/") or cmd.startswith("?"):
            pat = cmd[1:]
            if pat:
                self._search_pat = pat
            self._search_next(forward=cmd[0] == "/")
            return

        # Strip leading ":"
        if cmd.startswith(":"):
            cmd = cmd[1:].strip()

        if cmd in ("send", ""):
            self._do_send()
        elif cmd in ("quit", "q", "wq"):
            self.quitRequested.emit()
        elif cmd.startswith("nick "):
            # Emit a signal-ish via sendRequested overload? For now no-op here;
            # QML handles :nick via its own command handler.
            pass
        elif cmd == "scan":
            pass  # handled in QML by catching cmdLine text

    # ------------------------------------------------------------------
    # Operator helpers
    # ------------------------------------------------------------------

    def _op_line(self, op: str, count: int) -> None:
        """Double-op: dd, cc, yy, >>, <<."""
        ls = self._line_start(self._pos)
        line_idx = self._line_of(self._pos)
        lines = self._buf.split("\n")
        n = min(count, len(lines) - line_idx)
        end_line = line_idx + n - 1
        le = sum(len(ln) + 1 for ln in lines[: end_line + 1])
        le = min(le, len(self._buf))  # don't go past EOF

        if op in "dc":
            self._push_undo()
            yanked = self._buf[ls:le]
            self._yank_to_reg(yanked)
            self._buf = self._buf[:ls] + self._buf[le:]
            self._pos = ls
            self._clamp_pos()
            self._emit()
            if op == "c":
                self._enter_mode(VimMode.INSERT)
        elif op == "y":
            self._yank_to_reg(self._buf[ls:le])
        elif op in "><":
            self._push_undo()
            for i in range(line_idx, line_idx + n):
                if i >= len(lines):
                    break
                if op == ">":
                    lines[i] = _INDENT + lines[i]
                else:
                    lines[i] = lines[i].removeprefix(_INDENT)
            self._buf = "\n".join(lines)
            self._pos = ls
            self._emit()

    def _apply_op(self, op: str, start: int, end: int) -> None:
        yanked = self._buf[start:end]
        if op in "dc":
            self._push_undo()
            self._yank_to_reg(yanked)
            self._buf = self._buf[:start] + self._buf[end:]
            self._pos = start
            self._clamp_pos()
            self._emit()
        elif op == "y":
            self._yank_to_reg(yanked)
        elif op in "><":
            self._push_undo()
            region = self._buf[start:end]
            lines = region.split("\n")
            if op == ">":
                lines = [_INDENT + ln for ln in lines]
            else:
                lines = [ln.removeprefix(_INDENT) for ln in lines]
            self._buf = self._buf[:start] + "\n".join(lines) + self._buf[end:]
            self._pos = start
            self._emit()

    def _motion_range(
        self, key_text: str, key_code: int, ctrl: bool, count: int
    ) -> tuple[int | None, int | None]:
        """Return (start, end) for operator + motion, exclusive end."""
        pos = self._pos
        new = pos

        if key_text == "w":
            for _ in range(count):
                new = self._word_fwd(new, big=False)
        elif key_text == "W":
            for _ in range(count):
                new = self._word_fwd(new, big=True)
        elif key_text == "b":
            for _ in range(count):
                new = self._word_bwd(new, big=False)
        elif key_text == "B":
            for _ in range(count):
                new = self._word_bwd(new, big=True)
        elif key_text == "e":
            for _ in range(count):
                new = self._word_end_fwd(new, big=False)
            new += 1  # inclusive
        elif key_text == "E":
            for _ in range(count):
                new = self._word_end_fwd(new, big=True)
            new += 1
        elif key_text == "h" or key_code == _K["Left"]:
            new = self._move_left(pos)
        elif key_text == "l" or key_code == _K["Right"]:
            new = self._move_right_normal(pos)
        elif key_text == "$" or key_code == _K["End"]:
            new = self._line_end(pos)
        elif key_text == "0" or key_code == _K["Home"]:
            new = self._line_start(pos)
        elif key_text == "^":
            new = self._line_first_nonws(pos)
        elif key_text == "f" and self._last_find:
            new = self._find_char("f", self._last_find[1], pos) + 1
        else:
            return None, None

        return (min(pos, new), max(pos, new))

    # ------------------------------------------------------------------
    # Text objects
    # ------------------------------------------------------------------

    def _text_object(self, qual: str, obj: str) -> tuple[int | None, int | None]:
        """Return (start, end) for i/a text object."""
        pos = self._pos
        buf = self._buf
        n = len(buf)

        if obj in "wW":
            big = obj == "W"

            # Find word boundaries around pos
            def is_word(c: str) -> bool:
                return (not c.isspace()) if big else (c.isalnum() or c == "_")

            if pos < n and is_word(buf[pos]):
                s = pos
                while s > 0 and is_word(buf[s - 1]):
                    s -= 1
                e = pos
                while e < n and is_word(buf[e]):
                    e += 1
            else:
                return None, None

            if qual == "a":
                # include trailing whitespace (or leading if at end)
                while e < n and buf[e] == " ":
                    e += 1
            return s, e

        if obj in "\"'`":
            # Find enclosing quotes on same line
            ls = self._line_start(pos)
            le = self._line_end(pos)
            line = buf[ls:le]
            rel = pos - ls
            first = line.find(obj)
            if first == -1:
                return None, None
            second = line.find(obj, first + 1)
            if second == -1:
                return None, None
            if rel < first or rel > second:
                return None, None
            if qual == "i":
                return ls + first + 1, ls + second
            else:
                return ls + first, ls + second + 1

        if obj in "([{<)]}>":
            open_c = obj if obj in "([{<" else _CLOSE_BRACKETS.get(obj, obj)
            close_c = _BRACKETS.get(open_c, open_c)
            # Find enclosing pair
            depth = 0
            s = pos
            while s >= 0:
                if s < n and buf[s] == close_c:
                    depth += 1
                if s < n and buf[s] == open_c:
                    if depth == 0:
                        break
                    depth -= 1
                s -= 1
            else:
                return None, None
            e = s + 1
            depth = 0
            while e < n:
                if buf[e] == open_c:
                    depth += 1
                if buf[e] == close_c:
                    if depth == 0:
                        break
                    depth -= 1
                e += 1
            if e >= n:
                return None, None
            if qual == "i":
                return s + 1, e
            else:
                return s, e + 1

        if obj == "p":
            # Paragraph: blank-line bounded block
            ls = pos
            while ls > 0 and buf[ls - 1] != "\n":
                ls -= 1
            # Find paragraph start (skip blank lines going up)
            while ls > 0 and buf[ls : ls + 1] == "\n":
                ls -= 1
            while ls > 0 and buf[ls - 1] != "\n":
                ls -= 1
            le = pos
            while le < n and buf[le] != "\n":
                le += 1
            while le < n and buf[le] == "\n":
                le += 1
            while le < n and buf[le] != "\n":
                le += 1
            return ls, le

        return None, None

    # ------------------------------------------------------------------
    # Paste
    # ------------------------------------------------------------------

    def _paste(self, after: bool) -> None:
        text = self._get_reg()
        if not text:
            return
        self._push_undo()
        if after:
            insert_at = self._pos + 1 if self._pos < len(self._buf) else self._pos
        else:
            insert_at = self._pos
        self._buf = self._buf[:insert_at] + text + self._buf[insert_at:]
        self._pos = insert_at + len(text) - 1
        self._clamp_pos()
        self._emit()

    # ------------------------------------------------------------------
    # Registers
    # ------------------------------------------------------------------

    def _yank_to_reg(self, text: str) -> None:
        self._registers['"'] = text
        self._registers["0"] = text
        if self._reg not in ('"', "0"):
            self._registers[self._reg] = text
        if self._reg in ("+", "*"):
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setText(text)
        # Default register also syncs with clipboard (per plan)
        if self._reg == '"':
            cb = QGuiApplication.clipboard()
            if cb:
                cb.setText(text)

    def _get_reg(self) -> str:
        if self._reg in ("+", "*"):
            cb = QGuiApplication.clipboard()
            return cb.text() if cb else ""
        if self._reg == '"':
            cb = QGuiApplication.clipboard()
            return cb.text() if cb else self._registers.get('"', "")
        return self._registers.get(self._reg, "")

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append((self._buf, self._pos))
        self._redo_stack.clear()

    def _undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append((self._buf, self._pos))
        self._buf, self._pos = self._undo_stack.pop()
        self._clamp_pos()
        self._emit()

    def _redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append((self._buf, self._pos))
        self._buf, self._pos = self._redo_stack.pop()
        self._clamp_pos()
        self._emit()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_next(self, forward: bool) -> None:
        if not self._search_pat:
            return
        pat = self._search_pat
        # Smartcase: if pattern has uppercase, case-sensitive
        flags = 0 if any(c.isupper() for c in pat) else re.IGNORECASE
        try:
            regex = re.compile(pat, flags)
        except re.error:
            return
        buf = self._buf
        if forward:
            m = regex.search(buf, self._pos + 1)
            if not m:
                m = regex.search(buf)  # wrap
        else:
            # Find last match before pos
            matches = list(regex.finditer(buf))
            before = [m for m in matches if m.start() < self._pos]
            m = before[-1] if before else (matches[-1] if matches else None)
        if m:
            self._pos = m.start()
            self._emit()

    # ------------------------------------------------------------------
    # Motion primitives
    # ------------------------------------------------------------------

    def _line_start(self, pos: int) -> int:
        idx = self._buf.rfind("\n", 0, pos)
        return idx + 1

    def _line_end(self, pos: int) -> int:
        idx = self._buf.find("\n", pos)
        return idx if idx != -1 else len(self._buf)

    def _line_of(self, pos: int) -> int:
        return self._buf[:pos].count("\n")

    def _col_of(self, pos: int) -> int:
        return pos - self._line_start(pos)

    def _line_first_nonws(self, pos: int) -> int:
        ls = self._line_start(pos)
        le = self._line_end(pos)
        i = ls
        while i < le and self._buf[i] in " \t":
            i += 1
        return i

    def _move_left(self, pos: int) -> int:
        return max(self._line_start(pos), pos - 1)

    def _move_right_normal(self, pos: int) -> int:
        le = self._line_end(pos)
        # Normal mode: can't go past last char on line
        return min(max(self._line_start(pos), le - 1), pos + 1)

    def _move_right(self, pos: int) -> int:
        return min(len(self._buf), pos + 1)

    def _move_down(self, pos: int) -> int:
        line = self._line_of(pos)
        lines = self._buf.split("\n")
        if line + 1 >= len(lines):
            return pos
        col = self._col_of(pos)
        s = sum(len(ln) + 1 for ln in lines[: line + 1])
        return min(s + col, s + len(lines[line + 1]))

    def _move_up(self, pos: int) -> int:
        line = self._line_of(pos)
        if line == 0:
            return pos
        lines = self._buf.split("\n")
        col = self._col_of(pos)
        s = sum(len(ln) + 1 for ln in lines[: line - 1])
        return min(s + col, s + len(lines[line - 1]))

    def _move_down_col(self, pos: int, col: int) -> int:
        line = self._line_of(pos)
        lines = self._buf.split("\n")
        if line + 1 >= len(lines):
            return pos
        s = sum(len(ln) + 1 for ln in lines[: line + 1])
        return s + min(col, len(lines[line + 1]))

    def _move_up_col(self, pos: int, col: int) -> int:
        line = self._line_of(pos)
        if line == 0:
            return pos
        lines = self._buf.split("\n")
        s = sum(len(ln) + 1 for ln in lines[: line - 1])
        return s + min(col, len(lines[line - 1]))

    def _is_word_char(self, c: str, big: bool = False) -> bool:
        if big:
            return not c.isspace()
        return c.isalnum() or c == "_"

    def _word_fwd(self, pos: int, big: bool) -> int:
        buf = self._buf
        n = len(buf)
        if pos >= n:
            return pos
        # Skip current word / punct group
        if self._is_word_char(buf[pos], big):
            while pos < n and self._is_word_char(buf[pos], big):
                pos += 1
        elif not buf[pos].isspace():
            while (
                pos < n
                and not buf[pos].isspace()
                and not self._is_word_char(buf[pos], big)
            ):
                pos += 1
        # Skip whitespace
        while pos < n and buf[pos].isspace():
            pos += 1
        return min(pos, n - 1)

    def _word_bwd(self, pos: int, big: bool) -> int:
        buf = self._buf
        if pos == 0:
            return 0
        pos -= 1
        # Skip whitespace
        while pos > 0 and buf[pos].isspace():
            pos -= 1
        # Skip word chars
        if self._is_word_char(buf[pos], big):
            while pos > 0 and self._is_word_char(buf[pos - 1], big):
                pos -= 1
        else:
            while (
                pos > 0
                and not buf[pos - 1].isspace()
                and not self._is_word_char(buf[pos - 1], big)
            ):
                pos -= 1
        return pos

    def _word_end_fwd(self, pos: int, big: bool) -> int:
        buf = self._buf
        n = len(buf)
        if pos + 1 >= n:
            return pos
        pos += 1
        while pos < n and buf[pos].isspace():
            pos += 1
        if pos < n and self._is_word_char(buf[pos], big):
            while pos + 1 < n and self._is_word_char(buf[pos + 1], big):
                pos += 1
        elif pos < n:
            while (
                pos + 1 < n
                and not buf[pos + 1].isspace()
                and not self._is_word_char(buf[pos + 1], big)
            ):
                pos += 1
        return min(pos, n - 1)

    def _word_end_back(self, pos: int, big: bool) -> int:
        buf = self._buf
        if pos == 0:
            return 0
        pos -= 1
        while pos > 0 and buf[pos].isspace():
            pos -= 1
        if self._is_word_char(buf[pos], big):
            while pos > 0 and self._is_word_char(buf[pos - 1], big):
                pos -= 1
            pos = pos + (
                1 if pos > 0 and not self._is_word_char(buf[pos - 1], big) else 0
            )
        return pos

    def _find_char(self, op: str, char: str, pos: int) -> int:
        buf = self._buf
        ls = self._line_start(pos)
        le = self._line_end(pos)
        if op == "f":
            idx = buf.find(char, pos + 1, le)
            return idx if idx != -1 else pos
        if op == "F":
            idx = buf.rfind(char, ls, pos)
            return idx if idx != -1 else pos
        if op == "t":
            idx = buf.find(char, pos + 1, le)
            return (idx - 1) if idx > pos else pos
        if op == "T":
            idx = buf.rfind(char, ls, pos)
            return (idx + 1) if idx != -1 and idx + 1 < pos else pos
        return pos

    def _match_bracket(self, pos: int) -> int:
        buf = self._buf
        if pos >= len(buf):
            return pos
        ch = buf[pos]
        if ch in _BRACKETS:
            close = _BRACKETS[ch]
            depth = 0
            i = pos
            while i < len(buf):
                if buf[i] == ch:
                    depth += 1
                elif buf[i] == close:
                    depth -= 1
                    if depth == 0:
                        return i
                i += 1
        elif ch in _CLOSE_BRACKETS:
            open_c = _CLOSE_BRACKETS[ch]
            depth = 0
            i = pos
            while i >= 0:
                if buf[i] == ch:
                    depth += 1
                elif buf[i] == open_c:
                    depth -= 1
                    if depth == 0:
                        return i
                i -= 1
        return pos

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _insert_text(self, text: str) -> None:
        self._push_undo()
        self._buf = self._buf[: self._pos] + text + self._buf[self._pos :]
        self._pos += len(text)
        self._emit()

    def _clamp_pos(self) -> None:
        n = len(self._buf)
        if n == 0:
            self._pos = 0
            return
        if self._mode == VimMode.NORMAL:
            # Can't sit on \n in normal mode; back off to last char on line
            while 0 < self._pos < n and self._buf[self._pos] == "\n":
                self._pos -= 1
        self._pos = max(0, min(self._pos, n - 1))

    def _pop_count(self) -> int:
        c = self._count if self._count is not None else 1
        self._count = None
        return c

    def _reset_change(self) -> None:
        self._reg = '"'

    def _enter_mode(self, mode: VimMode) -> None:
        if self._mode != mode:
            self._mode = mode
            self.modeChanged.emit(mode.value)

    def _emit(self) -> None:
        self.bufferUpdated.emit(self._buf, self._pos)

    def _emit_selection(self) -> None:
        s = min(self._visual_anchor, self._pos)
        e = max(self._visual_anchor, self._pos) + 1
        self.selectionChanged.emit(s, e)

    def _do_send(self) -> None:
        text = self._buf.strip()
        if text:
            self.sendRequested.emit(text)
            self._buf = ""
            self._pos = 0
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
