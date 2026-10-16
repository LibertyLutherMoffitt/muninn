"""Full-screen terminal client (curses). Same commands as the line CLI.

    ┌ conversations ┐┌ bob · nearby · connected ─────────────────────┐
    │● bob        2 ││ 12:01 bob: hi                                  │
    │◎ carol        ││ 12:02 you: hey ✓✓                              │
    │# trip         ││   ↪ bob: hi                                    │
    └───────────────┘└────────────────────────────────────────────────┘
     > message, or /command                     me · 2 nearby · balanced

Keys: Enter send · Tab / Ctrl-N, Shift-Tab / Ctrl-P switch conversation ·
PgUp/PgDn scroll · Esc message mode (↑↓ select, r reply, e edit, d delete,
1-8 quick react, + react with :shortcode:, o open/download, y copy text, Esc
back) · Ctrl-L redraw · /help for commands · /quit.
"""

from __future__ import annotations

import curses
import locale
import os
import queue
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata

from muninn import emoji
from muninn.cli import ChatUI
from muninn.mesh import conv_group_id, dm_conv
from muninn.node import Node, default_transport
from muninn.protocol import FrameTooLarge
from muninn.storage import StoredMessage


def cwidth(s: str) -> int:
    w = 0
    for ch in s:
        if unicodedata.combining(ch) or ch in "‍️":
            continue
        w += 2 if unicodedata.east_asian_width(ch) in "WF" or ord(ch) >= 0x1F300 else 1
    return w


def clip(s: str, width: int) -> str:
    out, w = [], 0
    for ch in s:
        cw = cwidth(ch)
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def wrap(s: str, width: int) -> list[str]:
    lines = []
    for para in s.split("\n"):
        lines += textwrap.wrap(para, max(10, width), replace_whitespace=False) or [""]
    return lines


