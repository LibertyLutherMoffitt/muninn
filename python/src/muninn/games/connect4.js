// @muninn-game {"id": "connect4", "name": "Connect Four", "icon": "🔴", "min": 2, "max": 2}
// Drop discs into 7 columns; four in a row wins. A move is a column 0..6.
var game = {
  aspect: 7 / 6,
  setup: function (players, seed) {
    var g = []; for (var i = 0; i < 42; i++) g.push(null);
    return { g: g, p: players, t: 0, last: -1 };
  },
  at: function (s, r, c) { return r < 0 || r > 5 || c < 0 || c > 6 ? undefined : s.g[r * 7 + c]; },
  winner: function (s) {
    var D = [[0, 1], [1, 0], [1, 1], [1, -1]];
    for (var r = 0; r < 6; r++) for (var c = 0; c < 7; c++) {
      var v = s.g[r * 7 + c]; if (v === null) continue;
      for (var k = 0; k < 4; k++) {
        var n = 1;
        while (n < 4 && game.at(s, r + D[k][0] * n, c + D[k][1] * n) === v) n++;
        if (n === 4) return v;
      }
    }
    return null;
  },
  moves: function (s) {
    var out = []; for (var c = 0; c < 7; c++) if (s.g[35 + c] === null) out.push(c); return out;
  },
  turn: function (s) { return game.winner(s) !== null || s.t >= 42 ? "-" : s.p[s.t % 2]; },
  move: function (s, player, m) {
    if (typeof m !== "number" || m < 0 || m > 6 || m !== Math.floor(m)) throw "bad column";
    for (var r = 0; r < 6; r++) {
      if (s.g[r * 7 + m] === null) {
        var g = s.g.slice(); g[r * 7 + m] = s.t % 2;
        return { g: g, p: s.p, t: s.t + 1, last: r * 7 + m };
      }
    }
    throw "column full";
  },
  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var w = game.winner(s);
    if (w !== null) return { over: true, winners: [s.p[w]], text: nm(s.p[w]) + " wins!" };
    if (s.t >= 42) return { over: true, winners: [], text: "Draw" };
    return { over: false, winners: [], text: nm(s.p[s.t % 2]) + " to play (" + (s.t % 2 ? "yellow" : "red") + ")" };
  },
  render: function (ctx, s, v) {
    var c = v.w / 7;
    ctx.fillStyle = "#1d4fb8"; ctx.fillRect(0, 0, v.w, v.h);
    for (var r = 0; r < 6; r++) for (var col = 0; col < 7; col++) {
      var x = col * c + c / 2, y = (5 - r) * c + c / 2, val = s.g[r * 7 + col];
      ctx.fillStyle = val === 0 ? "#e8443a" : val === 1 ? "#f5c542" : "#0f1115";
      ctx.beginPath(); ctx.arc(x, y, c * 0.4, 0, Math.PI * 2); ctx.fill();
      if (r * 7 + col === s.last) {
        ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.arc(x, y, c * 0.4, 0, Math.PI * 2); ctx.stroke();
      }
    }
  },
  input: function (s, v, ev) {
    if (ev.type === "click") {
      var col = Math.floor(ev.x / (v.w / 7));
      return col >= 0 && col < 7 ? { move: col } : null;
    }
    if (ev.type === "key" && ev.down && ev.key >= "1" && ev.key <= "7") return { move: Number(ev.key) - 1 };
    return null;
  }
};
