// @muninn-game {"id": "reversi", "name": "Reversi", "icon": "⚫", "min": 2, "max": 2}
// Othello rules: outflank to flip; pass is automatic when you can't move.
// A move is a square index 0..63 (row*8+col).
var game = {
  aspect: 1,
  D: [[0, 1], [1, 0], [0, -1], [-1, 0], [1, 1], [1, -1], [-1, 1], [-1, -1]],
  setup: function (players, seed) {
    var b = []; for (var i = 0; i < 64; i++) b.push(null);
    b[27] = 1; b[36] = 1; b[28] = 0; b[35] = 0;
    return { b: b, p: players, side: 0, last: -1 };
  },
  flips: function (b, i, side) {
    if (b[i] !== null) return [];
    var out = [], r = Math.floor(i / 8), c = i % 8;
    for (var k = 0; k < 8; k++) {
      var run = [], rr = r + game.D[k][0], cc = c + game.D[k][1];
      while (rr >= 0 && rr < 8 && cc >= 0 && cc < 8 && b[rr * 8 + cc] === 1 - side) {
        run.push(rr * 8 + cc); rr += game.D[k][0]; cc += game.D[k][1];
      }
      if (run.length && rr >= 0 && rr < 8 && cc >= 0 && cc < 8 && b[rr * 8 + cc] === side) out = out.concat(run);
    }
    return out;
  },
  movesFor: function (b, side) {
    var out = []; for (var i = 0; i < 64; i++) if (game.flips(b, i, side).length) out.push(i); return out;
  },
  moves: function (s) { return game.movesFor(s.b, s.side); },
  over: function (s) { return !game.movesFor(s.b, 0).length && !game.movesFor(s.b, 1).length; },
  turn: function (s) { return game.over(s) ? "-" : s.p[s.side]; },
  move: function (s, player, m) {
    var f = typeof m === "number" ? game.flips(s.b, m, s.side) : [];
    if (!f.length) throw "illegal";
    var b = s.b.slice(); b[m] = s.side; f.forEach(function (i) { b[i] = s.side; });
    var next = 1 - s.side;
    if (!game.movesFor(b, next).length) next = s.side;  // opponent must pass
    return { b: b, p: s.p, side: next, last: m };
  },
  count: function (b, side) { var n = 0; b.forEach(function (x) { if (x === side) n++; }); return n; },
  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var a = game.count(s.b, 0), z = game.count(s.b, 1);
    if (game.over(s)) {
      if (a === z) return { over: true, winners: [], text: "Draw " + a + "–" + z };
      var w = a > z ? 0 : 1;
      return { over: true, winners: [s.p[w]], text: nm(s.p[w]) + " wins " + Math.max(a, z) + "–" + Math.min(a, z) };
    }
    return { over: false, winners: [], text: nm(s.p[s.side]) + " to play · black " + a + " – white " + z };
  },
  render: function (ctx, s, v) {
    var c = v.w / 8, legal = {};
    game.moves(s).forEach(function (i) { legal[i] = 1; });
    ctx.fillStyle = "#1f7a4a"; ctx.fillRect(0, 0, v.w, v.h);
    ctx.strokeStyle = "#0e3d24"; ctx.lineWidth = 1.5;
    for (var k = 0; k <= 8; k++) {
      ctx.beginPath(); ctx.moveTo(k * c, 0); ctx.lineTo(k * c, v.h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, k * c); ctx.lineTo(v.w, k * c); ctx.stroke();
    }
    for (var i = 0; i < 64; i++) {
      var x = (i % 8) * c + c / 2, y = Math.floor(i / 8) * c + c / 2;
      if (s.b[i] !== null) {
        ctx.fillStyle = s.b[i] === 0 ? "#111" : "#f2f2f2";
        ctx.beginPath(); ctx.arc(x, y, c * 0.4, 0, Math.PI * 2); ctx.fill();
        if (i === s.last) { ctx.fillStyle = "#e8443a"; ctx.beginPath(); ctx.arc(x, y, c * 0.08, 0, Math.PI * 2); ctx.fill(); }
      } else if (legal[i] && v.myTurn) {
        ctx.fillStyle = "rgba(0,0,0,0.25)"; ctx.beginPath(); ctx.arc(x, y, c * 0.12, 0, Math.PI * 2); ctx.fill();
      }
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var c = v.w / 8, i = Math.floor(ev.y / c) * 8 + Math.floor(ev.x / c);
    return i >= 0 && i < 64 ? { move: i } : null;
  }
};
