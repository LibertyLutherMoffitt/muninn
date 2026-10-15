// @muninn-game {"id": "checkers", "name": "Checkers", "icon": "⛀", "min": 2, "max": 2}
// American checkers / English draughts: captures are mandatory, a capturing
// piece must keep jumping, men become kings on the far row (which ends the
// turn). A move is the list of squares visited: [from, to, to2, ...].
// Board index = row*8 + col; row 0 is the first player's back row.
var game = {
  aspect: 1,

  setup: function (players, seed) {
    var b = [];
    for (var i = 0; i < 64; i++) {
      var r = Math.floor(i / 8), c = i % 8, dark = (r + c) % 2 === 1;
      b.push(dark && r < 3 ? "a" : dark && r > 4 ? "b" : null);
    }
    return { b: b, p: players, side: "a", quiet: 0, last: null };
  },

  dirs: function (piece) {
    if (piece === "a") return [[1, -1], [1, 1]];
    if (piece === "b") return [[-1, -1], [-1, 1]];
    return [[1, -1], [1, 1], [-1, -1], [-1, 1]];
  },
  owner: function (p) { return p ? p.toLowerCase() : null; },

  jumps: function (b, i, piece, path, out) {
    var r = Math.floor(i / 8), c = i % 8, found = false, ds = game.dirs(piece);
    for (var k = 0; k < ds.length; k++) {
      var mr = r + ds[k][0], mc = c + ds[k][1], tr = r + 2 * ds[k][0], tc = c + 2 * ds[k][1];
      if (tr < 0 || tr > 7 || tc < 0 || tc > 7) continue;
      var mid = mr * 8 + mc, to = tr * 8 + tc;
      if (b[to] || !b[mid] || game.owner(b[mid]) === game.owner(piece)) continue;
      found = true;
      var nb = b.slice(); nb[mid] = null; nb[i] = null;
      var crowned = piece === "a" && tr === 7 || piece === "b" && tr === 0;
      var np = crowned ? piece.toUpperCase() : piece;
      nb[to] = np;
      if (crowned) out.push(path.concat([to]));
      else game.jumps(nb, to, np, path.concat([to]), out);
    }
    if (!found && path.length > 1) out.push(path);
  },

  legal: function (s) {
    var caps = [], steps = [];
    for (var i = 0; i < 64; i++) {
      var p = s.b[i];
      if (!p || game.owner(p) !== s.side) continue;
      var before = caps.length;
      game.jumps(s.b, i, p, [i], caps);
      if (caps.length > before) continue;
      var r = Math.floor(i / 8), c = i % 8, ds = game.dirs(p);
      for (var k = 0; k < ds.length; k++) {
        var tr = r + ds[k][0], tc = c + ds[k][1];
        if (tr >= 0 && tr < 8 && tc >= 0 && tc < 8 && !s.b[tr * 8 + tc]) steps.push([i, tr * 8 + tc]);
      }
    }
    return caps.length ? caps : steps;
  },

  same: function (a, b) {
    if (!Array.isArray(a) || a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (Number(a[i]) !== b[i]) return false;
    return true;
  },

  move: function (s, player, m) {
    var legal = game.legal(s), ok = null;
    for (var k = 0; k < legal.length; k++) if (game.same(m, legal[k])) ok = legal[k];
    if (!ok) throw "illegal move";
    var b = s.b.slice(), piece = b[ok[0]], captured = false;
    b[ok[0]] = null;
    for (var j = 1; j < ok.length; j++) {
      if (Math.abs(ok[j] - ok[j - 1]) > 9) { b[(ok[j] + ok[j - 1]) / 2] = null; captured = true; }
    }
    var end = ok[ok.length - 1], row = Math.floor(end / 8);
    if (piece === "a" && row === 7 || piece === "b" && row === 0) piece = piece.toUpperCase();
    b[end] = piece;
    return { b: b, p: s.p, side: s.side === "a" ? "b" : "a", quiet: captured ? 0 : s.quiet + 1, last: ok };
  },

  turn: function (s) { return game.status(s, {}).over ? "-" : s.p[s.side === "a" ? 0 : 1]; },
  moves: function (s) { return game.legal(s); },

  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var mover = s.p[s.side === "a" ? 0 : 1], other = s.p[s.side === "a" ? 1 : 0];
    if (!game.legal(s).length) return { over: true, winners: [other], text: nm(other) + " wins!" };
    if (s.quiet >= 80) return { over: true, winners: [], text: "Draw (40 moves without a capture)" };
    var must = game.legal(s)[0].length > 2 || Math.abs(game.legal(s)[0][1] - game.legal(s)[0][0]) > 9;
    return { over: false, winners: [], text: nm(mover) + " to move (" + (s.side === "a" ? "red" : "black") + ")" +
             (must ? " — must capture" : "") };
  },

  mine: function (s, v) { return v.me === s.p[0] ? "a" : v.me === s.p[1] ? "b" : null; },
  scr: function (s, v, i) {
    var flip = game.mine(s, v) === "a";  // own pieces at the bottom
    var r = Math.floor(i / 8), c = i % 8;
    return flip ? [c, 7 - r] : [7 - c, r];
  },

  render: function (ctx, s, v) {
    var c = v.w / 8, path = v.ui.path || [];
    var reach = {};
    game.legal(s).forEach(function (m) {
      if (game.same(m.slice(0, path.length), path) && m.length > path.length && path.length) reach[m[path.length]] = 1;
    });
    for (var i = 0; i < 64; i++) {
      var xy = game.scr(s, v, i), dark = (Math.floor(i / 8) + i % 8) % 2 === 1;
      ctx.fillStyle = dark ? "#3d6b4a" : "#e8e1c9";
      if (s.last && s.last.indexOf(i) >= 0) ctx.fillStyle = "#6b8e4e";
      if (path.indexOf(i) >= 0) ctx.fillStyle = "#4f74b8";
      ctx.fillRect(xy[0] * c, xy[1] * c, c, c);
      if (reach[i]) {
        ctx.fillStyle = "rgba(255,255,255,0.35)";
        ctx.beginPath(); ctx.arc(xy[0] * c + c / 2, xy[1] * c + c / 2, c * 0.15, 0, Math.PI * 2); ctx.fill();
      }
      var p = s.b[i];
      if (!p) continue;
      var cx = xy[0] * c + c / 2, cy = xy[1] * c + c / 2;
      ctx.fillStyle = game.owner(p) === "a" ? "#c8352e" : "#1e1e24";
      ctx.strokeStyle = game.owner(p) === "a" ? "#ffb3a8" : "#9aa0b4";
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, c * 0.38, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      if (p === "A" || p === "B") {
        ctx.fillStyle = "#f5c542"; ctx.font = "bold " + Math.floor(c * 0.45) + "px sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("♛", cx, cy + 1);
      }
    }
  },

  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var c = v.w / 8, x = Math.floor(ev.x / c), y = Math.floor(ev.y / c);
    if (x < 0 || x > 7 || y < 0 || y > 7) return null;
    var flip = game.mine(s, v) === "a";
    var i = flip ? (7 - y) * 8 + x : y * 8 + (7 - x);
    var path = (v.ui.path || []).concat([i]);
    var legal = game.legal(s), prefix = false;
    for (var k = 0; k < legal.length; k++) {
      if (game.same(legal[k], path)) return { move: path, ui: { path: [] } };
      if (game.same(legal[k].slice(0, path.length), path)) prefix = true;
    }
    if (prefix) return { ui: { path: path } };
    // start a new selection from this square if it's a valid start
    for (k = 0; k < legal.length; k++) if (legal[k][0] === i) return { ui: { path: [i] } };
    return { ui: { path: [] } };
  }
};
