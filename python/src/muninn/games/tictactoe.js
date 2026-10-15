// @muninn-game {"id": "tictactoe", "name": "Tic-tac-toe", "icon": "⭕", "min": 2, "max": 2}
var game = {
  aspect: 1,
  LINES: [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]],

  setup: function (players, seed) {
    return { b: [null,null,null,null,null,null,null,null,null], p: players, t: 0 };
  },
  winner: function (s) {
    for (var i = 0; i < 8; i++) {
      var l = game.LINES[i];
      if (s.b[l[0]] !== null && s.b[l[0]] === s.b[l[1]] && s.b[l[1]] === s.b[l[2]]) return s.b[l[0]];
    }
    return null;
  },
  turn: function (s) {
    if (game.winner(s) !== null || s.t >= 9) return "-";
    return s.p[s.t % 2];
  },
  moves: function (s, player) {
    var out = [];
    for (var i = 0; i < 9; i++) if (s.b[i] === null) out.push(i);
    return out;
  },
  move: function (s, player, m) {
    if (typeof m !== "number" || m < 0 || m > 8 || m !== Math.floor(m)) throw "bad cell";
    if (s.b[m] !== null) throw "occupied";
    var b = s.b.slice();
    b[m] = s.t % 2;
    return { b: b, p: s.p, t: s.t + 1 };
  },
  status: function (s, names) {
    var w = game.winner(s);
    if (w !== null) return { over: true, winners: [s.p[w]], text: (names[s.p[w]] || "?") + " wins!" };
    if (s.t >= 9) return { over: true, winners: [], text: "Draw" };
    return { over: false, winners: [], text: (names[s.p[s.t % 2]] || "?") + " to play (" + (s.t % 2 ? "O" : "X") + ")" };
  },
  render: function (ctx, s, v) {
    var c = v.w / 3;
    ctx.strokeStyle = "#9ca3af"; ctx.lineWidth = 3;
    for (var i = 1; i < 3; i++) {
      ctx.beginPath(); ctx.moveTo(i * c, 10); ctx.lineTo(i * c, v.h - 10); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(10, i * c); ctx.lineTo(v.w - 10, i * c); ctx.stroke();
    }
    ctx.lineWidth = 8;
    for (var k = 0; k < 9; k++) {
      var x = (k % 3) * c + c / 2, y = Math.floor(k / 3) * c + c / 2, r = c * 0.3;
      if (s.b[k] === 0) {
        ctx.strokeStyle = "#7c3aed";
        ctx.beginPath(); ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r); ctx.stroke();
      } else if (s.b[k] === 1) {
        ctx.strokeStyle = "#10b981";
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.stroke();
      }
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var c = v.w / 3;
    var k = Math.floor(ev.y / c) * 3 + Math.floor(ev.x / c);
    return (k >= 0 && k < 9 && s.b[k] === null) ? { move: k } : null;
  }
};
