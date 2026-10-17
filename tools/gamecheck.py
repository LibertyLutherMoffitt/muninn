#!/usr/bin/env python3
"""Check a Muninn game plugin before sharing it.

    nix develop --command python tools/gamecheck.py my-game.js [--games 40]

Runs the file the way both clients do (a plain JS engine, no browser APIs)
and verifies the contract in python/src/muninn/games/README.md:

  header · size · required functions · setup for every player count and
  option · state is plain JSON and moves never mutate it · deterministic
  replay · illegal moves throw · random games end (or at least stay legal)
  · status shape · render only uses the canvas subset · clicks map to
  legal moves.

Exit code 0 = good to share. Prints a short report either way.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python" / "src"))

from muninn.gamehost import MAX_CODE, parse_header, resolve_opts  # noqa: E402

try:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QJSEngine
except ImportError:  # pragma: no cover
    sys.exit("needs PySide6 — run inside `nix develop`")

CANVAS = [
    "fillRect",
    "strokeRect",
    "clearRect",
    "beginPath",
    "closePath",
    "moveTo",
    "lineTo",
    "arc",
    "quadraticCurveTo",
    "bezierCurveTo",
    "rect",
    "fill",
    "stroke",
    "fillText",
    "save",
    "restore",
    "translate",
    "rotate",
    "scale",
]
PROPS = [
    "fillStyle",
    "strokeStyle",
    "lineWidth",
    "lineCap",
    "font",
    "textAlign",
    "textBaseline",
    "globalAlpha",
]

PRELUDE = """
var __bad = [];
function __ctx() {
  var ok = %s, props = %s, store = {};
  return new Proxy({}, {
    get: function (t, k) {
      if (ok.indexOf(k) >= 0) return function () {};
      if (props.indexOf(k) >= 0) return store[k];
      if (typeof k === "string" && __bad.indexOf("get " + k) < 0) __bad.push("get " + k);
      return undefined;
    },
    set: function (t, k, v) {
      if (props.indexOf(k) < 0 && __bad.indexOf("set " + k) < 0) __bad.push("set " + k);
      store[k] = v; return true;
    }
  });
}
function __rng(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0;
  var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
  return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
""" % (json.dumps(CANVAS), json.dumps(PROPS))

PLAY = """
(function (players, seed, opts, maxPlies) {
  var rand = __rng(seed), names = {}, log = [], problems = [];
  players.forEach(function (p) { names[p] = p; });
  var s = game.setup(players, seed, opts);
  var s0 = JSON.stringify(s);
  if (JSON.stringify(JSON.parse(s0)) !== s0) problems.push("setup state is not plain JSON");
  var view = { w: 400, h: 400 / (game.aspect || 1), me: players[0], players: players,
               names: names, ui: {}, myTurn: true };
  for (var n = 0; n < maxPlies; n++) {
    var st = game.status(s, names);
    if (!st || typeof st.over !== "boolean" || typeof st.text !== "string" || !Array.isArray(st.winners))
      { problems.push("status() must return {over: bool, winners: [], text: string}"); break; }
    st.winners.forEach(function (w) { if (players.indexOf(w) < 0) problems.push("winner not a player: " + w); });
    view.me = players[n % players.length];
    game.render(__ctx(), s, view);
    if (st.over) return { over: true, plies: n, winners: st.winners, text: st.text, log: log, problems: problems };
    var t = game.turn(s), who = (t === null || t === undefined) ? players[n % players.length] : t;
    if (t !== null && t !== undefined && players.indexOf(t) < 0) { problems.push("turn() returned a non-player: " + t); break; }
    if (!game.moves) return { over: false, plies: n, nomoves: true, log: log, problems: problems };
    var legal = game.moves(s, who);
    if (!legal || !legal.length) { problems.push("not over, but no legal moves for " + who + ": " + st.text); break; }
    var m = JSON.parse(JSON.stringify(legal[Math.floor(rand() * legal.length)]));
    var before = JSON.stringify(s), next;
    try { next = game.move(s, who, m); } catch (e) { problems.push("legal move " + JSON.stringify(m) + " threw: " + e); break; }
    if (JSON.stringify(s) !== before) problems.push("move() mutated its input state");
    var again = game.move(JSON.parse(before), who, m);
    if (JSON.stringify(again) !== JSON.stringify(next)) problems.push("move() is not deterministic");
    var js = JSON.stringify(next);
    if (JSON.stringify(JSON.parse(js)) !== js) problems.push("state is not plain JSON after a move");
    log.push([who, m]);
    s = JSON.parse(js);
  }
  return { over: false, plies: maxPlies, log: log, problems: problems };
})
"""

REPLAY = """
(function (players, seed, opts, log) {
  var s = game.setup(players, seed, opts);
  log.forEach(function (e) { s = game.move(s, e[0], e[1]); });
  return JSON.stringify(s);
})
"""

ILLEGAL = """
(function (players, seed, opts) {
  var s = game.setup(players, seed, opts), out = [];
  var who = game.turn(s) || players[0];
  [null, -1, 99999, "zz", [1, 2, 3, 4, 5], {x: 1}, 1.5].forEach(function (m) {
    try { game.move(s, who, m); out.push(JSON.stringify(m)); } catch (e) {}
  });
  return out;
})
"""

CLICKS = """
(function (players, seed, opts) {
  var s = game.setup(players, seed, opts), names = {}, bad = [], moves = 0;
  players.forEach(function (p) { names[p] = p; });
  if (!game.input) return {bad: [], moves: 0};
  var t = game.turn(s), me = (t === null || t === undefined) ? players[0] : t;
  var view = { w: 400, h: 400 / (game.aspect || 1), me: me, players: players, names: names, ui: {}, myTurn: true };
  function tryMove(r, where) {
    if (!r || r.move === undefined || r.move === null) return;
    moves++;
    try { game.move(s, me, JSON.parse(JSON.stringify(r.move))); }
    catch (e) { if (bad.length < 3) bad.push("click " + where + " → " + JSON.stringify(r.move) + ": " + e); }
  }
  var pts = [];
  for (var y = 5; y < view.h; y += 21) for (var x = 5; x < view.w; x += 21) pts.push([x, y]);
  pts.forEach(function (p) {
    var r = game.input(s, { w: view.w, h: view.h, me: me, players: players, names: names, ui: {}, myTurn: true },
                       { type: "click", x: p[0], y: p[1] });
    tryMove(r, "(" + p + ")");
    // click-to-select games (chess, checkers): a second click with that selection
    if (r && r.ui && moves < 400) {
      var ui = JSON.parse(JSON.stringify(r.ui));
      pts.forEach(function (q) {
        tryMove(game.input(s, { w: view.w, h: view.h, me: me, players: players, names: names, ui: ui, myTurn: true },
                           { type: "click", x: q[0], y: q[1] }), "(" + p + ") then (" + q + ")");
      });
    }
  });
  return {bad: bad, moves: moves};
})
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--games", type=int, default=30, help="random games per setup")
    ap.add_argument("--plies", type=int, default=600)
    a = ap.parse_args()

    src = Path(a.file).read_text(encoding="utf-8")
    errors: list[str] = []
    notes: list[str] = []

    try:
        meta = parse_header(src)
    except Exception as e:
        print(f"✗ header: {e}")
        return 1
    size = len(src.encode())
    (errors if size > MAX_CODE else notes).append(
        f"size {size} bytes (limit {MAX_CODE}: it travels inside the invite)"
    )

    _app = QCoreApplication.instance() or QCoreApplication([])
    eng = QJSEngine()
    eng.installExtensions(QJSEngine.Extension.ConsoleExtension)
    eng.evaluate(PRELUDE)
    g = eng.evaluate("(function(){" + src + "\n;return game;})()")
    if g.isError():
        print(f"✗ the file doesn't evaluate: {g.toString()}")
        return 1
    eng.globalObject().setProperty("game", g)
    for fn in ("setup", "turn", "move", "status", "render"):
        if not g.property(fn).isCallable():
            errors.append(f"game.{fn} is missing")
    for fn in ("moves", "input"):
        if not g.property(fn).isCallable():
            notes.append(
                f"game.{fn} is missing ({'bots/tests can' if fn == 'moves' else 'players can'}'t play it)"
            )
    realtime = g.property("tick").isCallable()
    if realtime:
        notes.append("real-time game (tick): turn-based playthroughs skipped")
    if errors:
        for e in errors:
            print("✗", e)
        return 1

    # every player count × every option value (others at default)
    counts = list(range(int(meta["min"]), int(meta["max"]) + 1))
    variants = [{}]
    for o in meta.get("options", []):
        for c in o.get("choices", []):
            variants.append({o["id"]: c})
    play, replay = eng.evaluate(PLAY), eng.evaluate(REPLAY)
    illegal, clicks = eng.evaluate(ILLEGAL), eng.evaluate(CLICKS)
    finished = total = 0
    win_counts: dict[str, int] = {}
    for n, opts in itertools.product(counts, variants):
        players = [f"P{i}" for i in range(n)]
        try:
            full = resolve_opts(meta, opts)
        except ValueError as e:
            errors.append(f"options: {e}")
            continue
        label = f"{n} players" + (f", {opts}" if opts else "")
        for seed in range(1, 0 if realtime else a.games + 1):
            r = play.call(
                [eng.toScriptValue(players), seed, eng.toScriptValue(full), a.plies]
            )
            if r.isError():
                errors.append(f"{label}, seed {seed}: crashed: {r.toString()}")
                break
            res = r.toVariant()
            for p in res.get("problems", []):
                errors.append(f"{label}, seed {seed}: {p}")
            if res.get("problems"):
                break
            total += 1
            if res.get("over"):
                finished += 1
                key = (
                    "draw"
                    if not res["winners"]
                    else ("seat " + ",".join(w[1:] for w in res["winners"]))
                )
                win_counts[key] = win_counts.get(key, 0) + 1
            again = replay.call(
                [
                    eng.toScriptValue(players),
                    seed,
                    eng.toScriptValue(full),
                    eng.toScriptValue(res["log"]),
                ]
            )
            if again.isError():
                errors.append(
                    f"{label}, seed {seed}: replaying the move log fails: {again.toString()}"
                )
                break
        bad = (
            illegal.call(
                [eng.toScriptValue(players), 1, eng.toScriptValue(full)]
            ).toVariant()
            or []
        )
        if bad:
            errors.append(
                f"{label}: move() accepted garbage {', '.join(bad)} — throw on illegal moves"
            )
        c = (
            clicks.call(
                [eng.toScriptValue(players), 1, eng.toScriptValue(full)]
            ).toVariant()
            or {}
        )
        for b in c.get("bad", []):
            errors.append(f"{label}: input() produced an illegal move: {b}")
        if (
            g.property("input").isCallable()
            and not c.get("moves")
            and not g.property("tick").isCallable()
        ):
            notes.append(
                f"{label}: clicking the opening position produced no move (is input() mapping coordinates?)"
            )

    bad_canvas = eng.evaluate("__bad").toVariant() or []
    if bad_canvas:
        errors.append(
            "render() uses things outside the canvas subset: " + ", ".join(bad_canvas)
        )

    print(
        f"{meta['icon']}  {meta['name']} ({meta['id']}), {meta['min']}–{meta['max']} players"
    )
    if total:
        print(
            f"  {total} random games: {finished} finished ({finished * 100 // total}%)"
            + (
                f" — {', '.join(f'{k}: {v}' for k, v in sorted(win_counts.items()))}"
                if win_counts
                else ""
            )
        )
    for nmsg in notes:
        print("  ·", nmsg)
    for e in dict.fromkeys(errors):
        print("  ✗", e)
    if not errors:
        print("  ✓ ready to share")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
