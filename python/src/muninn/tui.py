"""Full-screen terminal client (curses). Same commands as the line CLI.

    ┌ conversations ┐┌ bob · connected directly ─────────────────────┐
    │● bob        2 ││ 12:01 bob: hi                                  │
    │◎ carol        ││ 12:02 you: hey ✓✓                              │
    │# trip         ││   ↪ bob: hi                                    │
    └───────────────┘└────────────────────────────────────────────────┘
     > message, or /command                     me · 2 connected · balanced

Vim keys by default (the same engine as the desktop GUI; `/vim off` for a
plain line editor): you start in INSERT, Esc → NORMAL with motions, operators,
text objects, registers, undo/redo, visual mode; `:` runs a command (`:q`,
`:dm bob`, `:help`); `k` on the top line walks up into the messages (j/k,
Enter/r reply, e edit, + react, 1-8 quick react, dd delete, y copy, o open,
Esc back). Always: Enter sends · Tab / Ctrl-N, Shift-Tab / Ctrl-P switch
conversation · PgUp/PgDn (NORMAL: Ctrl-U/Ctrl-D) scroll · Ctrl-L redraw.
"""

from __future__ import annotations

import curses
import locale
import logging
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
from muninn.gui import vim as vimcore
from muninn.instance import AlreadyRunning
from muninn.mesh import conv_group_id, dm_conv
from muninn.node import Node, default_transport
from muninn.protocol import FrameTooLarge
from muninn.storage import StoredMessage


