# Writing a Muninn game

**This file is the whole contract.** If you are an AI (or a person) asked to
write a game for Muninn, you don't need anything else from the repository:
read this, write one `.js` file, run the checker, share it.

Muninn is an offline chat app that works over Bluetooth on a plane. A game is
**one JavaScript file** that runs, unchanged, in two very different hosts:

- the desktop app — Qt's JavaScript engine drawing on a QML `Canvas`;
- the Android app — a WebView drawing on an HTML `<canvas>`.

The file travels **inside the game invite**, so everyone in a game runs
exactly the same rules, even if they never installed it. Players' moves are
sent over the mesh (possibly through other people's phones, possibly minutes
late) and every device recomputes the game from the move list. That's why the
rules must be pure and deterministic.

## 1. File layout

```js
// @muninn-game {"id": "nim", "name": "Nim", "icon": "🪨", "min": 2, "max": 2}
// One or two lines saying what the game is and what a move looks like.
var game = {
  aspect: 1,               // board width / height
  setup: function (players, seed, opts) { … },
  turn:  function (s) { … },
  move:  function (s, player, m) { … },
  status: function (s, names) { … },
  moves: function (s, player) { … },      // strongly recommended
  render: function (ctx, s, view) { … },
  input:  function (s, view, ev) { … },
};
```

Rules for the file:

- **Line 1 is the header**: `// @muninn-game {json}` with
  `id` (`[a-z0-9_-]{1,32}`, unique), `name`, `icon` (one emoji), `min`/`max`
  players. Optional `options` (see §4).
- The file must define a global `var game = { … }`. Nothing else is called.
- **≤ 40 KB** (it rides in the invite). No `import`, no network, no DOM, no
  `window`, no timers, no `Math.random()` in rules, no `Date` in rules.
- Write plain ES5-ish JavaScript (`var`, `function`); both engines run modern
  JS, but simple code is easiest to keep identical across them.

## 2. The contract

`players` is an array of opaque player ids (strings) in seat order; seat 0
invited the others. `names` maps id → display name.

| function | returns | must |
|---|---|---|
| `setup(players, seed, opts)` | the initial **state** | be plain JSON data (objects, arrays, numbers, strings, booleans, null). `seed` is an integer: use it (with a tiny PRNG, §6) for anything random, e.g. shuffling. `opts` may be `undefined` — fall back to defaults. |
| `turn(state)` | id of the player who moves next; `null` if anyone may move (simultaneous); any non-player value (convention: `"-"`) when the game is over | be cheap; the host calls it a lot |
| `move(state, player, m)` | a **new** state | be **pure and deterministic**: never modify `state`, no randomness except derived from the state/seed, same inputs → same output. **Throw** (any value) if `m` is illegal or it isn't `player`'s turn. `m` is plain JSON chosen by you (a number, string, `[from, to]`, …). |
| `status(state, names)` | `{over: bool, winners: [ids], text: string}` | `winners` ⊆ players; `[]` with `over: true` = draw; several winners = shared win. `text` is shown above the board ("Ann to play", "Bob wins!"). |
| `moves(state, player)` | array of legal moves for `player` | optional but strongly recommended: the checker, the test bot and hints use it. Return `[]` when it isn't their turn or the game is over. |
| `render(ctx, state, view)` | nothing | draw the whole board into `view.w × view.h` (CSS pixels, origin top-left) using only the canvas subset (§5) |
| `input(state, view, ev)` | `null`, `{ui: {…}}`, `{move: m}` or `{rt: data}` | turn a click/tap into a move. `ev = {type: "click", x, y}` in board coordinates (zoom/pan already undone) or `{type: "key", key, down}`. Return `{move}` **only for legal moves** — otherwise `null` (the host would show "illegal move"). `{ui}` updates local view state (e.g. a selected piece), merged into `view.ui`. |

