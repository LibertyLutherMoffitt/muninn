// @muninn-game {"id": "uttt", "name": "Ultimate tic-tac-toe", "icon": "#️⃣", "min": 2, "max": 2}
// Tic-tac-toe made of tic-tac-toe boards. Where you play inside a small board
// sends your opponent to the matching board; if that board is already decided
// they may play anywhere. Win three small boards in a row to win.
//
// Generic engine (shared with uuttt.js — only LEVELS differs): the board is
// LEVELS nested 3x3 levels, 9^LEVELS cells. A cell index written in base 9 is
// its path from the top board down (digit 0 = top level). After a move the
// opponent is sent to the deepest board named by the move's path *without its
// first digit*; if that board (or an enclosing one) is decided they go up a
// level, and at the top they may play anywhere.
var game = {
  LEVELS: 2,
  aspect: 1,
  LINES: [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]],

  size: function () { return Math.pow(9, game.LEVELS); },
  side: function () { return Math.pow(3, game.LEVELS); },
  digits: function (i) {
    var d = [];
    for (var k = 0; k < game.LEVELS; k++) { d.unshift(i % 9); i = Math.floor(i / 9); }
    return d;
  },
  key: function (prefix) { return prefix.join(""); },

  setup: function (players, seed) {
    var b = [];
    for (var i = 0; i < game.size(); i++) b.push(null);
    return { b: b, won: {}, p: players, t: 0, target: [], last: -1 };
  },

  // owner of a node: a cell (full path) or a board (shorter prefix):
  // 0 / 1 = won by that player, "d" = drawn/full, null/undefined = open.
  owner: function (s, prefix) {
    if (prefix.length === game.LEVELS) {
      var i = 0;
      for (var k = 0; k < prefix.length; k++) i = i * 9 + prefix[k];
      return s.b[i];
    }
    var w = s.won[game.key(prefix)];
    return w === undefined ? null : w;
  },
  closed: function (s, prefix) {
    // decided itself, or inside a decided board
    for (var k = 0; k <= prefix.length && k < game.LEVELS; k++)
      if (game.owner(s, prefix.slice(0, k)) !== null) return true;
    return false;
  },
  judge: function (s, prefix) {
    var cells = [], full = true;
    for (var j = 0; j < 9; j++) {
      var o = game.owner(s, prefix.concat([j]));
      cells.push(o);
      if (o === null) full = false;
    }
    for (var l = 0; l < 8; l++) {
      var L = game.LINES[l], a = cells[L[0]];
      if ((a === 0 || a === 1) && a === cells[L[1]] && a === cells[L[2]]) return a;
    }
    return full ? "d" : null;
  },
  over: function (s) { return game.owner(s, []) !== null; },

  turn: function (s) { return game.over(s) ? "-" : s.p[s.t % 2]; },

  legalAt: function (s, i) {
    if (s.b[i] !== null) return false;
    var d = game.digits(i);
    for (var k = 0; k < s.target.length; k++) if (d[k] !== s.target[k]) return false;
    return !game.closed(s, d.slice(0, game.LEVELS - 1));
  },
  moves: function (s, player) {
    if (game.over(s)) return [];
    var out = [];
    for (var i = 0; i < game.size(); i++) if (game.legalAt(s, i)) out.push(i);
    return out;
  },
  move: function (s, player, m) {
    if (typeof m !== "number" || m !== Math.floor(m) || m < 0 || m >= game.size()) throw "bad cell";
    if (game.over(s)) throw "game over";
    if (!game.legalAt(s, m)) throw "not playable there";
    var me = s.t % 2, b = s.b.slice(), won = {};
    for (var k in s.won) won[k] = s.won[k];
    b[m] = me;
    var n = { b: b, won: won, p: s.p, t: s.t + 1, target: [], last: m };
    var d = game.digits(m);
    for (var depth = game.LEVELS - 1; depth >= 0; depth--) {
      var prefix = d.slice(0, depth);
      var r = game.judge(n, prefix);
      if (r === null) break;
      won[game.key(prefix)] = r;
    }
    var target = d.slice(1);
    while (target.length && game.closed(n, target)) target.pop();
    n.target = target;
    return n;
  },
  status: function (s, names) {
    var w = game.owner(s, []);
    var mark = function (i) { return i ? "O" : "X"; };
    if (w === 0 || w === 1) return { over: true, winners: [s.p[w]], text: (names[s.p[w]] || "?") + " wins!" };
    if (w === "d") return { over: true, winners: [], text: "Draw" };
    var where = s.target.length ? "the highlighted board" : "any open board";
    return { over: false, winners: [],
             text: (names[s.p[s.t % 2]] || "?") + " (" + mark(s.t % 2) + ") plays in " + where };
  },

  // --- drawing --------------------------------------------------------------
  cellOf: function (row, col) {
    var i = 0;
    for (var k = game.LEVELS - 1; k >= 0; k--) {
      var span = Math.pow(3, k);
      i = i * 9 + (Math.floor(row / span) % 3) * 3 + (Math.floor(col / span) % 3);
    }
    return i;
  },
  rectOf: function (prefix, c) {
    // top-left (in cells) and size of the board/cell at `prefix`
    var r = 0, col = 0, span = game.side();
    for (var k = 0; k < prefix.length; k++) {
      span /= 3;
      r += Math.floor(prefix[k] / 3) * span;
      col += (prefix[k] % 3) * span;
    }
    return { x: col * c, y: r * c, s: span * c };
  },
  drawMark: function (ctx, who, x, y, size, alpha) {
    var r = size * 0.32, cx = x + size / 2, cy = y + size / 2;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.lineWidth = Math.max(1.5, size * 0.09);
    ctx.lineCap = "round";
    if (who === 0) {
      ctx.strokeStyle = "#a78bfa";
      ctx.beginPath(); ctx.moveTo(cx - r, cy - r); ctx.lineTo(cx + r, cy + r); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(cx + r, cy - r); ctx.lineTo(cx - r, cy + r); ctx.stroke();
    } else if (who === 1) {
      ctx.strokeStyle = "#34d399";
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.restore();
  },
  render: function (ctx, s, v) {
    var n = game.side(), c = v.w / n;
    // playable region
    if (!game.over(s)) {
      var t = game.rectOf(s.target, c);
      ctx.fillStyle = v.myTurn ? "#2a1f4a" : "#1f2330";
      ctx.fillRect(t.x, t.y, t.s, t.s);
    }
    // last move
    if (s.last >= 0) {
      var ld = game.digits(s.last), lr = game.rectOf(ld, c);
      ctx.fillStyle = "#3b2a6a"; ctx.fillRect(lr.x, lr.y, lr.s, lr.s);
    }
    // grid: thicker lines for higher levels
    for (var lvl = 0; lvl < game.LEVELS; lvl++) {
      var span = Math.pow(3, lvl);
      ctx.strokeStyle = lvl === game.LEVELS - 1 ? "#e5e7eb" : lvl === 0 ? "#4b5563" : "#9ca3af";
      ctx.lineWidth = lvl === 0 ? 1 : 2 + lvl * 1.5;
      for (var g = span; g < n; g += span) {
        if (lvl < game.LEVELS - 1 && g % (span * 3) === 0) continue;
        ctx.beginPath(); ctx.moveTo(g * c, 0); ctx.lineTo(g * c, v.h); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, g * c); ctx.lineTo(v.w, g * c); ctx.stroke();
      }
    }
    // cells
    for (var i = 0; i < s.b.length; i++) {
      if (s.b[i] === null) continue;
      var rr = game.rectOf(game.digits(i), c);
      var dead = game.closed(s, game.digits(i).slice(0, game.LEVELS - 1));
      game.drawMark(ctx, s.b[i], rr.x, rr.y, rr.s, dead ? 0.35 : 1);
    }
    // decided boards, deepest first, as big translucent marks
    var keys = Object.keys(s.won).sort(function (a, b) { return b.length - a.length; });
    for (var q = 0; q < keys.length; q++) {
      var pre = keys[q].split("").map(Number), w = s.won[keys[q]];
      var br = game.rectOf(pre, c);
      if (w === "d") { ctx.fillStyle = "rgba(15,17,21,0.45)"; ctx.fillRect(br.x, br.y, br.s, br.s); }
      else game.drawMark(ctx, w, br.x, br.y, br.s, pre.length === 0 ? 0.9 : 0.75);
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var n = game.side(), c = v.w / n;
    var col = Math.floor(ev.x / c), row = Math.floor(ev.y / c);
    if (col < 0 || row < 0 || col >= n || row >= n) return null;
    var i = game.cellOf(row, col);
    return game.legalAt(s, i) ? { move: i } : null;
  }
};