def cwidth(s: str) -> int:
    w = 0
    for i, ch in enumerate(s):
        if unicodedata.combining(ch) or ch in "\u200d\ufe0f":
            continue
        wide = unicodedata.east_asian_width(ch) in "WF" or ord(ch) >= 0x1F300
        # text-style symbol turned emoji by VS16 (e.g. ✈️ ❤️): two cells
        wide = wide or s[i + 1 : i + 2] == "\ufe0f"
        w += 2 if wide else 1
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
        self.panel: tuple[str, list[str]] | None = None  # (title, lines)
        self.panel_scroll = 0
        self._capture: list[str] | None = None
        self._full_redraw = False
        self._quit = False
        self.cmdline = ""
        super().__init__(node)
        self.vim_on = self.mesh.storage.get_setting("tui_vim") != "off"
        self.vim = vimcore.VimEditor()
        v = self.vim
        v.bufferUpdated.connect(self._vim_buffer)
        v.sendRequested.connect(self._vim_send)
        v.commandRequested.connect(self._vim_command)
        v.cmdLineChanged.connect(self._vim_cmdline)
        v.quitRequested.connect(self._vim_quit)
        v.scrollRequested.connect(self._vim_scroll)
        v.convCycleRequested.connect(self.cycle)
        v.paletteRequested.connect(
            lambda i: (
                v.openCmdline() if i.startswith(":") else self._vim_command("list")
            )
        )
        v.scanRequested.connect(lambda: self._vim_command("scan"))
        v.messageNav.connect(self._vim_msg_nav)
        v.messageAction.connect(self._vim_msg_action)
        v.handleKey("i", 0x49, False, False, False)  # start typing right away
        vimcore.clipboard_setter = _copy_to_clipboard

    # --- ChatUI hooks: route output into the TUI ---------------------------

    def out(self, line: str) -> None:
        if self._capture is not None:  # a /command is running: collect it
            self._capture += line.split("\n")
            return
        for ln in line.split("\n"):
            self.notes.append((time.time(), ln))
        del self.notes[:-200]
        self.events.put("note")

    def _on_message(self, m: StoredMessage) -> None:
        if m.conv == self.conv and m.sender != self.mesh.me:
            self.mesh.mark_read(m.conv)
        elif (
            m.sender != self.mesh.me
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
        if self.panel:
            self.draw_panel(scr, h - 2, w)
        self.draw_input(scr, h, w)
        scr.refresh()

    def draw_panel(self, scr, h: int, w: int) -> None:
        """Command output (/help, /peers, /search …) in a box over the chat."""
        C = curses.color_pair
        title, rows = self.panel or ("", [])
        bw = min(w - 4, max(40, max((cwidth(r) for r in rows), default=0) + 4))
        lines = [ln for r in rows for ln in (wrap(r, bw - 4) if r else [""])]
        bh = min(h - 2, len(lines) + 2)
        y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
        inner = bh - 2
        self.panel_scroll = max(0, min(self.panel_scroll, len(lines) - inner))
        shown = lines[self.panel_scroll : self.panel_scroll + inner]
        top = f"┌ {clip(title, bw - 6)} "
        scr.addstr(y0, x0, top + "─" * (bw - 1 - cwidth(top)) + "┐", C(5))
        for i in range(inner):
            ln = shown[i] if i < len(shown) else ""
            body = " " + clip(ln, bw - 4)
            scr.addstr(y0 + 1 + i, x0, "│", C(5))
            scr.addstr(y0 + 1 + i, x0 + 1, body + " " * (bw - 2 - cwidth(body)))
            scr.addstr(y0 + 1 + i, x0 + bw - 1, "│", C(5))
        more = len(lines) - self.panel_scroll - inner
        bottom = f" {more} more ↓ " if more > 0 else ""
        scr.addstr(
            y0 + bh - 1, x0, "└" + bottom + "─" * (bw - 2 - cwidth(bottom)) + "┘", C(5)
        )

    def key_panel(self, k) -> None:
        if k in (27, "\x1b", "q", "\n", "\r", curses.KEY_ENTER, 10, 13, " "):
            self.panel = None
            self._full_redraw = True
        elif k in (curses.KEY_UP, "k"):
            self.panel_scroll = max(0, self.panel_scroll - 1)
        elif k in (curses.KEY_DOWN, "j"):
            self.panel_scroll += 1
        elif k == curses.KEY_PPAGE:
            self.panel_scroll = max(0, self.panel_scroll - 10)
        elif k == curses.KEY_NPAGE:
            self.panel_scroll += 10

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
        others = [x for x in g.members if x != self.mesh.me]
        here = sum(1 for x in others if self.mesh.person_reachable(x))
        return g.name, f"{here}/{len(others)} in range"

    def msg_lines(
        self, m: StoredMessage, width: int, selected: bool
    ) -> list[tuple[str, int]]:
        C = curses.color_pair
        me = m.sender == self.mesh.me
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
                    if o.sender == self.mesh.me
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
        recent = [n for t, n in self.notes if time.time() - t < 10][-3:]
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
        direct = len(self.mesh.connected())
        relayed = sum(
            1
            for p in self.mesh.active_peers()
            if (self.mesh.person_hops(p.node_id) or 0) > 1
        )
        status = f" {self.mesh.name or 'no name'} · {direct} connected"
        if relayed:
            status += f" · {relayed} via relay"
        if self.node.finding:
            left = self.node.find_left()
            status += (
                f" · finding {left // 60}:{left % 60:02d}" if left else " · finding"
            )
        if self.node.reconnect.name != "fast":
            status += f" · {self.node.reconnect.label.lower()}"
        status += " "
        vm = str(self.vim.mode) if self.vim_on else ""
        if self.panel:
            prompt = " ↑↓ scroll · Esc/q/Enter close "
        elif vm == "CMDLINE":
            prompt = " :"
        elif vm == "MESSAGES":
            prompt = " MESSAGES j/k ↵reply e edit + react 1-8 dd y o Esc "
        elif self.mode == "select":
            prompt = (
                " [msg] ↑↓ r:reply e:edit d:delete 1-8/+:react o:open y:copy Esc:back "
            )
        elif self.edit_id:
            prompt = " edit> "
        elif self.reply_to:
            o = self.mesh.storage.get_message(self.reply_to)
            prompt = f" ↪ {clip(o.text if o else '', 18)} > "
        else:
            prompt = {"NORMAL": " ▪ ", "VISUAL": " v ", "VISUAL_LINE": " V "}.get(
                vm, " > "
            )
        if vm in ("NORMAL", "VISUAL", "VISUAL_LINE", "INSERT"):
            status = f" {vm.lower().replace('_', ' ')} ·" + status
        scr.addstr(h - 2, 0, "─" * (w - 1), C(4))
        status = clip(status, w - 4)
        scr.addstr(h - 2, max(0, w - cwidth(status) - 2), status, C(4))
        scr.addstr(h - 1, 0, clip(prompt, w - 1), C(5) | curses.A_BOLD)
        if self.panel:
            curses.curs_set(0)
            return
        avail = w - cwidth(prompt) - 2
        text = self.cmdline.lstrip(":") if vm == "CMDLINE" else self.input
        if vm == "CMDLINE":
            self.cursor = len(text)
        elif vm == "MESSAGES":
            text = ""
        text = text.replace("\n", "⏎")
        start = 0
        while cwidth(text[start : self.cursor]) > avail:
            start += 1
        shown = clip(text[start:], avail)
        scr.addstr(h - 1, cwidth(prompt), shown)
        if self.mode == "type" or vm == "CMDLINE":
            try:
                curses.curs_set(2 if vm in ("NORMAL", "VISUAL", "VISUAL_LINE") else 1)
                scr.move(h - 1, cwidth(prompt) + cwidth(text[start : self.cursor]))
            except curses.error:
                pass
        else:
            curses.curs_set(0)

    # --- input ----------------------------------------------------------------

    def submit(self) -> bool:
        line = self.input.strip()
        self.input, self.cursor = "", 0
        return self.run_line(line)

    def run_line(self, line: str) -> bool:
        if not line:
            return True
        self.history.append(line)
        self.hist_i = len(self.history)
        if line.split()[0] in ("/vim",):
            arg = (line.split() + [""])[1]
            self.vim_on = arg != "off" if arg else not self.vim_on
            self.mesh.storage.set_setting("tui_vim", "on" if self.vim_on else "off")
            self.mode, self.sel = "type", -1
            self.out(f"  vim keys {'on' if self.vim_on else 'off'}")
            return True
        if line.startswith("/"):
            self._capture = []
            try:
                keep = self.handle(line)
            finally:
                rows, self._capture = self._capture, None
            rows = [r for r in rows if r.strip()] and rows
            if len(rows) > 1:
                self.panel, self.panel_scroll = (line.split()[0], rows), 0
            elif rows:
                self.out(rows[0])
            return keep
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
                if members and not any(self.mesh.person_reachable(x) for x in members):
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
        elif k == "e" and m.sender == self.mesh.me and m.kind == "text":
            self.edit_id, self.mode = m.msg_id, "type"
            self.input, self.cursor = m.text, len(m.text)
        elif k == "d" and m.sender == self.mesh.me:
            self.mesh.delete(self.conv, m.msg_id)
        elif isinstance(k, str) and k in "12345678":
            e = emoji.QUICK_REACTIONS[int(k) - 1]
            self.mesh.react(
                self.conv,
                m.msg_id,
                e,
                remove=self.mesh.me in m.reactions.get(e, []),
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

    # --- vim front end ---------------------------------------------------------

    def _vim_buffer(self, text: str, pos: int) -> None:
        if text != self.input and text and self.vim.mode == "INSERT":
            now = time.monotonic()
            if self.conv and not text.startswith("/") and now - self.last_typing > 3:
                self.last_typing = now
                try:
                    self.mesh.send_typing(self.conv)
                except Exception:
                    pass
        self.input, self.cursor = text, pos

    def _vim_send(self, text: str) -> None:
        if not self.run_line(text):
            self._quit = True
        if text.strip():
            self.history.append(text.strip())
            self.hist_i = len(self.history)

    def _vim_command(self, cmd: str) -> None:
        head = cmd.split()[0] if cmd.split() else ""
        if head in ("q", "q!", "qa", "quit", "wq", "x"):
            self._quit = True
            return
        if not self.run_line("/" + cmd):
            self._quit = True

    def _vim_cmdline(self, text: str) -> None:
        self.cmdline = text

    def _vim_quit(self) -> None:
        self._quit = True

    def _vim_scroll(self, fraction: float) -> None:
        self.scroll = max(0, self.scroll - int(fraction * 20))

    def _vim_msg_nav(self, d: int) -> None:
        msgs = getattr(self, "_visible_msgs", [])
        if not msgs:
            self.vim.leaveMessages()
            return
        if d == 0 or self.mode != "select":
            self.mode, self.sel = "select", len(msgs) - 1
            return
        self.sel += d
        if self.sel >= len(msgs):
            self.mode, self.sel = "type", -1
            self.vim.leaveMessages()
        self.sel = max(0, self.sel) if self.mode == "select" else -1

    def _vim_msg_action(self, a: str) -> None:
        msgs = getattr(self, "_visible_msgs", [])
        if a == "clear":
            self.mode, self.sel = "type", -1
            return
        if a in ("first", "last") and msgs:
            self.sel = 0 if a == "first" else len(msgs) - 1
            return
        keys: dict[str, str] = {
            "reply": "r",
            "edit": "e",
            "react": "+",
            "copy": "y",
            "open": "o",
            "delete": "d",
        }
        key = a[6:] if a.startswith("quick:") else keys.get(a, "")
        if not key:
            return
        self.key_select(key)
        if a in ("reply", "edit", "react"):
            self.mode, self.sel = "type", -1
            self.vim.setBuffer(self.input)  # edit text / "/react #n :" prefill
            if self.vim.mode != "INSERT":
                self.vim.handleKey("A", 0x41, False, True, False)

    def key_vim(self, k) -> bool:
        # Chat-level keys first; everything else is the editor's.
        if k in ("\t", curses.KEY_BTAB, "\x0e", "\x10"):
            self.cycle(-1 if k in (curses.KEY_BTAB, "\x10") else 1)
            return True
        if k == curses.KEY_PPAGE:
            self.scroll += 10
            return True
        if k == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 10)
            return True
        single = "\n" not in self.input
        if (
            self.vim.mode == "INSERT"
            and single
            and k in (curses.KEY_UP, curses.KEY_DOWN)
        ):
            if self.history:  # history, like a shell
                step = -1 if k == curses.KEY_UP else 1
                self.hist_i = max(0, min(len(self.history), self.hist_i + step))
                self.vim.setBuffer(
                    self.history[self.hist_i] if self.hist_i < len(self.history) else ""
                )
            return True
        ev = _qt_key(k)
        if ev is not None:
            self.vim.handleKey(*ev)
        return not self._quit

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
                if self._full_redraw:  # repaint every cell (Ctrl-L, panels)
                    scr.clear()
                    self._full_redraw = False
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
                self._full_redraw = True
                dirty = True
                continue
            if self.panel:
                self.key_panel(k)
            elif self.vim_on:
                if not self.key_vim(k):
                    return
            elif self.mode == "select":
                self.key_select(k)
            elif not self.key_type(k):
                return
            dirty = True


_CURSES_KEYS = {
    curses.KEY_LEFT: "Left",
    curses.KEY_RIGHT: "Right",
    curses.KEY_UP: "Up",
    curses.KEY_DOWN: "Down",
    curses.KEY_HOME: "Home",
    curses.KEY_END: "End",
    curses.KEY_DC: "Delete",
    curses.KEY_BACKSPACE: "Backspace",
    curses.KEY_ENTER: "Return",
}


def _qt_key(k) -> tuple[str, int, bool, bool, bool] | None:
    """curses key → VimEditor.handleKey(text, qt_code, ctrl, shift, alt)."""
    K = vimcore._K
    if isinstance(k, int):
        name = _CURSES_KEYS.get(k)
        return ("", K[name], False, False, False) if name else None
    if k in ("\n", "\r"):
        return ("", K["Return"], False, False, False)
    if k == "\x1b":
        return ("", K["Escape"], False, False, False)
    if k in ("\x7f", "\b"):
        return ("", K["Backspace"], False, False, False)
    if len(k) == 1 and 1 <= ord(k) <= 26:  # Ctrl-A … Ctrl-Z
        letter = chr(ord(k) + 96)
        return (letter, 0x40 + ord(k), True, False, False)
    if len(k) == 1 and k.isprintable():
        code = ord(k.upper()) if k.isalpha() else ord(k)
        return (k, code, False, k.isupper(), False)
    return None


def _copy_to_clipboard(text: str) -> None:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["pbcopy"]):
        try:
            subprocess.run(cmd, input=text.encode(), timeout=2, check=True)
            return
        except (OSError, subprocess.SubprocessError):
            continue


def _size(n: float) -> str:
    for u in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}GB"


def _quiet_streams(path) -> None:
    """Nothing but curses may write to the terminal: a stray print or a
    library warning scrolls the screen and leaves curses' picture of it
    stale (duplicated rows, half-drawn lines). Send it all to a log file."""
    logf = open(path, "a", buffering=1, encoding="utf-8")
    logging.basicConfig(
        stream=logf,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )
    sys.stdout = sys.stderr = logf
    try:
        os.dup2(logf.fileno(), 2)  # C-level warnings (GLib, Qt, D-Bus)
    except OSError:
        pass


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    term_out = sys.stdout
    try:
        node = Node(
            default_transport(),
            name=os.environ.get("MUNINN_NAME") or None,
            log=logging.getLogger("muninn").info,
        )
    except AlreadyRunning as e:
        print(e)
        return
    log_path = node.storage.path.parent / "muninn.log"
    _quiet_streams(log_path)
    try:
        node.start()
    except Exception as e:
        node.stop()
        print(f"Could not start Bluetooth: {e}", file=term_out)
        return
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
