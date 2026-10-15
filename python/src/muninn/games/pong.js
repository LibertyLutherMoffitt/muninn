// @muninn-game {"id": "pong", "name": "Pong (real-time)", "icon": "🏓", "min": 2, "max": 2}
// Real-time demo of the datagram channel: each player streams their paddle
// position ~30x/s; the first player simulates the ball and streams it too.
// Datagrams can be lost — the next one simply supersedes it. First to 7.
// Controls: arrow keys / W-S, or tap/click where the paddle should go.
var game = {
  aspect: 1.6,
  WIN: 7,
  setup: function (players, seed) {
    return { p: players, ball: { x: 0.5, y: 0.5, vx: 0.45, vy: 0.25 }, pads: [0.5, 0.5],
             score: [0, 0], t: 0, sendAcc: 0 };
  },
  turn: function (s) { return null; },  // everyone acts at once
  moves: function (s) { return []; },
  move: function (s, player, m) { throw "pong has no turn moves"; },
  status: function (s, names) {
    var nm = function (id) { return (names && names[id]) || "?"; };
    var sc = s.score;
    if (sc[0] >= game.WIN || sc[1] >= game.WIN) {
      var w = sc[0] > sc[1] ? 0 : 1;
      return { over: true, winners: [s.p[w]], text: nm(s.p[w]) + " wins " + sc[w] + "–" + sc[1 - w] };
    }
    return { over: false, winners: [], text: nm(s.p[0]) + " " + sc[0] + " : " + sc[1] + " " + nm(s.p[1]) };
  },
  side: function (s, v) { return v.me === s.p[0] ? 0 : v.me === s.p[1] ? 1 : -1; },
  copy: function (s) {
    return { p: s.p, ball: { x: s.ball.x, y: s.ball.y, vx: s.ball.vx, vy: s.ball.vy },
             pads: s.pads.slice(), score: s.score.slice(), t: s.t, sendAcc: s.sendAcc };
  },
  tick: function (s0, dt, v) {
    var s = game.copy(s0), me = game.side(s, v);
    if (me < 0 || game.status(s, {}).over) return { state: s };
    dt = Math.min(dt, 0.05);
    var ui = v.ui || {};
    var dir = (ui.down ? 1 : 0) - (ui.up ? 1 : 0);
    if (dir) s.pads[me] = Math.max(0.1, Math.min(0.9, s.pads[me] + dir * dt * 1.2));
    else if (ui.target !== undefined) {
      var d = ui.target - s.pads[me];
      s.pads[me] += Math.max(-dt * 1.4, Math.min(dt * 1.4, d));
    }
    if (me === 0) {  // authority: simulate the ball
      var b = s.ball;
      b.x += b.vx * dt; b.y += b.vy * dt;
      if (b.y < 0.02 || b.y > 0.98) { b.vy = -b.vy; b.y = Math.max(0.02, Math.min(0.98, b.y)); }
      var hit = function (px, pad) { return Math.abs(b.y - pad) < 0.11 && Math.abs(b.x - px) < 0.02; };
      if (b.vx < 0 && hit(0.04, s.pads[0])) { b.vx = Math.abs(b.vx) * 1.05; b.vy += (b.y - s.pads[0]) * 2; }
      if (b.vx > 0 && hit(0.96, s.pads[1])) { b.vx = -Math.abs(b.vx) * 1.05; b.vy += (b.y - s.pads[1]) * 2; }
      if (b.x < 0 || b.x > 1) {
        s.score[b.x < 0 ? 1 : 0]++;
        s.ball = { x: 0.5, y: 0.5, vx: (b.x < 0 ? 0.45 : -0.45), vy: (s.score[0] + s.score[1]) % 2 ? 0.25 : -0.25 };
      }
    }
    s.sendAcc += dt;
    if (s.sendAcc >= 0.033) {
      s.sendAcc = 0;
      var msg = { pad: s.pads[me] };
      if (me === 0) { msg.ball = s.ball; msg.score = s.score; }
      return { state: s, rt: msg };
    }
    return { state: s };
  },
  onRt: function (s0, sender, body) {
    var s = game.copy(s0), from = sender === s.p[0] ? 0 : sender === s.p[1] ? 1 : -1;
    if (from < 0 || !body) return s;
    if (typeof body.pad === "number") s.pads[from] = body.pad;
    if (from === 0 && body.ball) { s.ball = body.ball; s.score = body.score || s.score; }
    return s;
  },
  render: function (ctx, s, v) {
    var W = v.w, H = v.h;
    ctx.fillStyle = "#0b0d12"; ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "rgba(255,255,255,0.15)"; ctx.lineWidth = 2;
    for (var y = 0; y < H; y += 18) { ctx.beginPath(); ctx.moveTo(W / 2, y); ctx.lineTo(W / 2, y + 9); ctx.stroke(); }
    ctx.fillStyle = "#e5e7eb";
    var ph = H * 0.2, pw = W * 0.015;
    ctx.fillRect(W * 0.04 - pw, s.pads[0] * H - ph / 2, pw, ph);
    ctx.fillRect(W * 0.96, s.pads[1] * H - ph / 2, pw, ph);
    ctx.fillStyle = "#f5c542";
    ctx.beginPath(); ctx.arc(s.ball.x * W, s.ball.y * H, H * 0.018, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.7)"; ctx.font = "bold " + Math.floor(H * 0.1) + "px sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(String(s.score[0]), W * 0.4, H * 0.04); ctx.fillText(String(s.score[1]), W * 0.6, H * 0.04);
  },
  input: function (s, v, ev) {
    if (ev.type === "click") return { ui: { target: ev.y / v.h, up: false, down: false } };
    if (ev.type === "key") {
      if (ev.key === "ArrowUp" || ev.key === "w") return { ui: { up: ev.down } };
      if (ev.key === "ArrowDown" || ev.key === "s") return { ui: { down: ev.down } };
    }
    return null;
  }
};
