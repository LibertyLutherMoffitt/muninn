// @muninn-game {"id": "chess4", "name": "Four-player chess", "icon": "♛", "min": 2, "max": 4}
// Free-for-all on the 14x14 cross board, turns go Red, Blue, Yellow, Green
// (with 2 players: Red vs Yellow, face to face; with 3: Red, Blue, Yellow).
// A checkmated or stalemated player is out; their pieces turn grey, block
// and can be captured. A king left in check can be captured by a later player.
// Last player standing wins. Pawns promote to a queen on the middle rank.
// No castling or en passant. Each player sees the board from their own side.
// Squares: index = y*14 + x, (0,0) = Red's bottom-left. Moves: [from, to].
var game = {
  aspect: 1,
  N: 14,
  ORDER: ["r", "b", "y", "g"],
  FWD: { r: [0, 1], b: [1, 0], y: [0, -1], g: [-1, 0] },
  NAMES: { r: "Red", b: "Blue", y: "Yellow", g: "Green" },
  COLOR: { r: "#ef4444", b: "#3b82f6", y: "#eab308", g: "#22c55e", x: "#6b7280" },
  GLYPH: { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" },
  KN: [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1], [-1, -2], [-2, -1]],
  ORTHO: [[1, 0], [-1, 0], [0, 1], [0, -1]],
  DIAG: [[1, 1], [1, -1], [-1, 1], [-1, -1]],
  MAX_PLY: 1200,

  ok: function (x, y) {
    return x >= 0 && y >= 0 && x < 14 && y < 14 && !((x < 3 || x > 10) && (y < 3 || y > 10));
  },
  at: function (x, y) { return y * 14 + x; },

  setup: function (players, seed) {
    var n = players.length;
    var cols = n === 2 ? ["r", "y"] : n === 3 ? ["r", "b", "y"] : ["r", "b", "y", "g"];
    var b = [];
    for (var i = 0; i < 196; i++) b.push(null);
    var back1 = ["R", "N", "B", "Q", "K", "B", "N", "R"], back2 = ["R", "N", "B", "K", "Q", "B", "N", "R"];
    var place = { r: function (k, p) { b[game.at(3 + k, 0)] = "r" + back1[k]; b[game.at(3 + k, 1)] = "rP"; },
                  y: function (k, p) { b[game.at(3 + k, 13)] = "y" + back2[k]; b[game.at(3 + k, 12)] = "yP"; },
                  b: function (k, p) { b[game.at(0, 3 + k)] = "b" + back1[k]; b[game.at(1, 3 + k)] = "bP"; },
                  g: function (k, p) { b[game.at(13, 3 + k)] = "g" + back2[k]; b[game.at(12, 3 + k)] = "gP"; } };
    for (var c = 0; c < cols.length; c++) for (var k = 0; k < 8; k++) place[cols[c]](k);
    var alive = [];
    for (var j = 0; j < n; j++) alive.push(true);
    return { b: b, p: players, cols: cols, alive: alive, cur: 0, ply: 0, last: null, out: [] };
  },

  colorOf: function (s, i) { return s.cols[i]; },
  active: function (s) {
    var a = [];
    for (var i = 0; i < s.p.length; i++) if (s.alive[i]) a.push(i);
    return a;
  },
  over: function (s) { return game.active(s).length <= 1 || s.ply >= game.MAX_PLY; },
  turn: function (s) { return game.over(s) ? "-" : s.p[s.cur]; },

  // is (x,y) attacked by a piece of colour c?
  attacked: function (b, x, y, c) {
    var i, d, xx, yy, p;
    for (i = 0; i < 8; i++) {
      xx = x + game.KN[i][0]; yy = y + game.KN[i][1];
      if (game.ok(xx, yy) && b[game.at(xx, yy)] === c + "N") return true;
    }
    var rays = game.ORTHO.concat(game.DIAG);
    for (d = 0; d < 8; d++) {
      var diag = d >= 4;
      xx = x; yy = y;
      for (var n = 1; ; n++) {
        xx += rays[d][0]; yy += rays[d][1];
        if (!game.ok(xx, yy)) break;
        p = b[game.at(xx, yy)];
        if (!p) continue;
        if (p[0] === c && (p[1] === "Q" || p[1] === (diag ? "B" : "R") || (n === 1 && p[1] === "K"))) return true;
        break;
      }
    }
    var f = game.FWD[c];
    for (var sgn = -1; sgn <= 1; sgn += 2) {
      // a pawn of c standing at (x,y) - fwd - side attacks (x,y)
      xx = x - f[0] + (f[0] === 0 ? sgn : 0);
      yy = y - f[1] + (f[1] === 0 ? sgn : 0);
      if (game.ok(xx, yy) && b[game.at(xx, yy)] === c + "P") return true;
    }
    return false;
  },
  kingOf: function (b, c) {
    for (var i = 0; i < 196; i++) if (b[i] === c + "K") return i;
    return -1;
  },
  inCheck: function (s, b, idx) {
    var c = s.cols[idx], k = game.kingOf(b, c);
    if (k < 0) return true;
    for (var j = 0; j < s.p.length; j++)
      if (j !== idx && s.alive[j] && game.attacked(b, k % 14, Math.floor(k / 14), s.cols[j])) return true;
    return false;
  },
  promoteAt: function (c, x, y) {
    return (c === "r" && y === 7) || (c === "y" && y === 6) || (c === "b" && x === 7) || (c === "g" && x === 6);
  },
  pseudo: function (b, from, c) {
    var out = [], x = from % 14, y = Math.floor(from / 14), t = b[from][1], d, xx, yy, q, n;
    function add(xx, yy, quiet, capt) {
      if (!game.ok(xx, yy)) return false;
      var q = b[game.at(xx, yy)];
      if (q === null) { if (quiet) out.push([from, game.at(xx, yy)]); return true; }
      if (capt && q[0] !== c) out.push([from, game.at(xx, yy)]);
      return false;
    }
    if (t === "P") {
      var f = game.FWD[c];
      var start = (c === "r" && y === 1) || (c === "y" && y === 12) || (c === "b" && x === 1) || (c === "g" && x === 12);
      if (add(x + f[0], y + f[1], true, false) && start) add(x + 2 * f[0], y + 2 * f[1], true, false);
      for (var sgn = -1; sgn <= 1; sgn += 2) {
        xx = x + f[0] + (f[0] === 0 ? sgn : 0); yy = y + f[1] + (f[1] === 0 ? sgn : 0);
        if (game.ok(xx, yy)) { q = b[game.at(xx, yy)]; if (q && q[0] !== c) out.push([from, game.at(xx, yy)]); }
      }
    } else if (t === "N" || t === "K") {
      var offs = t === "N" ? game.KN : game.ORTHO.concat(game.DIAG);
      for (d = 0; d < offs.length; d++) add(x + offs[d][0], y + offs[d][1], true, true);
    } else {
      var rays = t === "R" ? game.ORTHO : t === "B" ? game.DIAG : game.ORTHO.concat(game.DIAG);
      for (d = 0; d < rays.length; d++)
        for (n = 1; ; n++) if (!add(x + n * rays[d][0], y + n * rays[d][1], true, true)) break;
    }
    return out;
  },
  apply: function (b, m) {
    var nb = b.slice(), pc = nb[m[0]];
    nb[m[1]] = pc; nb[m[0]] = null;
    var x = m[1] % 14, y = Math.floor(m[1] / 14);
    if (pc[1] === "P" && game.promoteAt(pc[0], x, y)) nb[m[1]] = pc[0] + "Q";
    return nb;
  },
  legalFor: function (s, idx) {
    var c = s.cols[idx], out = [];
    for (var i = 0; i < 196; i++) {
      if (!s.b[i] || s.b[i][0] !== c) continue;
      var ps = game.pseudo(s.b, i, c);
      for (var j = 0; j < ps.length; j++)
        if (!game.inCheck(s, game.apply(s.b, ps[j]), idx)) out.push(ps[j]);
    }
    return out;
  },
  moves: function (s, player) {
    if (game.over(s) || s.p[s.cur] !== player) return [];
    return game.legalFor(s, s.cur);
  },
  eliminate: function (n, idx, why) {
    n.alive[idx] = false;
    n.out.push([idx, why]);
    var c = n.cols[idx];
    for (var i = 0; i < 196; i++) if (n.b[i] && n.b[i][0] === c) n.b[i] = n.b[i][1] === "K" ? null : "x" + n.b[i][1];
  },
  move: function (s, player, m) {
    if (!m || m.length !== 2 || typeof m[0] !== "number" || typeof m[1] !== "number") throw "bad move";
    if (game.over(s)) throw "game over";
    if (s.p[s.cur] !== player) throw "not your turn";
    var legal = game.legalFor(s, s.cur), found = false;
    for (var i = 0; i < legal.length; i++) if (legal[i][0] === m[0] && legal[i][1] === m[1]) found = true;
    if (!found) throw "illegal move";
    var n = { b: game.apply(s.b, m), p: s.p, cols: s.cols, alive: s.alive.slice(), cur: s.cur,
              ply: s.ply + 1, last: [m[0], m[1]], out: s.out.slice() };
    // a king that was captured
    for (var j = 0; j < n.p.length; j++)
      if (n.alive[j] && game.kingOf(n.b, n.cols[j]) < 0) game.eliminate(n, j, "king captured");
    // next player who can move; players who can't are mated / stalemated
    var nx = n.cur;
    for (var tries = 0; tries < n.p.length && game.active(n).length > 1; tries++) {
      do { nx = (nx + 1) % n.p.length; } while (!n.alive[nx]);
      if (game.legalFor(n, nx).length) break;
      game.eliminate(n, nx, game.inCheck(n, n.b, nx) ? "checkmated" : "stalemated");
    }
    n.cur = nx;
    return n;
  },
  status: function (s, names) {
    var nm = function (i) { return (names[s.p[i]] || "?") + " (" + game.NAMES[s.cols[i]] + ")"; };
    var a = game.active(s);
    var outs = s.out.map(function (o) { return nm(o[0]) + " " + o[1]; }).join(" · ");
    if (a.length === 1) return { over: true, winners: [s.p[a[0]]], text: nm(a[0]) + " wins!" + (outs ? " — " + outs : "") };
    if (s.ply >= game.MAX_PLY) return { over: true, winners: [], text: "Draw (move limit)" };
    var check = game.inCheck(s, s.b, s.cur) ? " — check!" : "";
    return { over: false, winners: [], text: nm(s.cur) + " to move" + check + (outs ? " · " + outs : "") };
  },

  // --- view: rotate so the viewer's colour sits at the bottom ----------------
  rotFor: function (s, v) {
    var i = s.p.indexOf(v.me);
    return i < 0 ? 0 : { r: 0, b: 1, y: 2, g: 3 }[s.cols[i]];
  },
  toScreen: function (x, y, rot) {
    var X = x, Y = y;
    if (rot === 1) { X = 13 - y; Y = x; }
    else if (rot === 2) { X = 13 - x; Y = 13 - y; }
    else if (rot === 3) { X = y; Y = 13 - x; }
    return [X, 13 - Y];
  },
  fromScreen: function (sx, row, rot) {
    var X = sx, Y = 13 - row;
    if (rot === 1) return [Y, 13 - X];
    if (rot === 2) return [13 - X, 13 - Y];
    if (rot === 3) return [13 - Y, X];
    return [X, Y];
  },
  render: function (ctx, s, v) {
    var c = v.w / 14, rot = game.rotFor(s, v), sel = v.ui && v.ui.sel;
    var targets = {};
    if (sel !== undefined && sel !== null && v.myTurn) {
      var lm = game.legalFor(s, s.cur);
      for (var t = 0; t < lm.length; t++) if (lm[t][0] === sel) targets[lm[t][1]] = 1;
    }
    var checked = {};
    for (var pi = 0; pi < s.p.length; pi++)
      if (s.alive[pi] && game.inCheck(s, s.b, pi)) checked[game.kingOf(s.b, s.cols[pi])] = 1;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = Math.floor(c * 0.8) + "px sans-serif";
    for (var y = 0; y < 14; y++)
      for (var x = 0; x < 14; x++) {
        if (!game.ok(x, y)) continue;
        var i = game.at(x, y), sc = game.toScreen(x, y, rot), px = sc[0] * c, py = sc[1] * c;
        ctx.fillStyle = (x + y) % 2 ? "#3a3f4b" : "#596070";
        if (s.last && (s.last[0] === i || s.last[1] === i)) ctx.fillStyle = "#6d5a2a";
        if (i === sel) ctx.fillStyle = "#7c3aed";
        if (checked[i]) ctx.fillStyle = "#b91c1c";
        ctx.fillRect(px, py, c, c);
        var p = s.b[i];
        if (p) { ctx.fillStyle = game.COLOR[p[0]]; ctx.fillText(game.GLYPH[p[1]], px + c / 2, py + c * 0.55); }
        if (targets[i]) {
          ctx.fillStyle = "rgba(167,139,250,0.75)";
          ctx.beginPath(); ctx.arc(px + c / 2, py + c / 2, p ? c * 0.42 : c * 0.16, 0, Math.PI * 2);
          if (p) { ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 3; ctx.stroke(); } else ctx.fill();
        }
      }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click" || game.over(s)) return null;
    var c = v.w / 14, rot = game.rotFor(s, v);
    var xy = game.fromScreen(Math.floor(ev.x / c), Math.floor(ev.y / c), rot);
    if (!game.ok(xy[0], xy[1])) return { ui: { sel: null } };
    var i = game.at(xy[0], xy[1]), sel = v.ui ? v.ui.sel : null, me = s.p.indexOf(v.me);
    if (sel !== null && sel !== undefined && v.myTurn) {
      var lm = game.legalFor(s, s.cur);
      for (var t = 0; t < lm.length; t++) if (lm[t][0] === sel && lm[t][1] === i) return { move: [sel, i] };
    }
    var p = s.b[i];
    if (p && me >= 0 && p[0] === s.cols[me]) return { ui: { sel: i } };
    return { ui: { sel: null } };
  }
};