`view` = `{w, h, me, players, names, ui, myTurn}` — `me` is the local
player's id (may be a spectator who isn't in `players`), `ui` is your local
view state, `myTurn` tells you whether to highlight legal targets.

### How the host runs a game

1. Seat 0 sends an **invite** (with your code, a random `seed` and chosen
   `opts`). Others **join**; it starts when `max` players joined or when seat 0
   presses *Start* (≥ `min`). Seat 0 can **unsend** the invite before it starts.
2. State = `setup(players, seed, opts)` folded over every **move** in order.
   A move whose sender isn't `turn(state)` (when that's a player id), or that
   makes `move()` throw, is skipped — identically on every device.
3. When a move makes `status().over` true, the mover's device records the
   result for the **scoreboard** ("Ann 3 · Bob 1"). Resigning counts as a loss.
4. Moves are delivered reliably (retried across relays and reconnects) but
   can be late; never assume both players see a state at the same time.
5. Real-time games (§7) add unreliable datagrams on top.

## 3. Coordinates, size, zoom

- Your board is `view.w × view.h` with `view.h = view.w / aspect`. Draw
  *everything* relative to `view.w` (e.g. `var c = view.w / 8` for a cell).
- The hosts handle zoom and pan (pinch/drag on phones, wheel/drag/`+`/`-`/`0`
  on desktop) and give `input()` board coordinates. Big boards (27×27, or a
  board that grows) need nothing special — just draw them small.
- Rotate the view per player if it helps (chess draws the local side at the
  bottom: map coordinates in both `render` and `input`).

## 4. Options (chosen when inviting)

```js
// @muninn-game {"id": "unlimited", …, "options": [
//   {"id": "k", "label": "In a row to win", "choices": [3, 4, 5, 6, 7], "default": 4}]}
```

(Write the header JSON on **one line**.) The inviter picks from `choices`;
the value arrives as `opts.k` in `setup`. Store what you need in the state.

## 5. Canvas subset

Only these exist in both hosts:

- methods: `fillRect strokeRect clearRect beginPath closePath moveTo lineTo
  arc quadraticCurveTo bezierCurveTo rect fill stroke fillText save restore
  translate rotate scale`
- properties: `fillStyle strokeStyle lineWidth lineCap font textAlign
  textBaseline globalAlpha`

Not available: `roundRect`, images, gradients, `measureText` for layout,
`setLineDash`, `strokeText`, `clip`, shadows. Text and emoji work
(`ctx.font = "bold 24px sans-serif"; ctx.fillText("♞", x, y)`), so chess
pieces are just glyphs. The host paints a dark background (`#151820`) before
calling `render`; light lines/marks read well.

## 6. Randomness

Never `Math.random()` in `setup`/`move`. Derive from `seed`:

```js
rng: function (a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0;
  var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
  return ((t ^ t >>> 14) >>> 0) / 4294967296; }; },
// setup: var r = game.rng(seed); deck.sort(function () { return r() - 0.5; }) …
```

If later moves need randomness (dice), store a counter in the state and use
`game.rng(seed * 1000003 + state.rolls)`.

## 7. Real-time games (optional)

For action games (see `pong.js`): `turn()` returns `null`, and you add

- `tick(state, dt, view)` → `{state, rt?}` — called ~30×/s with seconds
  elapsed; may return `rt` data to broadcast;
- `onRt(state, sender, body)` → state — another player's datagram arrived.

Datagrams are fire-and-forget (~5–15 kB/s per hop, can drop). Keep each
small (< 1 kB), send only your own player's inputs/positions, and let each
device simulate. Use `move` for anything that must not be lost (score).

## 8. Complete example

