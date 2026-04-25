"""VimEditor — modal text editor state machine exposed to QML.

QML's TextEdit is read-only; Python owns the buffer. Key events are forwarded
via handleKey(); Python emits bufferUpdated(text, cursor) to drive the view.
"""

from __future__ import annotations

import enum
from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QClipboard, QGuiApplication


class VimMode(enum.Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    VISUAL_LINE = "VISUAL_LINE"
    OP_PENDING = "OP_PENDING"
    CMDLINE = "CMDLINE"


# Qt key codes (int values)
_K = {
    "Escape": 0x01000000,
    "Return": 0x01000004,
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

_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


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
    convCycleRequested = Signal(int)  # +1 / -1
    paletteRequested = Signal()
    scanRequested = Signal()
    commandRequested = Signal(str)  # raw command line w/o leading :

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = ""
        self._pos = 0
        self._mode = VimMode.NORMAL
        self._pending = ""  # chord accumulation
        self._count: int | None = None
        self._reg = '"'  # active register name
        self._registers: dict[str, str] = {}
        # Whether each register holds a linewise (yy/dd) chunk vs charwise.
        self._reg_linewise: dict[str, bool] = {}
        self._visual_anchor = 0
        self._last_find: tuple[str, str] | None = None  # (op, char): f/F/t/T + char
        self._search_pat = ""
        self._search_fwd = True
        self._cmd_buf = ""
        self._undo_stack: list[tuple[str, int]] = []
        self._redo_stack: list[tuple[str, int]] = []
        self._last_change: tuple | None = None  # for dot repeat
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
        if key_code in (_K["Return"], _K["Enter"]):
            if shift and self._mode == VimMode.INSERT:
                # Shift+Enter = newline in insert mode
                self._push_undo()
                self._buf = self._buf[: self._pos] + "\n" + self._buf[self._pos :]
                self._pos += 1
                self._emit()
                return
            # Enter = send in any mode
            self._do_send()
            return

        if key_code == _K["Escape"]:
            if self._mode == VimMode.CMDLINE:
                self._cmd_buf = ""
                self.cmdLineChanged.emit("")
            self._enter_mode(VimMode.NORMAL)
            self._pending = ""
            self._count = None
            self._clamp_normal()
            self._emit()
            return

        if self._mode == VimMode.INSERT:
            self._handle_insert(key_text, key_code, ctrl, shift)
        elif self._mode == VimMode.CMDLINE:
            self._handle_cmdline(key_text, key_code, ctrl)
        elif self._mode in (VimMode.VISUAL, VimMode.VISUAL_LINE):
            self._handle_visual(key_text, key_code, ctrl, shift)
        else:
            self._handle_normal(key_text, key_code, ctrl, shift)

    @Slot(str)
    def setCmdLine(self, text: str) -> None:
        """Replace the command-line buffer (used for tab completion)."""
        if self._mode != VimMode.CMDLINE:
            return
        if not text.startswith(":"):
            text = ":" + text.lstrip(":")
        self._cmd_buf = text
        self.cmdLineChanged.emit(self._cmd_buf)

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

    def _handle_insert(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        if key_code == _K["Escape"] or (ctrl and key_text.lower() == "["):
            self._enter_mode(VimMode.NORMAL)
            self._clamp_normal()
            self._emit()
            return

        if ctrl:
            k = key_text.lower()
            if k == "w":
                # delete word backward
                if self._pos > 0:
                    self._push_undo()
                    start = self._word_back(self._pos, False)
                    self._buf = self._buf[:start] + self._buf[self._pos :]
                    self._pos = start
                    self._emit()
                return
            if k == "u":
                # delete to line start
                self._push_undo()
                ls = self._line_start(self._pos)
                self._buf = self._buf[:ls] + self._buf[self._pos :]
                self._pos = ls
                self._emit()
                return
            if k == "h":
                # backspace
                if self._pos > 0:
                    self._push_undo()
                    self._buf = self._buf[: self._pos - 1] + self._buf[self._pos :]
                    self._pos -= 1
                    self._emit()
                return
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
        if key_code == _K["Up"]:
            self._pos = self._move_up_col(self._pos, self._col_of(self._pos))
            self._emit()
            return
        if key_code == _K["Down"]:
            self._pos = self._move_down_col(self._pos, self._col_of(self._pos))
            self._emit()
            return
        if key_code == _K["Home"]:
            self._pos = self._line_start(self._pos)
            self._emit()
            return
        if key_code == _K["End"]:
            self._pos = self._line_end(self._pos)
            self._emit()
            return

        if key_text:
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
        # Ctrl combos
        if ctrl:
            k = key_text.lower()
            if k == "r":
                self._do_redo()
                return
            return

        # Count accumulation
        if not self._pending and key_text.isdigit():
            digit = int(key_text)
            if digit != 0 or self._count is not None:
                self._count = (self._count or 0) * 10 + digit
                return

        # Operator-pending: d, c, y, >, <
        if not self._pending and key_text in 'dcyg><frFtTZ" ':
            self._pending = key_text
            return

        # Register select
        if self._pending == '"':
            self._reg = key_text
            self._pending = ""
            return

        # Process pending chords
        if self._pending:
            p = self._pending
            self._pending = ""
            count = self._pop_count()

            if p == "g":
                self._do_g(key_text, count)
                return

            if p == "r":
                if (
                    self._pos < len(self._buf)
                    and key_text
                    and self._buf[self._pos] != "\n"
                ):
                    self._push_undo()
                    self._buf = (
                        self._buf[: self._pos]
                        + key_text[0]
                        + self._buf[self._pos + 1 :]
                    )
                    self._emit()
                return

            if p in "fFtT":
                if key_text:
                    self._last_find = (p, key_text)
                    for _ in range(count):
                        self._pos = self._do_find_char(p, key_text, self._pos)
                    self._emit()
                return

            if p == "Z":
                if key_text == "Z" or key_text == "Q":
                    self.quitRequested.emit()
                return

            # <space> leader: <space>y = yank to clipboard, <space>f = palette,
            # <space>s = scan dialog
            if p == " ":
                if key_text == "y":
                    self._pending = "\x00"  # clipboard-yank operator
                elif key_text == "f":
                    self.paletteRequested.emit()
                    self._pending = ""
                elif key_text == "s":
                    self.scanRequested.emit()
                    self._pending = ""
                else:
                    self._pending = ""
                return

            # Clipboard yank operator (from <space>y)
            if p == "\x00":
                self._do_operator("y", key_text, count, clipboard=True)
                return

            # Text object completion: _di + char, _ca + char, etc.
            if len(p) == 3 and p[0] == "_":
                op = p[1]
                kind = p[2]  # "i" = inner, "a" = around
                rng = self._text_object(kind, key_text)
                if rng:
                    start, end = rng
                    text = self._buf[start:end]
                    if op == "d":
                        self._push_undo()
                        self._store_reg(text)
                        self._buf = self._buf[:start] + self._buf[end:]
                        self._pos = start
                        self._clamp_normal()
                        self._last_change = ("d_obj", count, kind, key_text)
                        self._emit()
                    elif op == "c":
                        self._push_undo()
                        self._store_reg(text)
                        self._buf = self._buf[:start] + self._buf[end:]
                        self._pos = start
                        self._enter_mode(VimMode.INSERT)
                        self._last_change = ("c_obj", count, kind, key_text)
                        self._emit()
                    elif op == "y":
                        self._store_reg(text)
                        self._emit()
                return

            # Operators: d, c, y, >, <
            if p in "dcy><":
                self._do_operator(p, key_text, count)
                return

        # Main normal mode commands
        count = self._pop_count()

        # Navigation
        if key_text == "h" or key_code == _K["Left"]:
            for _ in range(count):
                self._pos = self._move_left(self._pos)
            self._update_last_col()
            self._emit()
        elif key_text == "l" or key_code == _K["Right"]:
            for _ in range(count):
                self._pos = self._move_right_normal(self._pos)
            self._update_last_col()
            self._emit()
        elif key_text == "j" or key_code == _K["Down"]:
            for _ in range(count):
                self._pos = self._move_down_col(self._pos, self._last_col)
            self._emit()
        elif key_text == "k" or key_code == _K["Up"]:
            for _ in range(count):
                self._pos = self._move_up_col(self._pos, self._last_col)
            self._emit()

        # Word motions
        elif key_text == "w":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, False)
            self._update_last_col()
            self._emit()
        elif key_text == "W":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, True)
            self._update_last_col()
            self._emit()
        elif key_text == "b":
            for _ in range(count):
                self._pos = self._word_back(self._pos, False)
            self._update_last_col()
            self._emit()
        elif key_text == "B":
            for _ in range(count):
                self._pos = self._word_back(self._pos, True)
            self._update_last_col()
            self._emit()
        elif key_text == "e":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, False)
            self._update_last_col()
            self._emit()
        elif key_text == "E":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, True)
            self._update_last_col()
            self._emit()

        # Line motions
        elif key_text == "0":
            self._pos = self._line_start(self._pos)
            self._update_last_col()
            self._emit()
        elif key_text == "^":
            self._pos = self._line_first_nonws(self._pos)
            self._update_last_col()
            self._emit()
        elif key_text == "$":
            self._pos = self._line_end(self._pos)
            if self._pos > 0 and self._pos == len(self._buf):
                pass  # at end of buffer, fine
            elif self._pos > self._line_start(self._pos) and self._pos > 0:
                self._pos -= 1  # normal mode: cursor on last char, not past
            self._update_last_col()
            self._emit()

        # Top/bottom of buffer
        elif key_text == "G":
            if self._count is not None:
                # already popped, but count was the original
                pass
            # G = go to last line
            self._pos = self._line_start(len(self._buf))
            self._update_last_col()
            self._emit()

        # Mode switches
        elif key_text == "i":
            self._enter_mode(VimMode.INSERT)
        elif key_text == "I":
            self._pos = self._line_first_nonws(self._pos)
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "a":
            if (
                self._buf
                and self._pos < len(self._buf)
                and self._buf[self._pos] != "\n"
            ):
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
            ls = self._line_start(self._pos)
            self._buf = self._buf[:ls] + "\n" + self._buf[ls:]
            self._pos = ls
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "s":
            # substitute: delete char under cursor, enter insert
            self._push_undo()
            if (
                self._buf
                and self._pos < len(self._buf)
                and self._buf[self._pos] != "\n"
            ):
                self._store_reg(self._buf[self._pos])
                self._buf = self._buf[: self._pos] + self._buf[self._pos + 1 :]
            self._enter_mode(VimMode.INSERT)
            self._emit()
        elif key_text == "S":
            # substitute whole line
            self._push_undo()
            ls = self._line_start(self._pos)
            le = self._line_end(self._pos)
            self._store_reg(self._buf[ls:le])
            self._buf = self._buf[:ls] + self._buf[le:]
            self._pos = ls
            self._enter_mode(VimMode.INSERT)
            self._emit()

        # Delete/change single chars
        elif key_text == "x":
            for _ in range(count):
                if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                    self._push_undo()
                    self._store_reg(self._buf[self._pos])
                    self._buf = self._buf[: self._pos] + self._buf[self._pos + 1 :]
            self._clamp_normal()
            self._emit()
        elif key_text == "X":
            for _ in range(count):
                if self._pos > self._line_start(self._pos):
                    self._push_undo()
                    self._store_reg(self._buf[self._pos - 1])
                    self._buf = self._buf[: self._pos - 1] + self._buf[self._pos :]
                    self._pos -= 1
            self._emit()

        # D = delete to end of line
        elif key_text == "D":
            self._push_undo()
            le = self._line_end(self._pos)
            self._store_reg(self._buf[self._pos : le])
            self._buf = self._buf[: self._pos] + self._buf[le:]
            self._clamp_normal()
            self._emit()

        # C = change to end of line
        elif key_text == "C":
            self._push_undo()
            le = self._line_end(self._pos)
            self._store_reg(self._buf[self._pos : le])
            self._buf = self._buf[: self._pos] + self._buf[le:]
            self._enter_mode(VimMode.INSERT)
            self._emit()

        # Y = yank line(s); count extends across N lines
        elif key_text == "Y":
            ls = self._line_start(self._pos)
            end_pos = self._pos
            for _ in range(max(1, count) - 1):
                le_step = self._line_end(end_pos)
                if le_step >= len(self._buf):
                    break
                end_pos = le_step + 1
            le = self._line_end(end_pos)
            nl = "\n" if le < len(self._buf) else ""
            self._store_reg(self._buf[ls:le] + nl, linewise=True)

        # Paste — linewise vs charwise based on register type. Counts repeat.
        elif key_text == "p":
            txt, linewise = self._get_reg()
            if txt:
                self._push_undo()
                if linewise:
                    content = txt.rstrip("\n")
                    le = self._line_end(self._pos)
                    repeated = "\n".join([content] * max(1, count))
                    if le >= len(self._buf):
                        self._buf = self._buf + "\n" + repeated
                        new_pos = le + 1
                    else:
                        self._buf = (
                            self._buf[: le + 1] + repeated + "\n" + self._buf[le + 1 :]
                        )
                        new_pos = le + 1
                    self._pos = self._line_first_nonws(new_pos)
                else:
                    repeated = txt * max(1, count)
                    insert_at = (
                        self._pos + 1
                        if self._buf
                        and self._pos < len(self._buf)
                        and self._buf[self._pos] != "\n"
                        else self._pos
                    )
                    self._buf = self._buf[:insert_at] + repeated + self._buf[insert_at:]
                    self._pos = insert_at + len(repeated) - 1 if repeated else insert_at
                self._clamp_normal()
                self._emit()
        elif key_text == "P":
            txt, linewise = self._get_reg()
            if txt:
                self._push_undo()
                if linewise:
                    content = txt.rstrip("\n")
                    repeated = "\n".join([content] * max(1, count))
                    ls = self._line_start(self._pos)
                    self._buf = self._buf[:ls] + repeated + "\n" + self._buf[ls:]
                    self._pos = self._line_first_nonws(ls)
                else:
                    repeated = txt * max(1, count)
                    insert_at = self._pos
                    self._buf = self._buf[:insert_at] + repeated + self._buf[insert_at:]
                    self._pos = insert_at + len(repeated) - 1 if repeated else insert_at
                self._clamp_normal()
                self._emit()

        # Undo / redo
        elif key_text == "u":
            self._do_undo()

        # Join lines
        elif key_text == "J":
            self._push_undo()
            le = self._line_end(self._pos)
            if le < len(self._buf) and self._buf[le] == "\n":
                # remove newline, collapse whitespace to single space
                next_nonws = le + 1
                while next_nonws < len(self._buf) and self._buf[next_nonws] in " \t":
                    next_nonws += 1
                self._buf = self._buf[:le] + " " + self._buf[next_nonws:]
                self._pos = le
            self._emit()

        # Toggle case
        elif key_text == "~":
            if self._pos < len(self._buf) and self._buf[self._pos] != "\n":
                self._push_undo()
                ch = self._buf[self._pos]
                toggled = ch.lower() if ch.isupper() else ch.upper()
                self._buf = (
                    self._buf[: self._pos] + toggled + self._buf[self._pos + 1 :]
                )
                self._pos = min(self._pos + 1, self._line_end(self._pos) - 1)
                self._clamp_normal()
                self._emit()

        # Repeat find
        elif key_text == ";":
            if self._last_find:
                op, ch = self._last_find
                for _ in range(count):
                    self._pos = self._do_find_char(op, ch, self._pos)
                self._emit()
        elif key_text == ",":
            if self._last_find:
                op, ch = self._last_find
                rev = {"f": "F", "F": "f", "t": "T", "T": "t"}[op]
                for _ in range(count):
                    self._pos = self._do_find_char(rev, ch, self._pos)
                self._emit()

        # Dot repeat
        elif key_text == ".":
            if self._last_change:
                self._replay_change()

        # Visual mode
        elif key_text == "v":
            self._visual_anchor = self._pos
            self._enter_mode(VimMode.VISUAL)
            self._emit_selection()
        elif key_text == "V":
            self._visual_anchor = self._pos
            self._enter_mode(VimMode.VISUAL_LINE)
            self._emit_selection()

        # Command line
        elif key_text == ":":
            self._enter_cmdline(":")

    # ------------------------------------------------------------------
    # Operator handling (d, c, y, >, <)
    # ------------------------------------------------------------------

    def _do_operator(
        self, op: str, motion: str, count: int, *, clipboard: bool = False
    ) -> None:
        # Text object prefix: operator + i/a → wait for object char
        if motion in ("i", "a") and op in "dcy":
            self._pending = f"_{op}{motion}"  # e.g. "_di", "_ca"
            self._count = count  # preserve count for next key
            return

        # dd, cc, yy = line-wise; counts extend across N consecutive lines
        if motion == op:
            ls = self._line_start(self._pos)
            end_pos = self._pos
            for _ in range(max(1, count) - 1):
                le_step = self._line_end(end_pos)
                if le_step >= len(self._buf):
                    break
                end_pos = le_step + 1
            le = self._line_end(end_pos)
            # include trailing newline if present
            end = le + 1 if le < len(self._buf) and self._buf[le] == "\n" else le
            text = self._buf[ls:end]

            if op == "d":
                self._push_undo()
                self._store_reg(text, linewise=True, clipboard=clipboard)
                self._buf = self._buf[:ls] + self._buf[end:]
                self._pos = self._line_first_nonws(min(ls, max(0, len(self._buf) - 1)))
                self._clamp_normal()
                self._last_change = ("dd", count)
                self._emit()
            elif op == "c":
                self._push_undo()
                self._store_reg(text, linewise=True, clipboard=clipboard)
                self._buf = self._buf[:ls] + self._buf[le:]
                self._pos = ls
                self._enter_mode(VimMode.INSERT)
                self._last_change = ("cc", count)
                self._emit()
            elif op == "y":
                self._store_reg(text, linewise=True, clipboard=clipboard)
                # yy: don't move cursor
                self._emit()
            return

        # Compute motion range
        result = self._motion_range(motion, count)
        if result[0] is None or result[1] is None:
            return
        start: int = result[0]
        end: int = result[1]

        text = self._buf[start:end]

        if op == "d":
            self._push_undo()
            self._store_reg(text, clipboard=clipboard)
            self._buf = self._buf[:start] + self._buf[end:]
            self._pos = start
            self._clamp_normal()
            self._last_change = ("d", count, motion)
            self._emit()
        elif op == "c":
            self._push_undo()
            self._store_reg(text, clipboard=clipboard)
            self._buf = self._buf[:start] + self._buf[end:]
            self._pos = start
            self._enter_mode(VimMode.INSERT)
            self._last_change = ("c", count, motion)
            self._emit()
        elif op == "y":
            self._store_reg(text, clipboard=clipboard)
            self._emit()
        elif op == ">":
            self._push_undo()
            self._indent_range(start, end, "    ")
            self._emit()
        elif op == "<":
            self._push_undo()
            self._dedent_range(start, end, "    ")
            self._emit()

    def _motion_range(self, motion: str, count: int) -> tuple[int | None, int | None]:
        """Return (start, end) for a motion from current pos."""
        pos = self._pos

        if motion == "w":
            end = pos
            for _ in range(count):
                end = self._word_fwd(end, False)
            return (pos, end)
        elif motion == "W":
            end = pos
            for _ in range(count):
                end = self._word_fwd(end, True)
            return (pos, end)
        elif motion == "b":
            start = pos
            for _ in range(count):
                start = self._word_back(start, False)
            return (start, pos)
        elif motion == "B":
            start = pos
            for _ in range(count):
                start = self._word_back(start, True)
            return (start, pos)
        elif motion == "e":
            end = pos
            for _ in range(count):
                end = self._word_end_fwd(end, False)
            return (pos, end + 1)  # inclusive
        elif motion == "E":
            end = pos
            for _ in range(count):
                end = self._word_end_fwd(end, True)
            return (pos, end + 1)
        elif motion == "$":
            return (pos, self._line_end(pos))
        elif motion == "0":
            return (self._line_start(pos), pos)
        elif motion == "^":
            fnw = self._line_first_nonws(pos)
            return (min(fnw, pos), max(fnw, pos))
        elif motion == "h":
            return (max(0, pos - count), pos)
        elif motion == "l":
            end = min(len(self._buf), pos + count)
            return (pos, end)
        elif motion == "j":
            # line-wise down
            ls = self._line_start(pos)
            target = pos
            for _ in range(count):
                target = self._move_down_col(target, 0)
            le = self._line_end(target)
            end = le + 1 if le < len(self._buf) else le
            return (ls, end)
        elif motion == "k":
            ls = self._line_start(pos)
            le = self._line_end(pos)
            end = le + 1 if le < len(self._buf) else le
            target = pos
            for _ in range(count):
                target = self._move_up_col(target, 0)
            tls = self._line_start(target)
            return (tls, end)

        return (None, None)

    # ------------------------------------------------------------------
    # g-commands
    # ------------------------------------------------------------------

    def _do_g(self, key_text: str, count: int) -> None:
        if key_text == "g":
            self._pos = 0
            self._update_last_col()
            self._emit()
        elif key_text == "e":
            for _ in range(count):
                self._pos = self._word_end_back(self._pos, False)
            self._update_last_col()
            self._emit()
        elif key_text == "E":
            for _ in range(count):
                self._pos = self._word_end_back(self._pos, True)
            self._update_last_col()
            self._emit()

    # ------------------------------------------------------------------
    # Visual mode
    # ------------------------------------------------------------------

    def _handle_visual(
        self, key_text: str, key_code: int, ctrl: bool, shift: bool
    ) -> None:
        count = self._pop_count()

        # Movement
        moved = True
        if key_text == "h" or key_code == _K["Left"]:
            for _ in range(count):
                self._pos = self._move_left(self._pos)
        elif key_text == "l" or key_code == _K["Right"]:
            for _ in range(count):
                self._pos = self._move_right_insert(self._pos)
        elif key_text == "j" or key_code == _K["Down"]:
            for _ in range(count):
                self._pos = self._move_down_col(self._pos, self._last_col)
        elif key_text == "k" or key_code == _K["Up"]:
            for _ in range(count):
                self._pos = self._move_up_col(self._pos, self._last_col)
        elif key_text == "w":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, False)
        elif key_text == "W":
            for _ in range(count):
                self._pos = self._word_fwd(self._pos, True)
        elif key_text == "b":
            for _ in range(count):
                self._pos = self._word_back(self._pos, False)
        elif key_text == "B":
            for _ in range(count):
                self._pos = self._word_back(self._pos, True)
        elif key_text == "e":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, False)
        elif key_text == "E":
            for _ in range(count):
                self._pos = self._word_end_fwd(self._pos, True)
        elif key_text == "0":
            self._pos = self._line_start(self._pos)
        elif key_text == "$":
            self._pos = self._line_end(self._pos)
        elif key_text == "^":
            self._pos = self._line_first_nonws(self._pos)
        elif key_text == "G":
            self._pos = len(self._buf)
        elif key_text == "g":
            self._pending = "g"
            return
        else:
            moved = False

        if moved:
            self._emit_selection()
            self._emit()
            return

        # Actions on selection
        start, end = self._visual_range()
        linewise = self._mode == VimMode.VISUAL_LINE

        if key_text == "d" or key_text == "x":
            self._push_undo()
            self._store_reg(self._buf[start:end], linewise=linewise)
            self._buf = self._buf[:start] + self._buf[end:]
            self._pos = start
            self._enter_mode(VimMode.NORMAL)
            self._clamp_normal()
            self.selectionCleared.emit()
            self._emit()
        elif key_text == "c":
            self._push_undo()
            self._store_reg(self._buf[start:end], linewise=linewise)
            self._buf = self._buf[:start] + self._buf[end:]
            self._pos = start
            self._enter_mode(VimMode.INSERT)
            self.selectionCleared.emit()
            self._emit()
        elif key_text == "y":
            self._store_reg(self._buf[start:end], linewise=linewise)
            self._pos = start
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
        elif key_text == "v":
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
        elif key_text == "V":
            if self._mode == VimMode.VISUAL_LINE:
                self._enter_mode(VimMode.NORMAL)
                self.selectionCleared.emit()
            else:
                self._enter_mode(VimMode.VISUAL_LINE)
                self._emit_selection()
            self._emit()
        elif key_text == "~":
            self._push_undo()
            toggled = ""
            for ch in self._buf[start:end]:
                toggled += ch.lower() if ch.isupper() else ch.upper()
            self._buf = self._buf[:start] + toggled + self._buf[end:]
            self._pos = start
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
        elif key_text == ">":
            self._push_undo()
            self._indent_range(start, end, "    ")
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()
        elif key_text == "<":
            self._push_undo()
            self._dedent_range(start, end, "    ")
            self._enter_mode(VimMode.NORMAL)
            self.selectionCleared.emit()
            self._emit()

    def _visual_range(self) -> tuple[int, int]:
        a, b = self._visual_anchor, self._pos
        start, end = min(a, b), max(a, b)
        if self._mode == VimMode.VISUAL:
            return (start, end + 1)
        else:  # VISUAL_LINE
            start = self._line_start(start)
            end = self._line_end(end)
            if end < len(self._buf):
                end += 1  # include newline
            return (start, end)

    def _emit_selection(self) -> None:
        start, end = self._visual_range()
        self.selectionChanged.emit(start, end)

    # ------------------------------------------------------------------
    # Command line
    # ------------------------------------------------------------------

    def _handle_cmdline(self, key_text: str, key_code: int, ctrl: bool) -> None:
        if key_code == _K["Backspace"]:
            if len(self._cmd_buf) > 1:
                self._cmd_buf = self._cmd_buf[:-1]
                self.cmdLineChanged.emit(self._cmd_buf)
            else:
                self._cmd_buf = ""
                self._enter_mode(VimMode.NORMAL)
                self.cmdLineChanged.emit("")
            return

        if key_code in (_K["Return"], _K["Enter"]):
            self._exec_cmd(self._cmd_buf)
            self._cmd_buf = ""
            self._enter_mode(VimMode.NORMAL)
            self.cmdLineChanged.emit("")
            return

        if key_text and not ctrl:
            self._cmd_buf += key_text
            self.cmdLineChanged.emit(self._cmd_buf)

    def _exec_cmd(self, cmd: str) -> None:
        cmd = cmd.lstrip(":").strip()
        if not cmd:
            return
        head = cmd.split(None, 1)[0].lower()
        # `:wq` / `:x` flush the editor buffer first (the rest of the command
        # will tear down the app via bridge.runCommand).
        if head in ("wq", "x"):
            self._do_send()
        if head == "send":
            self._do_send()
            return
        # Everything else (including q, w, scan, palette, data commands)
        # routes through the bridge so : and <space>f share one dispatcher.
        self.commandRequested.emit(cmd)

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _do_undo(self) -> None:
        if self._undo_stack:
            self._redo_stack.append((self._buf, self._pos))
            self._buf, self._pos = self._undo_stack.pop()
            self._clamp_normal()
            self._emit()

    def _do_redo(self) -> None:
        if self._redo_stack:
            self._undo_stack.append((self._buf, self._pos))
            self._buf, self._pos = self._redo_stack.pop()
            self._clamp_normal()
            self._emit()

    # ------------------------------------------------------------------
    # Dot repeat (simplified)
    # ------------------------------------------------------------------

    def _replay_change(self) -> None:
        if not self._last_change:
            return
        lc = self._last_change
        if lc[0] == "dd":
            self._do_operator("d", "d", lc[1])
        elif lc[0] == "cc":
            self._do_operator("c", "c", lc[1])
        elif lc[0] in ("d", "c") and len(lc) >= 3:
            self._do_operator(lc[0], lc[2], lc[1])

    # ------------------------------------------------------------------
    # Register helpers
    # ------------------------------------------------------------------

    def _store_reg(
        self, text: str, *, linewise: bool = False, clipboard: bool = False
    ) -> None:
        self._registers[self._reg] = text
        self._reg_linewise[self._reg] = linewise
        if self._reg != '"':
            self._registers['"'] = text
            self._reg_linewise['"'] = linewise
        self._reg = '"'
        if clipboard:
            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.setText(text, QClipboard.Mode.Clipboard)

    def _get_reg(self) -> tuple[str, bool]:
        txt = self._registers.get(self._reg, "")
        linewise = self._reg_linewise.get(self._reg, False)
        self._reg = '"'
        return txt, linewise

    # ------------------------------------------------------------------
    # Indent / Dedent
    # ------------------------------------------------------------------

    def _indent_range(self, start: int, end: int, indent: str) -> None:
        ls = self._line_start(start)
        lines = self._buf[ls:end].split("\n")
        indented = "\n".join(indent + line for line in lines)
        self._buf = self._buf[:ls] + indented + self._buf[end:]

    def _dedent_range(self, start: int, end: int, indent: str) -> None:
        ls = self._line_start(start)
        lines = self._buf[ls:end].split("\n")
        dedented = []
        for line in lines:
            if line.startswith(indent):
                dedented.append(line[len(indent) :])
            else:
                dedented.append(
                    line.lstrip(" \t") if line.startswith((" ", "\t")) else line
                )
        self._buf = self._buf[:ls] + "\n".join(dedented) + self._buf[end:]

    # ------------------------------------------------------------------
    # Motion helpers
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
        self._mode = VimMode.CMDLINE
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
        self._enter_mode(VimMode.NORMAL)
        self._emit()

    def _push_undo(self) -> None:
        self._undo_stack.append((self._buf, self._pos))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _clamp_normal(self) -> None:
        """Clamp cursor for normal mode (not past last char)."""
        if self._mode in (VimMode.NORMAL, VimMode.VISUAL, VimMode.VISUAL_LINE):
            if self._buf:
                self._pos = max(0, min(self._pos, len(self._buf) - 1))
            else:
                self._pos = 0

    def _update_last_col(self) -> None:
        self._last_col = self._col_of(self._pos)

    def _col_of(self, pos: int) -> int:
        return pos - self._line_start(pos)

    # --- Basic movement ---

    def _move_left(self, pos: int) -> int:
        if pos <= 0:
            return 0
        if self._buf[pos - 1] == "\n":
            return pos  # don't cross line boundary in normal
        return pos - 1

    def _move_right_normal(self, pos: int) -> int:
        if pos >= len(self._buf) - 1:
            return pos
        if self._buf[pos] == "\n":
            return pos
        if self._buf[pos + 1] == "\n":
            return pos  # stay on last char of line
        return pos + 1

    def _move_right_insert(self, pos: int) -> int:
        if pos >= len(self._buf):
            return pos
        return pos + 1

    def _line_start(self, pos: int) -> int:
        pos = min(pos, len(self._buf))
        while pos > 0 and self._buf[pos - 1] != "\n":
            pos -= 1
        return pos

    def _line_end(self, pos: int) -> int:
        pos = min(pos, len(self._buf))
        while pos < len(self._buf) and self._buf[pos] != "\n":
            pos += 1
        return pos

    def _line_first_nonws(self, pos: int) -> int:
        if not self._buf:
            return 0
        pos = self._line_start(max(0, pos))
        while (
            pos < len(self._buf) and self._buf[pos].isspace() and self._buf[pos] != "\n"
        ):
            pos += 1
        return pos

    def _move_down_col(self, pos: int, col: int) -> int:
        le = self._line_end(pos)
        if le >= len(self._buf):
            return pos  # already on last line
        # next line starts at le + 1
        next_ls = le + 1
        next_le = self._line_end(next_ls)
        next_len = next_le - next_ls
        return next_ls + min(col, max(0, next_len - 1)) if next_len > 0 else next_ls

    def _move_up_col(self, pos: int, col: int) -> int:
        ls = self._line_start(pos)
        if ls == 0:
            return pos  # already on first line
        # prev line ends at ls - 1
        prev_le = ls - 1  # the \n of previous line
        prev_ls = self._line_start(prev_le)
        prev_len = prev_le - prev_ls
        return prev_ls + min(col, max(0, prev_len - 1)) if prev_len > 0 else prev_ls

    # --- Word motions ---

    def _word_fwd(self, pos: int, big: bool) -> int:
        if pos >= len(self._buf):
            return pos
        # skip current word
        if big:
            while pos < len(self._buf) and not self._buf[pos].isspace():
                pos += 1
        else:
            if self._buf[pos] in _WORD_CHARS:
                while pos < len(self._buf) and self._buf[pos] in _WORD_CHARS:
                    pos += 1
            elif not self._buf[pos].isspace():
                while (
                    pos < len(self._buf)
                    and self._buf[pos] not in _WORD_CHARS
                    and not self._buf[pos].isspace()
                ):
                    pos += 1
            else:
                pos += 1
        # skip whitespace
        while (
            pos < len(self._buf) and self._buf[pos].isspace() and self._buf[pos] != "\n"
        ):
            pos += 1
        if pos < len(self._buf) and self._buf[pos] == "\n":
            # skip one newline to get to next line
            if self._buf[max(0, pos - 1)].isspace():
                pos += 1
        return min(pos, len(self._buf))

    def _word_back(self, pos: int, big: bool) -> int:
        if pos <= 0:
            return 0
        pos -= 1
        # skip whitespace backward
        while pos > 0 and self._buf[pos].isspace():
            pos -= 1
        # skip word backward
        if big:
            while pos > 0 and not self._buf[pos - 1].isspace():
                pos -= 1
        else:
            if self._buf[pos] in _WORD_CHARS:
                while pos > 0 and self._buf[pos - 1] in _WORD_CHARS:
                    pos -= 1
            elif not self._buf[pos].isspace():
                while (
                    pos > 0
                    and self._buf[pos - 1] not in _WORD_CHARS
                    and not self._buf[pos - 1].isspace()
                ):
                    pos -= 1
        return pos

    def _word_end_fwd(self, pos: int, big: bool) -> int:
        if pos >= len(self._buf) - 1:
            return pos
        pos += 1
        # skip whitespace
        while pos < len(self._buf) and self._buf[pos].isspace():
            pos += 1
        if pos >= len(self._buf):
            return len(self._buf) - 1
        # skip to end of word
        if big:
            while pos < len(self._buf) - 1 and not self._buf[pos + 1].isspace():
                pos += 1
        else:
            if self._buf[pos] in _WORD_CHARS:
                while pos < len(self._buf) - 1 and self._buf[pos + 1] in _WORD_CHARS:
                    pos += 1
            else:
                while (
                    pos < len(self._buf) - 1
                    and self._buf[pos + 1] not in _WORD_CHARS
                    and not self._buf[pos + 1].isspace()
                ):
                    pos += 1
        return pos

    def _word_end_back(self, pos: int, big: bool) -> int:
        if pos <= 0:
            return 0
        pos -= 1
        if big:
            while pos > 0 and not self._buf[pos].isspace():
                pos -= 1
            while pos > 0 and self._buf[pos].isspace():
                pos -= 1
        else:
            if pos < len(self._buf) and self._buf[pos] in _WORD_CHARS:
                while pos > 0 and self._buf[pos - 1] in _WORD_CHARS:
                    pos -= 1
            elif pos < len(self._buf) and not self._buf[pos].isspace():
                while (
                    pos > 0
                    and self._buf[pos - 1] not in _WORD_CHARS
                    and not self._buf[pos - 1].isspace()
                ):
                    pos -= 1
        return pos

    # --- Text objects (i/a + w, ", ', `, (, ), [, ], {, }, <, >) ---

    _PAIR_OPEN = {
        "(": ")",
        ")": ")",
        "[": "]",
        "]": "]",
        "{": "}",
        "}": "}",
        "<": ">",
        ">": ">",
    }
    _PAIR_CLOSE = {v: v for v in _PAIR_OPEN.values()}
    _QUOTE_CHARS = {'"', "'", "`"}

    def _text_object(self, kind: str, ch: str) -> tuple[int, int] | None:
        """Return (start, end) for inner/around text object. kind='i' or 'a'."""
        if ch == "w" or ch == "W":
            return self._text_object_word(kind, big=(ch == "W"))
        if ch in self._PAIR_OPEN:
            open_ch = (
                ch if ch in "([{<" else {")": "(", "]": "[", "}": "{", ">": "<"}[ch]
            )
            close_ch = self._PAIR_OPEN[ch]
            return self._text_object_pair(kind, open_ch, close_ch)
        if ch in self._QUOTE_CHARS:
            return self._text_object_quote(kind, ch)
        return None

    def _text_object_word(self, kind: str, *, big: bool) -> tuple[int, int] | None:
        if not self._buf:
            return None
        pos = self._pos
        # find word boundaries
        start = pos
        end = pos
        if big:
            while start > 0 and not self._buf[start - 1].isspace():
                start -= 1
            while end < len(self._buf) and not self._buf[end].isspace():
                end += 1
        else:
            if pos < len(self._buf) and self._buf[pos] in _WORD_CHARS:
                while start > 0 and self._buf[start - 1] in _WORD_CHARS:
                    start -= 1
                while end < len(self._buf) and self._buf[end] in _WORD_CHARS:
                    end += 1
            elif pos < len(self._buf) and not self._buf[pos].isspace():
                while (
                    start > 0
                    and self._buf[start - 1] not in _WORD_CHARS
                    and not self._buf[start - 1].isspace()
                ):
                    start -= 1
                while (
                    end < len(self._buf)
                    and self._buf[end] not in _WORD_CHARS
                    and not self._buf[end].isspace()
                ):
                    end += 1
            else:
                # on whitespace
                while (
                    start > 0
                    and self._buf[start - 1].isspace()
                    and self._buf[start - 1] != "\n"
                ):
                    start -= 1
                while (
                    end < len(self._buf)
                    and self._buf[end].isspace()
                    and self._buf[end] != "\n"
                ):
                    end += 1
        if kind == "a":
            # include trailing whitespace (or leading if at end)
            trail = end
            while trail < len(self._buf) and self._buf[trail] == " ":
                trail += 1
            if trail > end:
                end = trail
            else:
                lead = start
                while lead > 0 and self._buf[lead - 1] == " ":
                    lead -= 1
                start = lead
        return (start, end)

    def _text_object_pair(
        self, kind: str, open_ch: str, close_ch: str
    ) -> tuple[int, int] | None:
        # search backward for open
        depth = 0
        i = self._pos
        while i >= 0:
            if self._buf[i] == close_ch and i != self._pos:
                depth += 1
            elif self._buf[i] == open_ch:
                if depth == 0:
                    break
                depth -= 1
            i -= 1
        if i < 0:
            return None
        open_pos = i
        # search forward for close
        depth = 0
        i = open_pos + 1
        while i < len(self._buf):
            if self._buf[i] == open_ch:
                depth += 1
            elif self._buf[i] == close_ch:
                if depth == 0:
                    break
                depth -= 1
            i += 1
        if i >= len(self._buf):
            return None
        close_pos = i
        if kind == "i":
            return (open_pos + 1, close_pos)
        else:
            return (open_pos, close_pos + 1)

    def _text_object_quote(self, kind: str, quote: str) -> tuple[int, int] | None:
        # find quote boundaries on current line
        ls = self._line_start(self._pos)
        le = self._line_end(self._pos)
        line = self._buf[ls:le]
        col = self._pos - ls
        # find pairs of quotes, determine which pair cursor is in
        pairs: list[tuple[int, int]] = []
        i = 0
        while i < len(line):
            if line[i] == quote:
                j = i + 1
                while j < len(line) and line[j] != quote:
                    j += 1
                if j < len(line):
                    pairs.append((i, j))
                    i = j + 1
                else:
                    break
            else:
                i += 1
        for qs, qe in pairs:
            if qs <= col <= qe:
                if kind == "i":
                    return (ls + qs + 1, ls + qe)
                else:
                    return (ls + qs, ls + qe + 1)
        return None

    # --- Find char (f/F/t/T) ---

    def _do_find_char(self, op: str, ch: str, pos: int) -> int:
        if op == "f":
            idx = self._buf.find(ch, pos + 1)
            le = self._line_end(pos)
            if idx != -1 and idx < le:
                return idx
        elif op == "F":
            ls = self._line_start(pos)
            idx = self._buf.rfind(ch, ls, pos)
            if idx != -1:
                return idx
        elif op == "t":
            idx = self._buf.find(ch, pos + 1)
            le = self._line_end(pos)
            if idx != -1 and idx < le:
                return idx - 1
        elif op == "T":
            ls = self._line_start(pos)
            idx = self._buf.rfind(ch, ls, pos)
            if idx != -1:
                return idx + 1
        return pos
