// @muninn-game {"id": "unlimited", "name": "Unlimited tic-tac-toe", "icon": "♾️", "min": 2, "max": 8, "options": [{"id": "k", "label": "In a row to win", "choices": [3, 4, 5, 6, 7], "default": 4}]}
// Connect-k on an endless grid, for any number of players. The first mark goes
// in the middle; after that every mark must touch an existing one (diagonals
// count). The board grows as you play — pinch / scroll to zoom.
// Moves are [x, y] integer coordinates.
var game = {
  aspect: 1,
  MARKS: ["✕", "◯", "△", "□", "◇", "★", "✚", "♥"],
  COLORS: ["#a78bfa", "#34d399", "#f59e0b", "#60a5fa", "#f472b6", "#facc15", "#f87171", "#22d3ee"],

  setup: function (players, seed, opts) {
    var k = opts && opts.k ? Math.floor(opts.k) : 4;
    if (!(k >= 3 && k <= 10)) k = 4;
    return { p: players, k: k, cells: {}, list: [], t: 0, win: null };
  },
  key: function (x, y) { return x + "," + y; },
  turn: function (s) { return s.win ? "-" : s.p[s.t % s.p.length]; },

  legalAt: function (s, x, y) {
    if (s.win || s.cells[game.key(x, y)] !== undefined) return false;
    if (!s.list.length) return x === 0 && y === 0;
    for (var dx = -1; dx <= 1; dx++)
      for (var dy = -1; dy <= 1; dy++)
        if ((dx || dy) && s.cells[game.key(x + dx, y + dy)] !== undefined) return true;
    return false;
  },
  moves: function (s, player) {
    if (s.win) return [];
    if (!s.list.length) return [[0, 0]];
    var seen = {}, out = [];
    for (var i = 0; i < s.list.length; i++)
      for (var dx = -1; dx <= 1; dx++)
        for (var dy = -1; dy <= 1; dy++) {
          var x = s.list[i][0] + dx, y = s.list[i][1] + dy, kk = game.key(x, y);
          if (!seen[kk] && game.legalAt(s, x, y)) { seen[kk] = 1; out.push([x, y]); }
        }
    return out;
  },
  line: function (s, x, y, who) {
    var dirs = [[1, 0], [0, 1], [1, 1], [1, -1]];
    for (var d = 0; d < 4; d++) {
      var run = [[x, y]];
      for (var sgn = -1; sgn <= 1; sgn += 2)
        for (var n = 1; n < s.k; n++) {
          var xx = x + sgn * n * dirs[d][0], yy = y + sgn * n * dirs[d][1];
          if (s.cells[game.key(xx, yy)] !== who) break;
          run.push([xx, yy]);
        }
      if (run.length >= s.k) return run;
    }
    return null;
  },
  move: function (s, player, m) {
    if (!m || m.length !== 2 || typeof m[0] !== "number" || typeof m[1] !== "number" ||
        m[0] !== Math.floor(m[0]) || m[1] !== Math.floor(m[1])) throw "bad move";
    if (!game.legalAt(s, m[0], m[1])) throw "must touch an existing mark";
    var who = s.t % s.p.length, cells = {};
    for (var c in s.cells) cells[c] = s.cells[c];
    cells[game.key(m[0], m[1])] = who;
    var n = { p: s.p, k: s.k, cells: cells, list: s.list.concat([[m[0], m[1]]]), t: s.t + 1, win: null };
    var run = game.line(n, m[0], m[1], who);
    if (run) n.win = { who: who, run: run };
    return n;
  },
  status: function (s, names) {
    var nm = function (i) { return (names[s.p[i]] || "?") + " " + game.MARKS[i]; };
    if (s.win) return { over: true, winners: [s.p[s.win.who]], text: nm(s.win.who) + " wins with " + s.k + " in a row!" };
    return { over: false, winners: [], text: nm(s.t % s.p.length) + " to play · " + s.k + " in a row wins" };
  },

  // The view always shows every mark plus a margin, so the grid grows.
  frame: function (s, v) {
    var x0 = 0, x1 = 0, y0 = 0, y1 = 0;
    for (var i = 0; i < s.list.length; i++) {
      var p = s.list[i];
      x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]);
      y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]);
    }
    var n = Math.max(7, x1 - x0 + 5, y1 - y0 + 5);
    var cx = Math.floor((x0 + x1) / 2), cy = Math.floor((y0 + y1) / 2);
    var ox = cx - Math.floor(n / 2), oy = cy - Math.floor(n / 2);
    return { ox: ox, oy: oy, n: n, c: v.w / n };
  },
  render: function (ctx, s, v) {
    var f = game.frame(s, v), c = f.c;
    var mine = v.myTurn && !s.win;
    for (var gx = 0; gx < f.n; gx++)
      for (var gy = 0; gy < f.n; gy++)
        if (mine && game.legalAt(s, f.ox + gx, f.oy + gy)) {
          ctx.fillStyle = "#2a1f4a"; ctx.fillRect(gx * c + 1, gy * c + 1, c - 2, c - 2);
        }
    ctx.strokeStyle = "#374151"; ctx.lineWidth = 1;
    for (var g = 0; g <= f.n; g++) {
      ctx.beginPath(); ctx.moveTo(g * c, 0); ctx.lineTo(g * c, v.h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, g * c); ctx.lineTo(v.w, g * c); ctx.stroke();
    }
    var last = s.list.length ? s.list[s.list.length - 1] : null;
    if (last) {
      ctx.fillStyle = "#3b2a6a";
      ctx.fillRect((last[0] - f.ox) * c + 1, (last[1] - f.oy) * c + 1, c - 2, c - 2);
    }
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "bold " + Math.floor(c * 0.62) + "px sans-serif";
    for (var key in s.cells) {
      var xy = key.split(","), who = s.cells[key];
      ctx.fillStyle = game.COLORS[who % 8];
      ctx.fillText(game.MARKS[who % 8], (Number(xy[0]) - f.ox + 0.5) * c, (Number(xy[1]) - f.oy + 0.55) * c);
    }
    if (s.win) {
      var r = s.win.run;
      r.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = Math.max(2, c * 0.12); ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo((r[0][0] - f.ox + 0.5) * c, (r[0][1] - f.oy + 0.5) * c);
      ctx.lineTo((r[r.length - 1][0] - f.ox + 0.5) * c, (r[r.length - 1][1] - f.oy + 0.5) * c);
      ctx.stroke();
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var f = game.frame(s, v);
    var x = f.ox + Math.floor(ev.x / f.c), y = f.oy + Math.floor(ev.y / f.c);
    return game.legalAt(s, x, y) ? { move: [x, y] } : null;
  }
};
