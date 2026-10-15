// @muninn-game {"id": "chess", "name": "Chess", "icon": "♟️", "min": 2, "max": 2}
// Full rules: castling, en passant, promotion (tap a promotion square to pick
// the piece), check, checkmate, stalemate, 50-move rule, insufficient material.
// Board: 64 squares, index = rank*8 + file, a1 = 0, h8 = 63.
// Pieces: "wK","bQ",... or null. Moves are UCI strings: "e2e4", "e7e8q".
var game = {
  aspect: 1,
  FILES: "abcdefgh",

  setup: function (players, seed) {
    var b = [];
    var back = ["R", "N", "B", "Q", "K", "B", "N", "R"];
    for (var i = 0; i < 64; i++) b.push(null);
    for (var f = 0; f < 8; f++) {
      b[f] = "w" + back[f]; b[8 + f] = "wP";
      b[48 + f] = "bP"; b[56 + f] = "b" + back[f];
    }
    return { b: b, p: players, side: "w", castle: "KQkq", ep: -1, half: 0, ply: 0, last: null };
  },

  sq: function (name) { return (name.charCodeAt(1) - 49) * 8 + (name.charCodeAt(0) - 97); },
  name: function (i) { return game.FILES[i % 8] + String(Math.floor(i / 8) + 1); },
  other: function (c) { return c === "w" ? "b" : "w"; },

  attacked: function (b, i, by) {
    // is square i attacked by colour `by`?
    var r = Math.floor(i / 8), f = i % 8, k, rr, ff, p;
    var pd = by === "w" ? -1 : 1;  // pawn of `by` attacks from rank r+pd... reversed
    for (k = -1; k <= 1; k += 2) {
      rr = r + pd; ff = f + k;
      if (rr >= 0 && rr < 8 && ff >= 0 && ff < 8 && b[rr * 8 + ff] === by + "P") return true;
    }
    var N = [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1], [-1, -2], [-2, -1]];
    for (k = 0; k < 8; k++) {
      rr = r + N[k][0]; ff = f + N[k][1];
      if (rr >= 0 && rr < 8 && ff >= 0 && ff < 8 && b[rr * 8 + ff] === by + "N") return true;
    }
    for (rr = -1; rr <= 1; rr++) for (ff = -1; ff <= 1; ff++) {
      if (!rr && !ff) continue;
      var a = r + rr, c = f + ff;
      if (a >= 0 && a < 8 && c >= 0 && c < 8 && b[a * 8 + c] === by + "K") return true;
    }
    var dirs = [[1, 0, "RQ"], [-1, 0, "RQ"], [0, 1, "RQ"], [0, -1, "RQ"],
                [1, 1, "BQ"], [1, -1, "BQ"], [-1, 1, "BQ"], [-1, -1, "BQ"]];
    for (k = 0; k < 8; k++) {
      rr = r + dirs[k][0]; ff = f + dirs[k][1];
      while (rr >= 0 && rr < 8 && ff >= 0 && ff < 8) {
        p = b[rr * 8 + ff];
        if (p) { if (p[0] === by && dirs[k][2].indexOf(p[1]) >= 0) return true; break; }
        rr += dirs[k][0]; ff += dirs[k][1];
      }
    }
    return false;
  },

  kingSq: function (b, c) { for (var i = 0; i < 64; i++) if (b[i] === c + "K") return i; return -1; },
  inCheck: function (s, c) { return game.attacked(s.b, game.kingSq(s.b, c), game.other(c)); },

  pseudo: function (s) {
    var b = s.b, c = s.side, out = [], i, r, f, k, t;
    function add(from, to) {
      var promo = b[from][1] === "P" && (Math.floor(to / 8) === 7 || Math.floor(to / 8) === 0);
      if (promo) { "qrbn".split("").forEach(function (x) { out.push(game.name(from) + game.name(to) + x); }); }
      else out.push(game.name(from) + game.name(to));
    }
    for (i = 0; i < 64; i++) {
      var p = b[i];
      if (!p || p[0] !== c) continue;
      r = Math.floor(i / 8); f = i % 8;
      var kind = p[1];
      if (kind === "P") {
        var dir = c === "w" ? 1 : -1, start = c === "w" ? 1 : 6;
        t = i + 8 * dir;
        if (t >= 0 && t < 64 && !b[t]) {
          add(i, t);
          if (r === start && !b[t + 8 * dir]) add(i, t + 8 * dir);
        }
        for (k = -1; k <= 1; k += 2) {
          if (f + k < 0 || f + k > 7) continue;
          t = i + 8 * dir + k;
          if (t < 0 || t >= 64) continue;
          if ((b[t] && b[t][0] !== c) || t === s.ep) add(i, t);
        }
      } else if (kind === "N" || kind === "K") {
        var D = kind === "N" ? [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1], [-1, -2], [-2, -1]]
                             : [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
        for (k = 0; k < 8; k++) {
          var rr = r + D[k][0], ff = f + D[k][1];
          if (rr < 0 || rr > 7 || ff < 0 || ff > 7) continue;
          t = rr * 8 + ff;
          if (!b[t] || b[t][0] !== c) add(i, t);
        }
        if (kind === "K") {
          var home = c === "w" ? 4 : 60, o = game.other(c);
          if (i === home && !game.attacked(b, i, o)) {
            var ks = c === "w" ? "K" : "k", qs = c === "w" ? "Q" : "q";
            if (s.castle.indexOf(ks) >= 0 && !b[i + 1] && !b[i + 2] && b[i + 3] === c + "R" &&
                !game.attacked(b, i + 1, o) && !game.attacked(b, i + 2, o)) add(i, i + 2);
            if (s.castle.indexOf(qs) >= 0 && !b[i - 1] && !b[i - 2] && !b[i - 3] && b[i - 4] === c + "R" &&
                !game.attacked(b, i - 1, o) && !game.attacked(b, i - 2, o)) add(i, i - 2);
          }
        }
      } else {
        var dirs = kind === "R" ? [[1, 0], [-1, 0], [0, 1], [0, -1]]
                 : kind === "B" ? [[1, 1], [1, -1], [-1, 1], [-1, -1]]
                 : [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
        for (k = 0; k < dirs.length; k++) {
          var a = r + dirs[k][0], bb = f + dirs[k][1];
          while (a >= 0 && a < 8 && bb >= 0 && bb < 8) {
            t = a * 8 + bb;
            if (b[t]) { if (b[t][0] !== c) add(i, t); break; }
            add(i, t);
            a += dirs[k][0]; bb += dirs[k][1];
          }
        }
      }
    }
    return out;
  },

  apply: function (s, mv) {
    var b = s.b.slice(), from = game.sq(mv.slice(0, 2)), to = game.sq(mv.slice(2, 4));
    var p = b[from], c = p[0], cap = b[to] !== null;
    var ep = -1, castle = s.castle;
    if (p[1] === "P" && to === s.ep) { b[to + (c === "w" ? -8 : 8)] = null; cap = true; }
    if (p[1] === "P" && Math.abs(to - from) === 16) ep = (to + from) / 2;
    if (p[1] === "K" && Math.abs(to - from) === 2) {
      if (to > from) { b[from + 1] = b[from + 3]; b[from + 3] = null; }
      else { b[from - 1] = b[from - 4]; b[from - 4] = null; }
    }
    b[to] = p; b[from] = null;
    if (mv.length === 5) b[to] = c + mv[4].toUpperCase();
    function strip(x) { castle = castle.replace(x, ""); }
    if (p === "wK") { strip("K"); strip("Q"); }
    if (p === "bK") { strip("k"); strip("q"); }
    if (from === 0 || to === 0) strip("Q");
    if (from === 7 || to === 7) strip("K");
    if (from === 56 || to === 56) strip("q");
    if (from === 63 || to === 63) strip("k");
    return { b: b, p: s.p, side: game.other(s.side), castle: castle, ep: ep,
             half: (p[1] === "P" || cap) ? 0 : s.half + 1, ply: s.ply + 1, last: [from, to] };
  },

  legal: function (s) {
    var c = s.side;
    return game.pseudo(s).filter(function (m) { return !game.inCheck(game.apply(s, m), c); });
  },

  turn: function (s) {
    if (game.status(s, {}).over) return "-";
    return s.p[s.side === "w" ? 0 : 1];
  },
  moves: function (s, player) { return game.legal(s); },

  move: function (s, player, m) {
    if (typeof m !== "string") throw "bad move";
    if (game.legal(s).indexOf(m) < 0) throw "illegal move " + m;
    return game.apply(s, m);
  },

  material: function (b) {
    var out = [];
    for (var i = 0; i < 64; i++) if (b[i] && b[i][1] !== "K") out.push(b[i][1]);
    return out;
  },

  status: function (s, names) {
    var legal = game.legal(s), c = s.side, mover = s.p[c === "w" ? 0 : 1], other = s.p[c === "w" ? 1 : 0];
    var nm = function (id) { return (names && names[id]) || "?"; };
    if (!legal.length) {
      if (game.inCheck(s, c)) return { over: true, winners: [other], text: "Checkmate — " + nm(other) + " wins" };
      return { over: true, winners: [], text: "Stalemate — draw" };
    }
    if (s.half >= 100) return { over: true, winners: [], text: "Draw (50-move rule)" };
    var mat = game.material(s.b).join("");
    if (mat === "" || mat === "N" || mat === "B") return { over: true, winners: [], text: "Draw (insufficient material)" };
    return { over: false, winners: [],
             text: nm(mover) + " to move (" + (c === "w" ? "white" : "black") + ")" + (game.inCheck(s, c) ? " — check!" : "") };
  },

  GLYPH: { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" },

  myColour: function (s, v) { return v.me === s.p[0] ? "w" : (v.me === s.p[1] ? "b" : null); },

  toScreen: function (s, v, i) {
    var flip = game.myColour(s, v) === "b";
    var f = i % 8, r = Math.floor(i / 8);
    return flip ? [7 - f, r] : [f, 7 - r];
  },

  render: function (ctx, s, v) {
    var c = v.w / 8, i, xy;
    var sel = v.ui.sel === undefined ? -1 : v.ui.sel;
    var targets = {};
    if (sel >= 0) game.legal(s).forEach(function (m) { if (game.sq(m.slice(0, 2)) === sel) targets[game.sq(m.slice(2, 4))] = 1; });
    var check = game.inCheck(s, s.side) ? game.kingSq(s.b, s.side) : -1;
    for (i = 0; i < 64; i++) {
      xy = game.toScreen(s, v, i);
      var light = (i % 8 + Math.floor(i / 8)) % 2 === 1;
      ctx.fillStyle = light ? "#e9dcc4" : "#a77e5a";
      if (s.last && (i === s.last[0] || i === s.last[1])) ctx.fillStyle = light ? "#f2e38b" : "#cdb352";
      if (i === sel) ctx.fillStyle = "#7c9eda";
      if (i === check) ctx.fillStyle = "#e05a5a";
      ctx.fillRect(xy[0] * c, xy[1] * c, c, c);
    }
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "bold " + Math.floor(c * 0.78) + "px sans-serif";
    for (i = 0; i < 64; i++) {
      var p = s.b[i];
      xy = game.toScreen(s, v, i);
      var cx = xy[0] * c + c / 2, cy = xy[1] * c + c / 2 + c * 0.04;
      if (p) {
        ctx.fillStyle = p[0] === "w" ? "#ffffff" : "#1b1b1f";
        ctx.strokeStyle = p[0] === "w" ? "#1b1b1f" : "#ffffff";
        ctx.lineWidth = 1.2;
        ctx.fillText(game.GLYPH[p[1]], cx, cy);
      }
      if (targets[i]) {
        ctx.fillStyle = "rgba(30,30,40,0.35)";
        ctx.beginPath(); ctx.arc(xy[0] * c + c / 2, xy[1] * c + c / 2, c * (p ? 0.45 : 0.15), 0, Math.PI * 2); ctx.fill();
      }
    }
    if (v.ui.promo) {  // promotion picker
      ctx.fillStyle = "rgba(0,0,0,0.6)"; ctx.fillRect(0, 0, v.w, v.h);
      var opts = ["q", "r", "b", "n"], my = game.myColour(s, v) || "w";
      for (i = 0; i < 4; i++) {
        ctx.fillStyle = "#e9dcc4"; ctx.fillRect((2 + i) * c, 3.5 * c, c, c);
        ctx.fillStyle = my === "w" ? "#ffffff" : "#1b1b1f";
        ctx.fillText(game.GLYPH[opts[i].toUpperCase()], (2.5 + i) * c, 4 * c);
      }
    }
  },

  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var c = v.w / 8, sx = Math.floor(ev.x / c), sy = Math.floor(ev.y / c);
    if (sx < 0 || sx > 7 || sy < 0 || sy > 7) return null;
    if (v.ui.promo) {
      if (sy === 3 && sx >= 2 && sx <= 5) return { move: v.ui.promo + "qrbn"[sx - 2], ui: { promo: null, sel: -1 } };
      return { ui: { promo: null, sel: -1 } };
    }
    var flip = game.myColour(s, v) === "b";
    var i = flip ? sy * 8 + (7 - sx) : (7 - sy) * 8 + sx;
    var sel = v.ui.sel === undefined ? -1 : v.ui.sel;
    var mine = game.myColour(s, v);
    if (sel >= 0) {
      var base = game.name(sel) + game.name(i);
      var legal = game.legal(s);
      if (legal.indexOf(base) >= 0) return { move: base, ui: { sel: -1 } };
      if (legal.indexOf(base + "q") >= 0) return { ui: { promo: base, sel: sel } };
    }
    if (s.b[i] && s.b[i][0] === mine) return { ui: { sel: i } };
    return { ui: { sel: -1 } };
  }
};
