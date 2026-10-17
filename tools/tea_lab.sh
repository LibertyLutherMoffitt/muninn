#!/usr/bin/env bash
# Bubble Tea client lab: hub + 2 bots + tea/ (from source) in a detached tmux.
#   tools/tea_lab.sh start | keys <tmux keys...> | shot | ansi | stop | bot '<botctl args>'
S=${MUNINN_LAB:-/tmp/muninn-tea-lab}; mkdir -p "$S"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
export PYTHONPATH=$ROOT/python/src
case "$1" in
start)
  rm -rf "$S/ta"; mkdir -p "$S/ta"
  (cd "$ROOT/tea" && go build -o "$S/ta/muninn-tea" .) || exit 1
  python -m muninn.sim.hub --port 7450 > "$S/ta/hub.log" 2>&1 & echo $! > "$S/ta/pids"
  sleep 1.5
  MUNINN_SIM=127.0.0.1:7450 python -m muninn.bot --name bob --pos 1,0 --echo --react --control 7551 --data "$S/ta/bob" > "$S/ta/bob.log" 2>&1 & echo $! >> "$S/ta/pids"
  MUNINN_SIM=127.0.0.1:7450 python -m muninn.bot --name carol --pos 0,1 --echo --control 7552 --data "$S/ta/carol" > "$S/ta/carol.log" 2>&1 & echo $! >> "$S/ta/pids"
  tmux -L muninn-tea new-session -d -s tea -x "${COLS:-120}" -y "${ROWS:-34}" \
    "MUNINN_SIM=127.0.0.1:7450 MUNINN_DATA=$S/ta/me MUNINN_NAME=${NAME-me} MUNINN_CORE='python -m muninn.cli' PYTHONPATH=$PYTHONPATH TERM=xterm-256color COLORTERM=truecolor $S/ta/muninn-tea 2> $S/ta/tea.err"
  ;;
keys) shift; tmux -L muninn-tea send-keys -t tea "$@" ;;
shot) tmux -L muninn-tea capture-pane -p -t tea ;;
ansi) tmux -L muninn-tea capture-pane -p -e -t tea ;;
png) # colour screenshot: xterm attached to the session on a private Xvfb
  XT=${XTERM:-$(nix build --no-link --print-out-paths nixpkgs#xterm)/bin/xterm}
  if ! pgrep -f "Xvfb :96" >/dev/null; then Xvfb :96 -screen 0 1400x900x24 >/dev/null 2>&1 & sleep 1; fi
  if ! kill -0 "$(cat "$S/ta/xterm.pid" 2>/dev/null)" 2>/dev/null; then
    DISPLAY=:96 "$XT" -geometry 120x34+0+0 -fa "DejaVu Sans Mono" -fs 10 -bg "#0f1115" -fg "#e5e7eb" \
      -e tmux -L muninn-tea attach -t tea >/dev/null 2>&1 & echo $! > "$S/ta/xterm.pid"; sleep 1.5
  fi
  DISPLAY=:96 import -window root -crop 1100x640+0+0 "${2:-$S/ta/shot.png}" && echo "${2:-$S/ta/shot.png}" ;;
bot) shift; python "$ROOT/tools/botctl.py" 7551 $1 ;;
stop) tmux -L muninn-tea kill-server 2>/dev/null; kill "$(cat "$S/ta/xterm.pid" 2>/dev/null)" 2>/dev/null; kill $(cat "$S/ta/pids") 2>/dev/null ;;
esac