class Tui(ChatUI):
    def __init__(self, node: Node):
        self.events: queue.Queue = queue.Queue()
        self.notes: list[tuple[float, str]] = []
        self.input = ""
        self.cursor = 0
        self.history: list[str] = []
        self.hist_i = 0
        self.scroll = 0
        self.mode = "type"  # type | select
        self.sel = -1
        self.reply_to: bytes | None = None
        self.edit_id: bytes | None = None
        self.convs: list[dict] = []
        self.last_typing = 0.0
        super().__init__(node)

    # --- ChatUI hooks: route output into the TUI ---------------------------

    def out(self, line: str) -> None:
        for ln in line.split("\n"):
            self.notes.append((time.time(), ln))
        del self.notes[:-200]
        self.events.put("note")

    def _on_message(self, m: StoredMessage) -> None:
        if m.conv == self.conv and m.sender != self.mesh.node_id:
            self.mesh.mark_read(m.conv)
        elif (
            m.sender != self.mesh.node_id
            and m.kind in ("text", "file")
            and not self.mesh.is_muted(m.conv)
        ):
            self.events.put("bell")
        self.events.put("redraw")

    def _on_update(self, msg_id: bytes) -> None:
        self.events.put("redraw")

    def _on_file(self, file_id: bytes) -> None:
        self.events.put("redraw")

    def _on_typing(self, conv: str, node: str) -> None:
        self.events.put("redraw")

    def _on_peers(self) -> None:
        self.events.put("redraw")

    def switch(self, conv: str) -> None:
        self.conv = conv
        self.scroll = 0
        self.mode, self.sel = "type", -1
        self.reply_to = self.edit_id = None
        self.mesh.mark_read(conv)
        self.events.put("redraw")

    # --- data ---------------------------------------------------------------

    def refresh_convs(self) -> None:
        m = self.mesh
        last = m.storage.last_messages()
        unread = m.storage.unread_counts()
        out = []
        for p in m.active_peers():
            conv = dm_conv(p.node_id)
            pres = self.node.presence(p.node_id)
            if conv not in last and pres["state"] == "offline" and not unread.get(conv):
                continue
            out.append(
                {
                    "conv": conv,
                    "title": p.display,
                    "state": pres["state"],
                    "ts": last[conv].ts if conv in last else 0,
                    "unread": unread.get(conv, 0),
                }
            )
        with m.lock:
            groups = list(m.groups.values())
        for g in groups:
            out.append(
                {
                    "conv": g.conv,
                    "title": g.name,
                    "state": "group",
                    "ts": last[g.conv].ts if g.conv in last else 0,
                    "unread": unread.get(g.conv, 0),
                }
            )
        out.sort(key=lambda c: -c["ts"])
        self.convs = out
        if not self.conv and out:
            self.switch(out[0]["conv"])

    def cycle(self, d: int) -> None:
        ids = [c["conv"] for c in self.convs]
        if not ids:
            return
        i = ids.index(self.conv) if self.conv in ids else -1
        self.switch(ids[(i + d) % len(ids)])

    # --- rendering ------------------------------------------------------------

    def draw(self, scr) -> None:
        h, w = scr.getmaxyx()
        scr.erase()
        if h < 8 or w < 40:
            scr.addstr(0, 0, "window too small")
            scr.refresh()
            return
        side = min(26, max(18, w // 4))
        self.draw_sidebar(scr, h - 2, side)
        self.draw_chat(scr, 0, side + 1, h - 2, w - side - 1)
        self.draw_input(scr, h, w)
        scr.refresh()

    def draw_sidebar(self, scr, h: int, w: int) -> None:
        C = curses.color_pair
        scr.addstr(0, 0, clip(" Muninn", w), C(5) | curses.A_BOLD)
        dots = {
            "direct": ("●", 2),
            "relay": ("◎", 3),
            "nearby": ("○", 6),
            "offline": ("○", 4),
            "group": ("#", 5),
        }
        for i, c in enumerate(self.convs[: h - 2]):
            y = i + 2
            dot, col = dots.get(c["state"], ("○", 4))
            attr = curses.A_REVERSE if c["conv"] == self.conv else 0
            badge = f" {c['unread']}" if c["unread"] else ""
            typing = "…" if self.mesh.typing_in(c["conv"]) else ""
            name = clip(c["title"], w - 4 - len(badge) - len(typing))
            scr.addstr(y, 0, " " * (w - 1), attr)
            scr.addstr(y, 0, dot, C(col) | attr)
            scr.addstr(
                y, 2, name + typing, attr | (curses.A_BOLD if c["unread"] else 0)
            )
            if badge:
                scr.addstr(y, w - 1 - len(badge), badge, C(5) | curses.A_BOLD | attr)
        if not self.convs:
            for i, ln in enumerate(
                wrap(
                    "No one yet. Muninn is looking for people nearby (/scan, /whoami).",
                    w - 2,
                )
            ):
                if 2 + i < h:
                    scr.addstr(2 + i, 1, ln, C(4))
        for y in range(h):
            scr.addstr(y, w, "│", C(4))

    def title(self) -> tuple[str, str]:
        c = self.conv
        if not c:
            return "", ""
        if c.startswith("dm:"):
            nid = c[3:]
            sub = self.node.presence(nid)["text"]
            n = self.mesh.pending_count(nid)
            if n:
                sub += f" · {n} waiting to send"
            return self.mesh.display_name(nid), sub
        gid = conv_group_id(c)
        g = self.mesh.groups.get(gid or b"")
        if not g:
            return "group", ""
        others = [x for x in g.members if x != self.mesh.node_id]
        here = sum(1 for x in others if self.mesh.reachable(x))
        return g.name, f"{here}/{len(others)} in range"

    def msg_lines(
        self, m: StoredMessage, width: int, selected: bool
    ) -> list[tuple[str, int]]:
        C = curses.color_pair
        me = m.sender == self.mesh.node_id
        t = time.strftime("%H:%M", time.localtime(m.ts))
        sel = curses.A_REVERSE if selected else 0
        if m.kind == "system":
            return [(f"  · {ln}", C(4) | sel) for ln in wrap(m.text, width - 4)]
        who = "you" if me else self.mesh.display_name(m.sender)
        lines: list[tuple[str, int]] = []
        if m.reply_to:
            o = self.mesh.storage.get_message(m.reply_to)
            if o:
                ow = (
                    "you"
                    if o.sender == self.mesh.node_id
                    else self.mesh.display_name(o.sender)
                )
                lines.append(
                    (
                        clip(f"      ↪ {ow}: {o.text or o.meta.get('n', '')}", width),
                        C(4) | sel,
                    )
                )
        if m.deleted:
            body = "(deleted)"
        elif m.kind == "file":
            fid = bytes.fromhex(m.meta.get("f", "00"))
            have, total, state = self.mesh.file_progress(fid)
            st = {
                "complete": "saved",
                "downloading": f"{have * 100 // max(total, 1)}%",
            }.get(state, "o to download")
            body = f"📎 {m.meta.get('n')} ({_size(int(m.meta.get('s', 0)))}, {st})" + (
                f" {m.text}" if m.text else ""
            )
        else:
            body = m.text + (" (edited)" if m.edited else "")
        ticks = {"sent": " ◌", "delivered": " ✓", "read": " ✓✓"}[m.state] if me else ""
        head = f"{t} {who}: "
        body_lines = wrap(body + ticks, width - cwidth(head))
        for i, ln in enumerate(body_lines):
            prefix = head if i == 0 else " " * cwidth(head)
            lines.append((prefix + ln, (C(5) if me else 0) | sel))
        if m.reactions:
            lines.append(
                (
                    "      "
                    + "  ".join(
                        f"{e}{len(n) if len(n) > 1 else ''}"
                        for e, n in m.reactions.items()
                    ),
                    C(3) | sel,
                )
            )
        return lines

    def draw_chat(self, scr, y0: int, x0: int, h: int, w: int) -> None:
        C = curses.color_pair
        name, sub = self.title()
        typing = (
            [self.mesh.display_name(n) for n in self.mesh.typing_in(self.conv)]
            if self.conv
            else []
        )
        scr.addstr(y0, x0 + 1, clip(name, w - 2), curses.A_BOLD)
        info = (", ".join(typing) + " typing…") if typing else sub
        scr.addstr(
            y0,
            x0 + 2 + cwidth(clip(name, w - 2)),
            clip("  " + info, max(0, w - cwidth(name) - 4)),
            C(3) if typing else C(4),
        )
        body_h = h - 2
        msgs = self.mesh.storage.load_history(self.conv, 300) if self.conv else []
        self._visible_msgs = msgs
        lines: list[tuple[str, int]] = []
        sel_line = None
        for i, m in enumerate(msgs):
            if i == self.sel:
                sel_line = len(lines)
            lines += self.msg_lines(m, w - 2, i == self.sel)
        recent = [n for t, n in self.notes if time.time() - t < 12][-4:]
        lines += [(n, C(6)) for n in recent]
        if self.mode == "select" and sel_line is not None:
            self.scroll = max(
                0, min(len(lines) - body_h, len(lines) - sel_line - body_h // 2)
            )
        end = len(lines) - self.scroll
        start = max(0, end - body_h)
        for j, (text, attr) in enumerate(lines[start:end]):
            try:
                scr.addstr(y0 + 2 + j, x0 + 1, clip(text, w - 2), attr)
            except curses.error:
                pass
        if not self.conv:
            scr.addstr(
                y0 + 3, x0 + 2, "Pick a conversation with Tab, or /dm <name>.", C(4)
            )

    def draw_input(self, scr, h: int, w: int) -> None:
        C = curses.color_pair
        n = len(self.mesh.connected())
        status = (
            f" {self.mesh.name or 'no name'} · {n} nearby · {self.node.policy.name} "
        )
        if self.mode == "select":
            prompt = (
                " [msg] ↑↓ r:reply e:edit d:delete 1-8/+:react o:open y:copy Esc:back "
            )
        elif self.edit_id:
            prompt = " edit> "
        elif self.reply_to:
            o = self.mesh.storage.get_message(self.reply_to)
            prompt = f" ↪ {clip(o.text if o else '', 18)} > "
        else:
            prompt = " > "
        scr.addstr(h - 2, 0, "─" * (w - 1), C(4))
        scr.addstr(h - 2, max(0, w - cwidth(status) - 2), status, C(4))
        scr.addstr(h - 1, 0, clip(prompt, w - 1), C(5) | curses.A_BOLD)
        avail = w - cwidth(prompt) - 2
        text = self.input
        start = 0
        while cwidth(text[start : self.cursor]) > avail:
            start += 1
        shown = clip(text[start:], avail)
        scr.addstr(h - 1, cwidth(prompt), shown)
        if self.mode == "type":
            try:
                curses.curs_set(1)
                scr.move(h - 1, cwidth(prompt) + cwidth(text[start : self.cursor]))
            except curses.error:
                pass
        else:
            curses.curs_set(0)

    # --- input ----------------------------------------------------------------

    def submit(self) -> bool:
        line = self.input.strip()
        self.input, self.cursor = "", 0
        if not line:
            return True
        self.history.append(line)
        self.hist_i = len(self.history)
        if line.startswith("/"):
            return self.handle(line)
        if not self.conv:
            self.out("! pick a conversation first (Tab or /dm name)")
            return True
        text = emoji.expand(line)
        try:
            if self.edit_id:
                self.mesh.edit(self.conv, self.edit_id, text)
            else:
                self.mesh.send_text(self.conv, text, self.reply_to)
                members = self.mesh.conv_members(self.conv)
                if members and not any(self.mesh.reachable(x) for x in members):
                    self.out(
                        "  out of range — will send automatically when they're back"
                    )
        except (ValueError, FrameTooLarge) as e:
            self.out(f"! {e}")
        self.reply_to = self.edit_id = None
        self.scroll = 0
        return True

    def selected(self) -> StoredMessage | None:
        msgs = getattr(self, "_visible_msgs", [])
        return msgs[self.sel] if 0 <= self.sel < len(msgs) else None

    def key_select(self, k) -> None:
        msgs = getattr(self, "_visible_msgs", [])
        m = self.selected()
        if k in (27, "\x1b"):
            self.mode, self.sel = "type", -1
        elif k in (curses.KEY_UP, "k"):
            self.sel = max(0, self.sel - 1)
        elif k in (curses.KEY_DOWN, "j"):
            self.sel = min(len(msgs) - 1, self.sel + 1)
        elif m is None:
            return
        elif k == "r":
            self.reply_to, self.mode = m.msg_id, "type"
        elif k == "e" and m.sender == self.mesh.node_id and m.kind == "text":
            self.edit_id, self.mode = m.msg_id, "type"
            self.input, self.cursor = m.text, len(m.text)
        elif k == "d" and m.sender == self.mesh.node_id:
            self.mesh.delete(self.conv, m.msg_id)
        elif isinstance(k, str) and k in "12345678":
            e = emoji.QUICK_REACTIONS[int(k) - 1]
            self.mesh.react(
                self.conv,
                m.msg_id,
                e,
                remove=self.mesh.node_id in m.reactions.get(e, []),
            )
        elif k == "+":
            self.mode, self.input, self.cursor = (
                "type",
                f"/react #{self._ref(m.msg_id)} :",
                len(f"/react #{self._ref(m.msg_id)} :"),
            )
        elif k == "o" and m.kind == "file":
            fid = bytes.fromhex(m.meta.get("f", "00"))
            rec = self.mesh.files.get(fid)
            if rec and rec.state == "complete":
                opener = (
                    "xdg-open"
                    if sys.platform == "linux"
                    else ("open" if sys.platform == "darwin" else None)
                )
                if opener:
                    subprocess.Popen(
                        [opener, rec.path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                self.out(f"  {rec.path}")
            else:
                self.mesh.download(fid)
                self.out("  downloading…")
        elif k == "y":
            self.out("  " + m.text)

    def key_type(self, k) -> bool:
        if k in ("\n", "\r", curses.KEY_ENTER, 10, 13):
            return self.submit()
        if k in (27, "\x1b"):
            if self.reply_to or self.edit_id:
                if self.edit_id:
                    self.input, self.cursor = "", 0
                self.reply_to = self.edit_id = None
            elif getattr(self, "_visible_msgs", []):
                self.mode, self.sel = "select", len(self._visible_msgs) - 1
            return True
        if k in ("\t", curses.KEY_BTAB, "\x0e", "\x10"):
            self.cycle(-1 if k in (curses.KEY_BTAB, "\x10") else 1)
        elif k in (curses.KEY_BACKSPACE, "\x7f", "\b", 127, 8):
            if self.cursor:
                self.input = self.input[: self.cursor - 1] + self.input[self.cursor :]
                self.cursor -= 1
        elif k == curses.KEY_DC:
            self.input = self.input[: self.cursor] + self.input[self.cursor + 1 :]
        elif k == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif k == curses.KEY_RIGHT:
            self.cursor = min(len(self.input), self.cursor + 1)
        elif k in (curses.KEY_HOME, "\x01"):
            self.cursor = 0
        elif k in (curses.KEY_END, "\x05"):
            self.cursor = len(self.input)
        elif k == "\x15":  # Ctrl-U
            self.input, self.cursor = self.input[self.cursor :], 0
        elif k == curses.KEY_UP and self.history:
            self.hist_i = max(0, self.hist_i - 1)
            self.input = self.history[self.hist_i]
            self.cursor = len(self.input)
        elif k == curses.KEY_DOWN and self.history:
            self.hist_i = min(len(self.history), self.hist_i + 1)
            self.input = (
                self.history[self.hist_i] if self.hist_i < len(self.history) else ""
            )
            self.cursor = len(self.input)
        elif k == curses.KEY_PPAGE:
            self.scroll += 10
        elif k == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 10)
        elif isinstance(k, str) and k.isprintable():
            self.input = self.input[: self.cursor] + k + self.input[self.cursor :]
            self.cursor += 1
            now = time.monotonic()
            if (
                self.conv
                and not self.input.startswith("/")
                and now - self.last_typing > 3
            ):
                self.last_typing = now
                try:
                    self.mesh.send_typing(self.conv)
                except Exception:
                    pass
        return True

    def loop(self, scr) -> None:
        curses.use_default_colors()
        for i, c in enumerate(
            [
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                curses.COLOR_CYAN,
                8 if curses.COLORS > 8 else curses.COLOR_WHITE,
                curses.COLOR_MAGENTA,
                curses.COLOR_BLUE,
            ],
            start=2,
        ):
            curses.init_pair(i, c, -1)
        scr.keypad(True)
        scr.timeout(250)
        self.refresh_convs()
        if not self.mesh.name:
            self.out(
                "Welcome to Muninn! Type your name and press Enter (people nearby will see it)."
            )
            self.input = "/nick "
            self.cursor = len(self.input)
        dirty = True
        while True:
            while not self.events.empty():
                ev = self.events.get()
                if ev == "bell":
                    curses.beep()
                dirty = True
            if dirty:
                self.refresh_convs()
                self.draw(scr)
                dirty = False
            try:
                k = scr.get_wch()
            except curses.error:
                # periodic refresh for presence / typing expiry
                if int(time.time()) % 3 == 0:
                    dirty = True
                continue
            if k == curses.KEY_RESIZE or k == "\x0c":
                dirty = True
                continue
            if self.mode == "select":
                self.key_select(k)
            elif not self.key_type(k):
                return
            dirty = True


def _size(n: float) -> str:
    for u in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}GB"


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    node = Node(default_transport(), name=os.environ.get("MUNINN_NAME") or None)
    node.start()
    ui = Tui(node)
    try:
        curses.wrapper(ui.loop)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()


if __name__ == "__main__":
    threading.current_thread().name = "tui"
    main()
