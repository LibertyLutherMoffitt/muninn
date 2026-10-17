"""Every bundled game plugin: plays to the end with random legal moves,
moves survive a JSON round trip (as on the wire), renders only with the
canvas subset both hosts support, and rejects illegal input."""

import json
import time
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
        "uttt",
        "uuttt",
        "unlimited",
        "chess4",
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
    # may not finish in 400 random plies (unlimited: 8 players, endless board)
    if meta["id"] not in ("chess", "chess4", "uuttt", "unlimited"):
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


def test_ultimate_sends_opponent_to_matching_board(engine):
    load(engine, BUNDLED / "uttt.js")
    r = engine.evaluate("""(function(){var s=game.setup(['A','B'],1);
      s=game.move(s,'A',4*9+2);            // centre board, top-right cell
      var t1=JSON.stringify(s.target);       // -> board 2
      var ok=game.legalAt(s,2*9+0), bad=game.legalAt(s,5*9+0);
      return [t1, ok, bad];})()""")
    assert r.toVariant() == ["[2]", True, False]
    # a decided target board frees the opponent
    r = engine.evaluate("""(function(){var s=game.setup(['A','B'],1);
      var seq=[0*9+3, 3*9+0, 0*9+4, 4*9+0, 0*9+5];  // A wins board 0 (row 3,4,5)
      for(var i=0;i<seq.length;i++) s=game.move(s,i%2?'B':'A',seq[i]);
      var won=s.won['0'];
      s=game.move(s,'B',5*9+0);              // sends A to board 0, which is decided
      return [won, JSON.stringify(s.target)];})()""")
    assert r.toVariant() == [0, "[]"]


def test_ultimate2_has_three_levels(engine):
    load(engine, BUNDLED / "uuttt.js")
    r = engine.evaluate("""(function(){var s=game.setup(['A','B'],1);
      s=game.move(s,'A',4*81+7*9+2);  // top 4, mid 7, cell 2 -> small board (7,2)
      return [s.b.length, JSON.stringify(s.target)];})()""")
    assert r.toVariant() == [729, "[7,2]"]


def test_unlimited_adjacency_options_and_players(engine):
    load(engine, BUNDLED / "unlimited.js")
    r = engine.evaluate("""(function(){var s=game.setup(['A','B','C'],1,{k:3});
      var first=game.legalAt(s,5,5);
      s=game.move(s,'A',[0,0]);
      var far=game.legalAt(s,2,0), near=game.legalAt(s,1,1);
      s=game.move(s,'B',[1,0]); s=game.move(s,'C',[0,1]);
      s=game.move(s,'A',[-1,-1]); s=game.move(s,'B',[2,0]); s=game.move(s,'C',[0,2]);
      s=game.move(s,'A',[1,1]);   // A: (-1,-1),(0,0),(1,1) -> 3 in a row
      return [first, far, near, game.turn(s), game.status(s,{A:'A',B:'B',C:'C'}).over];})()""")
    assert r.toVariant() == [False, False, True, "-", True]


def test_chess4_opening_and_elimination(engine):
    load(engine, BUNDLED / "chess4.js")
    r = engine.evaluate("""(function(){var s=game.setup(['R','B','Y','G'],1);
      var pieces=s.b.filter(function(p){return p;}).length;
      return [pieces, game.moves(s,'R').length, game.turn(s)];})()""")
    # 4 x 16 pieces; red: 8 pawns x 2 + 2 knights x 2
    assert r.toVariant() == [64, 20, "R"]
    r = engine.evaluate("""(function(){var s=game.setup(['R','Y'],1);
      return [s.cols.join(''), s.b.filter(function(p){return p;}).length];})()""")
    assert r.toVariant() == ["ry", 32]


def test_game_options_are_validated():
    from muninn.gamehost import resolve_opts

    meta = parse_header((BUNDLED / "unlimited.js").read_text())
    assert resolve_opts(meta, None) == {"k": 4}
    assert resolve_opts(meta, {"k": "6"}) == {"k": 6}
    with pytest.raises(ValueError):
        resolve_opts(meta, {"k": 99})
    with pytest.raises(ValueError):
        resolve_opts(meta, {"zz": 1})


def _two(lab):
    from muninn.gamehost import GameRegistry
    from muninn.mesh import dm_conv
    from muninn.sim.lab import wait_until

    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    wait_until(lambda: b.node_id in a.mesh.connected(), 30)
    return a, b, GameRegistry(a), GameRegistry(b), dm_conv(b.node_id), wait_until


def test_scoreboard_counts_results_and_resignations(lab):
    a, b, ga, gb, conv, wait_until = _two(lab)
    for winner in ("a", "b", "resign-b", "draw"):
        sid = ga.invite(conv, "tictactoe")
        wait_until(lambda: gb.session_info(sid).get("invitedMe"), 15)
        gb.act(sid, "join", {})
        wait_until(lambda: ga.session_info(sid).get("started"), 15)
        if winner == "resign-b":
            gb.act(sid, "resign", {})
        else:
            w = {"a": [a.node_id], "b": [b.node_id], "draw": []}[winner]
            ga.act(sid, "result", {"winners": w, "text": "done"})
        wait_until(lambda: gb.session_info(sid).get("result") is not None, 15)
    board = ga.scores(conv)
    assert len(board) == 1 and board[0]["app"] == "tictactoe"
    assert board[0]["played"] == 4 and board[0]["draws"] == 1
    assert board[0]["wins"] == {a.node_id: 2, b.node_id: 1}
    assert gb.scores(None)[0]["wins"] == board[0]["wins"]  # same on both sides


def test_unsent_invite_disappears_for_everyone(lab):
    a, b, ga, gb, conv, wait_until = _two(lab)
    sid = ga.invite(conv, "chess")
    wait_until(lambda: any(s["session"] == sid for s in gb.sessions(None)), 15)
    with pytest.raises(ValueError):
        gb.withdraw(sid)  # only the inviter can
    ga.withdraw(sid)
    wait_until(lambda: not any(s["session"] == sid for s in gb.sessions(None)), 15)
    assert not any(s["session"] == sid for s in ga.sessions(None))
    gb.act(sid, "join", {})  # a late join can't revive it
    time.sleep(1)
    assert gb.session_info(sid)["withdrawn"] and not gb.session_info(sid)["started"]
