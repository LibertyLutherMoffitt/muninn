# Muninn game plugins

A game is **one JavaScript file**. The same file runs in the desktop GUI
(Qt Quick JS engine + `Canvas`) and on Android (WebView + `<canvas>`), and it is
shipped *inside the game invite*, so every player runs identical rules — even
someone who never installed the game. A game can also be shared as a file
(`:sharegame <id>` on desktop, "Share game" on Android) and installed by the
receiver.

## Header

The first line must be a JSON header:

```js
// @muninn-game {"id": "tictactoe", "name": "Tic-tac-toe", "icon": "⭕", "min": 2, "max": 2}
```

`id` is `[a-z0-9_-]{1,32}`. `min`/`max` = number of players. Invites carry the
code, so keep files under 40 KB.

## The `game` object

```js
var game = {
  aspect: 1.0,                       // board width / height

  setup(players, seed) {},           // -> initial state (plain JSON data)
  turn(state) {},                    // -> id of the player to move, or null
                                     //    (null = anyone may move / simultaneous)
  move(state, player, move) {},      // -> NEW state; throw on illegal moves.
                                     //    Must be pure + deterministic.
  status(state, names) {},           // -> {over, winners: [ids], text}
  moves(state, player) {},           // optional: legal moves (bots, hints)

  render(ctx, state, view) {},       // draw; view = {w, h, me, players, names,
                                     //               ui, myTurn}
  input(state, view, ev) {},         // ev = {type:"click", x, y}
                                     //    | {type:"key", key, down}
                                     // -> null | {ui: {...}} (local view state,
                                     //    e.g. a selected piece)
                                     //    | {move: m} (sent to everyone)
                                     //    | {rt: any} (real-time datagram)

  // optional real-time hooks (unreliable datagrams, ~30 Hz):
  tick(state, dt, view) {},          // -> {state, rt?}
  onRt(state, sender, body) {},      // -> state
};
```

### How the host runs a session

1. **invite** (creator) → others **join**/**decline**. The game starts when
   `max` players joined, or when the creator presses *Start* (≥ `min`).
2. Players = creator + joiners in join order.
3. State = `setup(players, seed)` folded over every **move** event, ordered by
   move number. A move whose sender isn't `turn(state)` (when non-null), or
   that makes `move()` throw, is ignored — identically on every device, so all
   players always agree.
4. Moves are delivered reliably (retried across relays and disconnects).
   Real-time `rt` datagrams are best-effort and may be dropped.

### Canvas subset

Use only what both QML `Context2D` and HTML canvas support: `fillRect`,
`strokeRect`, `clearRect`, `beginPath`, `closePath`, `moveTo`, `lineTo`,
`arc`, `quadraticCurveTo`, `bezierCurveTo`, `rect`, `fill`, `stroke`,
`fillText`, `save`, `restore`, `translate`, `rotate`, `scale`, and the
properties `fillStyle`, `strokeStyle`, `lineWidth`, `lineCap`, `font`,
`textAlign`, `textBaseline`, `globalAlpha`. Emoji/Unicode text works for
pieces (♔♕♖♗♘♙).

Avoid: `roundRect`/`roundedRect`, images, `measureText` widths for layout,
`Math.random()` in `setup`/`move` (use the `seed`).