```js
// @muninn-game {"id": "nim", "name": "Nim", "icon": "🪨", "min": 2, "max": 2}
// 15 stones; take 1–3 per turn; whoever takes the last stone wins. Move = 1, 2 or 3.
var game = {
  aspect: 2,
  setup: function (players, seed, opts) { return { left: 15, p: players, t: 0, last: null }; },
  turn: function (s) { return s.left === 0 ? "-" : s.p[s.t % 2]; },
  moves: function (s, player) {
    if (s.left === 0 || player !== s.p[s.t % 2]) return [];
    var out = []; for (var k = 1; k <= Math.min(3, s.left); k++) out.push(k); return out;
  },
  move: function (s, player, m) {
    if (player !== s.p[s.t % 2]) throw "not your turn";
    if (typeof m !== "number" || m !== Math.floor(m) || m < 1 || m > 3 || m > s.left) throw "take 1-3";
    return { left: s.left - m, p: s.p, t: s.t + 1, last: player };
  },
  status: function (s, names) {
    if (s.left === 0) return { over: true, winners: [s.last], text: names[s.last] + " took the last stone!" };
    return { over: false, winners: [], text: names[s.p[s.t % 2]] + " to take 1–3 · " + s.left + " left" };
  },
  render: function (ctx, s, v) {
    var r = v.w / 40;
    for (var i = 0; i < s.left; i++) {
      ctx.fillStyle = "#a78bfa";
      ctx.beginPath(); ctx.arc(r * 2 + (i % 15) * r * 2.5, v.h * 0.35, r, 0, Math.PI * 2); ctx.fill();
    }
    for (var k = 1; k <= 3; k++) {                         // three buttons
      ctx.fillStyle = v.myTurn && k <= s.left ? "#7c3aed" : "#374151";
      ctx.fillRect((k - 1) * v.w / 3 + 10, v.h * 0.65, v.w / 3 - 20, v.h * 0.25);
      ctx.fillStyle = "#ffffff"; ctx.font = "bold " + Math.floor(v.h * 0.12) + "px sans-serif";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("take " + k, (k - 0.5) * v.w / 3, v.h * 0.775);
    }
  },
  input: function (s, v, ev) {
    if (ev.type !== "click" || ev.y < v.h * 0.65) return null;
    var k = Math.floor(ev.x / (v.w / 3)) + 1;
    return game.moves(s, v.me).indexOf(k) >= 0 ? { move: k } : null;
  }
};
```

## 9. Check it, then share it

```bash
nix develop --command python tools/gamecheck.py nim.js
```

The checker plays hundreds of random games with every player count and
option and reports: state not plain JSON, `move()` mutating its input or not
deterministic, legal moves throwing, garbage moves accepted, games stuck
with no legal moves, bad `status()` shapes, canvas calls outside the subset,
clicks that produce illegal moves, and how often random games finish. Fix
everything marked ✗; "✓ ready to share" means both apps will run it.

Sharing: put the file in a chat (desktop: `:file nim.js`, or `:sharegame
<id>` for an installed game; Android: games sheet → share) — the receiver
taps **Install game** on the attachment. Or just invite people: the code is
inside the invite, so they can play without installing anything.

### Checklist

- [ ] header on line 1, valid JSON, unique `id`, sensible `min`/`max`
- [ ] state is plain JSON; `move` returns a new object and throws on illegal input
- [ ] `turn` returns a non-player value (`"-"`) once `status().over` is true
- [ ] `moves()` matches what `move()` accepts
- [ ] `status().winners` only contains player ids; `[]` = draw
- [ ] everything drawn relative to `view.w`; only the canvas subset
- [ ] `input()` returns `{move}` only for legal moves, `{ui}` for selections
- [ ] no `Math.random`/`Date` in rules; randomness from `seed`
- [ ] `tools/gamecheck.py` says ✓

Bundled games to learn from: `tictactoe.js` (smallest), `connect4.js`,
`reversi.js`, `dots.js` (2–4 players, extra turns), `checkers.js` and
`chess.js` (select-then-move input, rotated board), `uttt.js` (nested
boards), `unlimited.js` (growing board, options, up to 8 players),
`chess4.js` (eliminations), `pong.js` (real-time).
