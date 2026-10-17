// @muninn-game {"id": "checkers", "name": "Checkers", "icon": "⛀", "min": 2, "max": 2, "options": [{"id": "moves", "label": "Pieces move", "choices": ["straight (Turkish)", "diagonal"], "default": "straight (Turkish)"}, {"id": "size", "label": "Board", "choices": [8, 6], "default": 8}, {"id": "must", "label": "Capturing is mandatory", "choices": ["yes", "no"], "default": "yes"}, {"id": "most", "label": "Must capture the most pieces", "choices": ["yes", "no"], "default": "yes"}, {"id": "flying", "label": "Kings fly (any distance)", "choices": ["yes", "no"], "default": "yes"}, {"id": "back", "label": "Men capture backwards", "choices": ["no", "yes"], "default": "no"}]}
// Checkers with configurable rules; the defaults are Turkish draughts (Dama):
// men move one square forward or sideways, capture forward or sideways by
// jumping, captures are mandatory and you must take the most pieces, kings
// fly along ranks and files (and can't reverse direction mid-capture),
// captured pieces come off immediately, men are crowned on the last row at
// the end of the move. "diagonal" + no flying + no "most" ≈ American
// checkers (crowning ends the move there). 8×8 or 6×6.
// A move is the list of squares visited: [from, to, to2, ...]; index = row*N + col,
// row 0 is the first player's back row.
var game = {
  aspect: 1,

  rules: function (opts) {
    var o = opts || {};
    var n = o.size === 6 || o.size === "6" ? 6 : 8;
    return {
      n: n,
      orth: o.moves !== "diagonal",
      must: o.must !== "no",
      most: o.most !== "no",
      fly: o.flying !== "no",
      back: o.back === "yes",
    };
  },

  setup: function (players, seed, opts) {
    var R = game.rules(opts), n = R.n, b = [];
    for (var i = 0; i < n * n; i++) {
      var r = Math.floor(i / n), c = i % n, usable = R.orth || (r + c) % 2 === 1;
      var aRow, bRow;
      if (R.orth && n === 8) { aRow = r === 1 || r === 2; bRow = r === 5 || r === 6; }
      else { var depth = n === 8 ? 3 : 2; aRow = r < depth; bRow = r >= n - depth; }
      b.push(usable && aRow ? "a" : usable && bRow ? "b" : null);
    }
    return { b: b, p: players, side: "a", quiet: 0, last: null, R: R };
  },

  owner: function (p) { return p ? p.toLowerCase() : null; },
  king: function (p) { return p === "A" || p === "B"; },
  fwd: function (p) { return game.owner(p) === "a" ? 1 : -1; },

  // [dr, dc] directions a piece may step / capture in
  stepDirs: function (R, p) {
    var f = game.fwd(p);
    if (game.king(p)) return R.orth ? [[1, 0], [-1, 0], [0, 1], [0, -1]] : [[1, 1], [1, -1], [-1, 1], [-1, -1]];
    return R.orth ? [[f, 0], [0, -1], [0, 1]] : [[f, -1], [f, 1]];
  },
  capDirs: function (R, p) {
    var d = game.stepDirs(R, p), f = game.fwd(p);
    if (game.king(p) || !R.back) return d;
    return d.concat(R.orth ? [[-f, 0]] : [[-f, -1], [-f, 1]]);
  },
  inside: function (R, r, c) { return r >= 0 && c >= 0 && r < R.n && c < R.n; },

  // all capture sequences from square i: arrays of landing squares
  captures: function (R, b, i, piece, path, lastDir, out) {
    var n = R.n, r = Math.floor(i / n), c = i % n, found = false, ds = game.capDirs(R, piece);
    var flying = game.king(piece) && R.fly;
    for (var k = 0; k < ds.length; k++) {
      var dr = ds[k][0], dc = ds[k][1];
      // Turkish: a flying king may not turn back the way it came
      if (flying && R.orth && lastDir && dr === -lastDir[0] && dc === -lastDir[1]) continue;
      var rr = r + dr, cc = c + dc;
      if (flying) while (game.inside(R, rr, cc) && b[rr * n + cc] === null) { rr += dr; cc += dc; }
      if (!game.inside(R, rr, cc)) continue;
      var mid = rr * n + cc;
      if (!b[mid] || game.owner(b[mid]) === game.owner(piece)) continue;
      var lr = rr + dr, lc = cc + dc;
      while (game.inside(R, lr, lc) && b[lr * n + lc] === null) {
        found = true;
        var to = lr * n + lc, nb = b.slice();
        nb[i] = null; nb[mid] = null; nb[to] = piece;   // captured pieces come off at once
        var crowned = !game.king(piece) && (piece === "a" ? lr === n - 1 : lr === 0);
        if (crowned && !R.orth) out.push(path.concat([to]));   // American: crowning ends the move
        else game.captures(R, nb, to, piece, path.concat([to]), [dr, dc], out);
        if (!flying) break;
        lr += dr; lc += dc;
      }
    }
    if (!found && path.length > 1) out.push(path);
  },

  legal: function (s) {
    var R = s.R || game.rules({}), n = R.n, caps = [], steps = [];
    for (var i = 0; i < n * n; i++) {
      var p = s.b[i];
      if (!p || game.owner(p) !== s.side) continue;
      game.captures(R, s.b, i, p, [i], null, caps);
      var r = Math.floor(i / n), c = i % n, ds = game.stepDirs(R, p);
      for (var k = 0; k < ds.length; k++) {
        var tr = r + ds[k][0], tc = c + ds[k][1];
        while (game.inside(R, tr, tc) && !s.b[tr * n + tc]) {
          steps.push([i, tr * n + tc]);
          if (!(game.king(p) && R.fly)) break;
          tr += ds[k][0]; tc += ds[k][1];
        }
      }
    }
    if (caps.length && R.most) {
      var best = 0;
      caps.forEach(function (m) { best = Math.max(best, m.length); });
      caps = caps.filter(function (m) { return m.length === best; });
    }
    if (caps.length && R.must) return caps;
    return caps.concat(steps);
  },

  same: function (a, b) {
    if (!Array.isArray(a) || a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (Number(a[i]) !== b[i]) return false;
    return true;
  },

  move: function (s, player, m) {
    if (player !== s.p[s.side === "a" ? 0 : 1]) throw "not your turn";
    var legal = game.legal(s), ok = null;
    for (var k = 0; k < legal.length; k++) if (game.same(m, legal[k])) ok = legal[k];
    if (!ok) throw "illegal move";
    var R = s.R || game.rules({}), n = R.n, b = s.b.slice(), piece = b[ok[0]], captured = false;
    b[ok[0]] = null;
    for (var j = 1; j < ok.length; j++) {
      var a = ok[j - 1], z = ok[j];
      var dr = Math.sign(Math.floor(z / n) - Math.floor(a / n)), dc = Math.sign(z % n - a % n);
      for (var q = a + dr * n + dc; q !== z; q += dr * n + dc) {
        if (b[q]) { b[q] = null; captured = true; }
      }
    }
    var end = ok[ok.length - 1], row = Math.floor(end / n);
    if (piece === "a" && row === n - 1 || piece === "b" && row === 0) piece = piece.toUpperCase();
    b[end] = piece;
    return { b: b, p: s.p, side: s.side === "a" ? "b" : "a", quiet: captured ? 0 : s.quiet + 1, last: ok, R: R };
  },

  turn: function (s) { return game.status(s, {}).over ? "-" : s.p[s.side === "a" ? 0 : 1]; },
  moves: function (s, player) {
    if (player !== undefined && player !== s.p[s.side === "a" ? 0 : 1]) return [];
    return game.legal(s);
  },

  isCapture: function (s, m) {
    var R = s.R || game.rules({}), n = R.n, a = m[0], z = m[1];
    var dr = Math.sign(Math.floor(z / n) - Math.floor(a / n)), dc = Math.sign(z % n - a % n);
    for (var q = a + dr * n + dc; q !== z; q += dr * n + dc) if (s.b[q]) return true;
    return false;
  },

  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var mover = s.p[s.side === "a" ? 0 : 1], other = s.p[s.side === "a" ? 1 : 0];
    var legal = game.legal(s);
    if (!legal.length) return { over: true, winners: [other], text: nm(other) + " wins!" };
    if (s.quiet >= 80) return { over: true, winners: [], text: "Draw (40 moves without a capture)" };
    var must = (s.R || {}).must !== false && game.isCapture(s, legal[0]);
    return { over: false, winners: [], text: nm(mover) + " to move (" + (s.side === "a" ? "red" : "black") + ")" +
             (must ? " — must capture" : "") };
  },

  mine: function (s, v) { return v.me === s.p[0] ? "a" : v.me === s.p[1] ? "b" : null; },
  scr: function (s, v, i) {
    var n = (s.R || game.rules({})).n, flip = game.mine(s, v) !== "b";  // own pieces at the bottom
    var r = Math.floor(i / n), c = i % n;
    return flip ? [c, n - 1 - r] : [n - 1 - c, r];
  },

  render: function (ctx, s, v) {
    var R = s.R || game.rules({}), n = R.n, c = v.w / n, path = (v.ui && v.ui.path) || [];
    var reach = {};
    if (v.myTurn) game.legal(s).forEach(function (m) {
      if (path.length && game.same(m.slice(0, path.length), path) && m.length > path.length) reach[m[path.length]] = 1;
    });
    for (var i = 0; i < n * n; i++) {
      var xy = game.scr(s, v, i), dark = (Math.floor(i / n) + i % n) % 2 === 1;
      ctx.fillStyle = R.orth ? (dark ? "#b58a5a" : "#d9b98a") : (dark ? "#3d6b4a" : "#e8e1c9");
      if (s.last && s.last.indexOf(i) >= 0) ctx.fillStyle = R.orth ? "#c9a24d" : "#6b8e4e";
      if (path.indexOf(i) >= 0) ctx.fillStyle = "#4f74b8";
      ctx.fillRect(xy[0] * c, xy[1] * c, c, c);
      if (R.orth) { ctx.strokeStyle = "rgba(0,0,0,0.25)"; ctx.lineWidth = 1; ctx.strokeRect(xy[0] * c, xy[1] * c, c, c); }
      if (reach[i]) {
        ctx.fillStyle = "rgba(255,255,255,0.45)";
        ctx.beginPath(); ctx.arc(xy[0] * c + c / 2, xy[1] * c + c / 2, c * 0.15, 0, Math.PI * 2); ctx.fill();
      }
      var p = s.b[i];
      if (!p) continue;
      var cx = xy[0] * c + c / 2, cy = xy[1] * c + c / 2;
      ctx.fillStyle = game.owner(p) === "a" ? "#c8352e" : "#1e1e24";
      ctx.strokeStyle = game.owner(p) === "a" ? "#ffb3a8" : "#9aa0b4";
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, c * 0.38, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      if (game.king(p)) {
        ctx.fillStyle = "#f5c542"; ctx.font = "bold " + Math.floor(c * 0.45) + "px sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("♛", cx, cy + 1);
      }
    }
  },

  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var n = (s.R || game.rules({})).n, c = v.w / n, x = Math.floor(ev.x / c), y = Math.floor(ev.y / c);
    if (x < 0 || x >= n || y < 0 || y >= n) return null;
    var flip = game.mine(s, v) !== "b";
    var i = flip ? (n - 1 - y) * n + x : y * n + (n - 1 - x);
    var path = ((v.ui && v.ui.path) || []).concat([i]);
    var legal = game.legal(s), prefix = false;
    for (var k = 0; k < legal.length; k++) {
      if (game.same(legal[k], path)) return { move: path, ui: { path: [] } };
      if (game.same(legal[k].slice(0, path.length), path)) prefix = true;
    }
    if (prefix) return { ui: { path: path } };
    for (k = 0; k < legal.length; k++) if (legal[k][0] === i) return { ui: { path: [i] } };
    return { ui: { path: [] } };
  }
};
