"""Game sessions on top of Mesh app events — the host side of the generic
game API (see games/README.md).

A game is one self-contained JavaScript file (a "plugin") whose first line is

    // @muninn-game {"id": "chess", "name": "Chess", "icon": "♟️", "min": 2, "max": 2}

The plugin's code travels inside the invite, so every participant runs the
exact same rules even if their installed copy differs (or they never had it).

Session event types (all reliable app events, app id = game id):
    invite  {game, name, icon, min, max, seed, code, opts}
    join    {}               decline {}
    start   {players: [...]} (creator, for games with min < max)
    move    {m: <any JSON>}  seq = move number (1, 2, ...)
    resign  {}               end {}  (abandon)
    withdraw {}              (creator, before the game started: "unsend")
    result  {winners, text}  (sent by whoever's move ended the game; feeds
                              the scoreboard — first one counts)
State = setup(players, seed, opts) folded over moves sorted by (seq, ts, sender);
illegal / out-of-turn moves are ignored identically everywhere.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from muninn.node import Node
    from muninn.storage import AppEvent

BUNDLED = Path(__file__).resolve().parent / "games"
HEADER = re.compile(r"^\s*//\s*@muninn-game\s+(\{.*\})\s*$")
MAX_CODE = 40_000


def parse_header(src: str) -> dict:
    first = src.lstrip("﻿").splitlines()[0] if src.strip() else ""
    m = HEADER.match(first)
    if not m:
        raise ValueError("not a Muninn game (missing // @muninn-game header)")
    meta = json.loads(m.group(1))
    if not re.fullmatch(r"[a-z0-9_\-]{1,32}", str(meta.get("id", ""))):
        raise ValueError("game id must be [a-z0-9_-]{1,32}")
    meta.setdefault("name", meta["id"])
    meta.setdefault("icon", "🎮")
    meta.setdefault("min", 2)
    meta.setdefault("max", meta["min"])
    meta.setdefault("options", [])
    return meta


def resolve_opts(meta: dict, opts: dict | None) -> dict:
    """Validate chosen options against the header's `options` (each
    {id, label, choices, default}); missing ones get their default."""
    out = {}
    opts = opts or {}
    for o in meta.get("options", []):
        v = opts.get(o["id"], o.get("default"))
        choices = o.get("choices")
        if choices and v not in choices:
            # accept "5" for 5 from text UIs
            v = next((c for c in choices if str(c) == str(v)), None)
            if v is None:
                raise ValueError(
                    f"{o['id']} must be one of: {', '.join(map(str, choices))}"
                )
        out[o["id"]] = v
    unknown = set(opts) - set(out)
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(sorted(unknown))}")
    return out


class GameRegistry:
    def __init__(self, node: Node):
        self.node = node
        self.mesh = node.mesh
        self.user_dir = Path(node.storage.path).parent / "games"
        self.user_dir.mkdir(parents=True, exist_ok=True)

    # --- catalog ---------------------------------------------------------------

    def _files(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for d in (BUNDLED, self.user_dir):
            if d.is_dir():
                for p in sorted(d.glob("*.js")):
                    try:
                        meta = parse_header(p.read_text(encoding="utf-8"))
                    except (ValueError, OSError, json.JSONDecodeError):
                        continue
                    out[meta["id"]] = p
        return out

    def catalog(self) -> list[dict]:
        res = []
        for gid, p in self._files().items():
            meta = parse_header(p.read_text(encoding="utf-8"))
            players = (
                f"{meta['min']}"
                if meta["min"] == meta["max"]
                else f"{meta['min']}–{meta['max']}"
            )
            res.append(
                {
                    "id": gid,
                    "name": meta["name"],
                    "icon": meta["icon"],
                    "players": players + " players",
                    "options": meta["options"],
                    "user": p.parent == self.user_dir,
                }
            )
        return sorted(res, key=lambda g: g["name"].lower())

    def path_of(self, game_id: str) -> str:
        return str(self._files()[game_id])

    def install(self, path: str) -> str:
        src = Path(path).read_text(encoding="utf-8")
        if len(src) > MAX_CODE * 4:
            raise ValueError("game file too large")
        meta = parse_header(src)
        shutil.copyfile(path, self.user_dir / f"{meta['id']}.js")
        return meta["id"]

    # --- sessions ---------------------------------------------------------------

    def invite(self, conv: str, game_id: str, opts: dict | None = None) -> str:
        files = self._files()
        if game_id not in files:
            raise ValueError(f"no such game: {game_id}")
        src = files[game_id].read_text(encoding="utf-8")
        if len(src.encode()) > MAX_CODE:
            raise ValueError("game code too large to send in an invite")
        meta = parse_header(src)
        chosen = resolve_opts(meta, opts)
        if not self.mesh.conv_members(conv):
            raise ValueError("nobody to play with in this conversation")
        session = os.urandom(6).hex()
        self.mesh.app_event(
            conv,
            game_id,
            session,
            "invite",
            {
                "game": game_id,
                "name": meta["name"],
                "icon": meta["icon"],
                "min": meta["min"],
                "max": meta["max"],
                "seed": random.randrange(2**31),
                "code": src,
                "opts": chosen,
            },
            0,
        )
        return session

    def _events(self, session: str) -> list[AppEvent]:
        return self.mesh.storage.load_app_events(session)

    def on_event(self, ev: AppEvent) -> None:
        pass  # state is derived on demand from the stored event log

    def session_info(self, session: str) -> dict:
        evs = self._events(session)
        inv = next((e for e in evs if e.type == "invite"), None)
        if inv is None:
            return {}
        b = inv.body or {}
        creator = inv.sender
        joins = sorted(
            (e for e in evs if e.type == "join" and e.sender != creator),
            key=lambda e: (e.ts, e.sender),
        )
        declined = sorted({e.sender for e in evs if e.type == "decline"})
        start = next(
            (e for e in evs if e.type == "start" and e.sender == creator), None
        )
        mx, mn = int(b.get("max", 2)), int(b.get("min", 2))
        joined = [creator]
        for e in joins:
            if e.sender not in joined:
                joined.append(e.sender)
        if start is not None:
            players = [p for p in start.body.get("players", []) if p in joined]
        else:
            players = joined[:mx]
        started = start is not None or len(players) >= mx
        moves = sorted(
            (e for e in evs if e.type == "move"), key=lambda e: (e.seq, e.ts, e.sender)
        )
        ended = [e for e in evs if e.type in ("resign", "end")]
        # an unsent invite stays unsent on every device, even if someone
        # joined meanwhile (no explicit "start" yet = the creator's call)
        withdrawn = start is None and any(
            e.type == "withdraw" and e.sender == creator for e in evs
        )
        started = started and not withdrawn
        result = next((e.body for e in evs if e.type == "result"), None)
        if result is None and ended and ended[0].type == "resign":
            rest = [p for p in players if p != ended[0].sender]
            result = {"winners": rest if len(rest) == 1 else [], "text": "resigned"}
        me = self.mesh.node_id
        if withdrawn:
            status = "invite withdrawn"
        elif ended:
            status = f"{self.mesh.display_name(ended[0].sender)} " + (
                "resigned" if ended[0].type == "resign" else "ended the game"
            )
        elif started:
            status = f"in progress · {len(moves)} moves"
        elif len(players) >= mn:
            status = f"{len(players)}/{mx} joined — ready, the host can start"
        else:
            n = mn - len(players)
            status = f"waiting for {n} more player{'s' if n > 1 else ''}"
        return {
            "session": session,
            "app": inv.app,
            "conv": inv.conv,
            "name": b.get("name", inv.app),
            "icon": b.get("icon", "🎮"),
            "creator": creator,
            "seed": b.get("seed", 0),
            "opts": b.get("opts") or {},
            "min": mn,
            "max": mx,
            "joined": joined,
            "players": players if started else joined,
            "declined": declined,
            "started": started,
            "ended": bool(ended),
            "endedBy": ended[0].sender if ended else "",
            "endType": ended[0].type if ended else "",
            "me": me,
            "amPlayer": me in (players if started else joined),
            # a full or running game can still be watched, not joined
            "invitedMe": me != creator
            and me not in joined
            and me not in declined
            and not started
            and not withdrawn
            and len(joined) < mx,
            "spectator": started and me not in players,
            "names": {p: self.mesh.display_name(p) for p in set(joined) | {me}},
            "moves": [
                {"seq": e.seq, "sender": e.sender, "m": (e.body or {}).get("m")}
                for e in moves
            ],
            "status": status,
            "withdrawn": withdrawn,
            "result": result,
            "ts": evs[-1].ts if evs else 0,
        }

    def code_for(self, session: str) -> str:
        inv = next((e for e in self._events(session) if e.type == "invite"), None)
        return (inv.body or {}).get("code", "") if inv else ""

    def sessions(self, conv: str | None, include_withdrawn: bool = False) -> list[dict]:
        out = []
        for session, app, c, _ts in self.mesh.storage.list_app_sessions(conv):
            info = self.session_info(session)
            if info and (include_withdrawn or not info["withdrawn"]):
                out.append(info)
        return out

    def scores(self, conv: str | None = None) -> list[dict]:
        """Wins per player for each game (finished games only), newest game
        first: [{app, name, icon, played, draws, wins: {node: n}}]."""
        by: dict[str, dict] = {}
        for s in self.sessions(conv):
            r = s.get("result")
            if not s["started"] or not r:
                continue
            g = by.setdefault(
                s["app"],
                {
                    "app": s["app"],
                    "name": s["name"],
                    "icon": s["icon"],
                    "played": 0,
                    "draws": 0,
                    "wins": {},
                },
            )
            g["played"] += 1
            winners = [w for w in r.get("winners") or [] if isinstance(w, str)]
            if not winners:
                g["draws"] += 1
            for w in winners:
                g["wins"][w] = g["wins"].get(w, 0) + 1
        return sorted(by.values(), key=lambda g: -g["played"])

    def withdraw(self, session: str) -> None:
        info = self.session_info(session)
        if not info:
            raise ValueError("unknown game session")
        if info["creator"] != self.mesh.node_id:
            raise ValueError("only whoever sent the invite can unsend it")
        if info["started"]:
            raise ValueError("the game already started — resign or end it instead")
        self.act(session, "withdraw", {})

    def act(self, session: str, type_: str, body) -> None:
        info = self.session_info(session)
        if not info:
            raise ValueError("unknown game session")
        seq = 0
        if type_ == "move":
            seq = (max((m["seq"] for m in info["moves"]), default=0)) + 1
            body = {"m": body}
        self.mesh.app_event(info["conv"], info["app"], session, type_, body or {}, seq)

    def rt(self, session: str, body) -> None:
        info = self.session_info(session)
        if info:
            # everyone in the chat, so watchers see real-time games live too
            to = [
                p
                for p in self.mesh.conv_members(info["conv"])
                if p != self.mesh.node_id
            ]
            self.mesh.rt_send(info["conv"], info["app"], session, body, to=to)
