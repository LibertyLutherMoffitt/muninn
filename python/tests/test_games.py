"""Every bundled game plugin: plays to the end with random legal moves,
moves survive a JSON round trip (as on the wire), renders only with the
canvas subset both hosts support, and rejects illegal input."""

import json
from pathlib import Path

import pytest

from muninn.gamehost import BUNDLED, parse_header

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtQml import QJSEngine  # noqa: E402

GAMES = sorted(BUNDLED.glob("*.js"))

# The canvas API subset documented in games/README.md — nothing else exists here.
MOCK_CTX = """
(function () {
  var ctx = {};
  ["fillRect","strokeRect","clearRect","beginPath","closePath","moveTo","lineTo","arc",
   "quadraticCurveTo","bezierCurveTo","rect","fill","stroke","fillText","save","restore",
   "translate","rotate","scale"].forEach(function (n) { ctx[n] = function () { ctx.calls++; }; });
  ctx.calls = 0;
  return ctx;
})()
"""

HARNESS = """
(function (players, seed, rounds) {
  function rng(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0;
    var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  var rand = rng(seed), s = game.setup(players, seed), n = 0, names = {};
  players.forEach(function (p) { names[p] = p; });
  var view = { w: 400, h: 400 / (game.aspect || 1), me: players[0], players: players, names: names, ui: {}, myTurn: true };
  game.render(MOCK, s, view);
  while (n < rounds) {
    var st = game.status(s, names);
    if (st.over) return { over: true, n: n, text: st.text, winners: st.winners };
    var t = game.turn(s);
    var who = (t === null || t === undefined) ? players[0] : t;
    var legal = game.moves(s, who);
    if (!legal.length) return { over: false, n: n, stuck: true, text: st.text };
    var m = JSON.parse(JSON.stringify(legal[Math.floor(rand() * legal.length)]));
    s = game.move(s, who, m);
    s = JSON.parse(JSON.stringify(s));  // state must be plain data
    view.me = players[n % players.length];
    game.render(MOCK, s, view);
    n++;
  }
  return { over: false, n: n, text: game.status(s, names).text };
})
"""


@pytest.fixture(scope="module")
def engine():
    _app = QCoreApplication.instance() or QCoreApplication([])
    yield QJSEngine()


def load(engine, path: Path):
    engine.globalObject().setProperty("MOCK", engine.evaluate(MOCK_CTX))
    g = engine.evaluate("(function(){" + path.read_text() + "\n;return game;})()")
    assert not g.isError(), g.toString()
    engine.globalObject().setProperty("game", g)
    return g


def test_bundled_games_exist():
    ids = {parse_header(p.read_text())["id"] for p in GAMES}
    assert {
        "tictactoe",
        "chess",
        "checkers",
        "connect4",
        "reversi",
        "dots",
        "pong",
    } <= ids


@pytest.mark.parametrize("path", GAMES, ids=lambda p: p.stem)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_playthrough(engine, path, seed):
    meta = parse_header(path.read_text())
    load(engine, path)
    players = [f"P{i}" for i in range(meta["max"])]
    fn = engine.evaluate(HARNESS)
    res = fn.call([engine.toScriptValue(players), seed, 400])
    assert not res.isError(), f"{path.stem}: {res.toString()}"
    r = res.toVariant()
    if meta["id"] == "pong":
        assert r["n"] == 0  # real-time: no turn moves
        return
    assert not r.get("stuck"), r
    if meta["id"] != "chess":  # random chess may not finish in 400 plies
        assert r["over"], r
    assert isinstance(r["text"], str) and r["text"]


@pytest.mark.parametrize("path", GAMES, ids=lambda p: p.stem)
def test_illegal_moves_are_rejected(engine, path):
    load(engine, path)
    meta = parse_header(path.read_text())
    res = engine.evaluate(
        "(function(){var s=game.setup(%s,1);var bad=[null,-1,999,'zz',[1,2,3],{x:1}];"
        "var ok=0;bad.forEach(function(m){try{game.move(s,'P0',m);}catch(e){ok++;}});return ok;})()"
        % json.dumps([f"P{i}" for i in range(meta["max"])])
    )
    assert res.toInt() == 6, path.stem


def test_chess_rules_spot_checks(engine):
    load(engine, BUNDLED / "chess.js")
    # Fool's mate
    r = engine.evaluate("""(function(){var s=game.setup(['W','B'],1);
      ['f2f3','e7e5','g2g4','d8h4'].forEach(function(m){s=game.move(s,game.turn(s),m);});
      return game.status(s,{W:'W',B:'B'}).text;})()""")
    assert r.toString().startswith("Checkmate"), r.toString()
    # castling + en passant are generated
    r = engine.evaluate("""(function(){var s=game.setup(['W','B'],1);
      ['e2e4','a7a6','e4e5','d7d5'].forEach(function(m){s=game.move(s,game.turn(s),m);});
      var ep = game.legal(s).indexOf('e5d6')>=0;
      s=game.setup(['W','B'],1);
      ['e2e4','e7e5','g1f3','b8c6','f1c4','g8f6'].forEach(function(m){s=game.move(s,game.turn(s),m);});
      return [ep, game.legal(s).indexOf('e1g1')>=0];})()""")
    assert r.toVariant() == [True, True]


def test_checkers_forced_capture_and_multijump(engine):
    load(engine, BUNDLED / "checkers.js")
    r = engine.evaluate("""(function(){
      var s=game.setup(['A','B'],1); var b=[];for(var i=0;i<64;i++)b.push(null);
      b[1]='a'; b[10]='b'; b[26]='b'; s.b=b;   // a at row0 can double-jump
      var l=game.legal(s); return JSON.stringify(l);})()""")
    assert json.loads(r.toString()) == [[1, 19, 33]]
