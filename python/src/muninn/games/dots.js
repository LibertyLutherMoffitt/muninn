// @muninn-game {"id": "dots", "name": "Dots and Boxes", "icon": "🔲", "min": 2, "max": 4}
// Take turns drawing a line; close a box to score it and go again.
// 5x5 boxes. A move is a line id: "h r c" (horizontal, row 0..5, col 0..4)
// or "v r c" (vertical, row 0..4, col 0..5).
var game = {
  aspect: 1,
  N: 5,
  setup: function (players, seed) {
    return { p: players, lines: {}, boxes: {}, t: 0, last: null };
  },
  all: function () {
    var out = [], n = game.N, r, c;
    for (r = 0; r <= n; r++) for (c = 0; c < n; c++) out.push("h " + r + " " + c);
    for (r = 0; r < n; r++) for (c = 0; c <= n; c++) out.push("v " + r + " " + c);
    return out;
  },
  moves: function (s) { return game.all().filter(function (l) { return !s.lines[l]; }); },
  closed: function (lines, r, c) {
    return lines["h " + r + " " + c] && lines["h " + (r + 1) + " " + c] &&
           lines["v " + r + " " + c] && lines["v " + r + " " + (c + 1)];
  },
  turn: function (s) { return game.moves(s).length ? s.p[s.t % s.p.length] : "-"; },
  move: function (s, player, m) {
    if (typeof m !== "string" || game.all().indexOf(m) < 0 || s.lines[m]) throw "illegal line";
    var lines = {}, boxes = {}, k;
    for (k in s.lines) lines[k] = s.lines[k];
    for (k in s.boxes) boxes[k] = s.boxes[k];
    lines[m] = true;
    var who = s.t % s.p.length, scored = false;
    for (var r = 0; r < game.N; r++) for (var c = 0; c < game.N; c++) {
      var key = r + "," + c;
      if (boxes[key] === undefined && game.closed(lines, r, c)) { boxes[key] = who; scored = true; }
    }
    return { p: s.p, lines: lines, boxes: boxes, t: scored ? s.t : s.t + 1, last: m };
  },
  scores: function (s) {
    var sc = s.p.map(function () { return 0; });
    for (var k in s.boxes) sc[s.boxes[k]]++;
    return sc;
  },
  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var sc = game.scores(s);
    var line = s.p.map(function (id, i) { return nm(id) + " " + sc[i]; }).join(" · ");
    if (!game.moves(s).length) {
      var best = Math.max.apply(null, sc), w = [];
      sc.forEach(function (v, i) { if (v === best) w.push(s.p[i]); });
      return { over: true, winners: w, text: (w.length > 1 ? "Tie! " : nm(w[0]) + " wins! ") + line };
    }
    return { over: false, winners: [], text: nm(s.p[s.t % s.p.length]) + " to draw · " + line };
  },
  COLORS: ["#7c3aed", "#10b981", "#f59e0b", "#ef4444"],
  geom: function (v) { var m = v.w * 0.08; return { m: m, step: (v.w - 2 * m) / game.N }; },
  render: function (ctx, s, v) {
    var g = game.geom(v), n = game.N, r, c;
    for (var key in s.boxes) {
      var rc = key.split(",");
      ctx.globalAlpha = 0.35; ctx.fillStyle = game.COLORS[s.boxes[key]];
      ctx.fillRect(g.m + Number(rc[1]) * g.step, g.m + Number(rc[0]) * g.step, g.step, g.step);
      ctx.globalAlpha = 1;
    }
    ctx.lineCap = "round";
    game.all().forEach(function (l) {
      var p = l.split(" "), rr = Number(p[1]), cc = Number(p[2]);
      var x1 = g.m + cc * g.step, y1 = g.m + rr * g.step;
      var x2 = p[0] === "h" ? x1 + g.step : x1, y2 = p[0] === "h" ? y1 : y1 + g.step;
      ctx.strokeStyle = s.lines[l] ? (l === s.last ? "#ffffff" : "#c9cbd6") : "rgba(255,255,255,0.06)";
      ctx.lineWidth = s.lines[l] ? 5 : 3;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    });
    ctx.fillStyle = "#ffffff";
    for (r = 0; r <= n; r++) for (c = 0; c <= n; c++) {
      ctx.beginPath(); ctx.arc(g.m + c * g.step, g.m + r * g.step, 5, 0, Math.PI * 2); ctx.fill();
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click") return null;
    var g = game.geom(v), best = null, bd = 1e9;
    game.moves(s).forEach(function (l) {
      var p = l.split(" "), rr = Number(p[1]), cc = Number(p[2]);
      var mx = g.m + cc * g.step + (p[0] === "h" ? g.step / 2 : 0);
      var my = g.m + rr * g.step + (p[0] === "v" ? g.step / 2 : 0);
      var d = Math.abs(ev.x - mx) + Math.abs(ev.y - my);
      if (d < bd) { bd = d; best = l; }
    });
    return best && bd < g.step * 0.6 ? { move: best } : null;
  }
};
