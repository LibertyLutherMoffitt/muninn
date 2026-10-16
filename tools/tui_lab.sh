#!/usr/bin/env bash
# Terminal client lab: hub + 2 bots + the TUI in a detached tmux session.
# tools/tui_lab.sh start | keys <tmux keys...> | shot | stop
S=${MUNINN_LAB:-/tmp/muninn-tui-lab}; mkdir -p "$S"; cd "$(dirname "$0")/../python"; export PYTHONPATH=$PWD/src
case "$1" in
start)
  rm -rf $S/tl; mkdir -p $S/tl
  python -m muninn.sim.hub --port 7430 > $S/tl/hub.log 2>&1 & echo $! > $S/tl/pids
  sleep 1.5
  MUNINN_SIM=127.0.0.1:7430 python -m muninn.bot --name bob --pos 1,0 --echo --react --control 7531 --data $S/tl/bob > $S/tl/bob.log 2>&1 & echo $! >> $S/tl/pids
  MUNINN_SIM=127.0.0.1:7430 python -m muninn.bot --name carol --pos 5,0 --echo --data $S/tl/carol > $S/tl/carol.log 2>&1 & echo $! >> $S/tl/pids
  tmux -L muninn new-session -d -s tui -x 110 -y 30 "MUNINN_SIM=127.0.0.1:7430 MUNINN_DATA=$S/tl/me MUNINN_NAME=me PYTHONPATH=$PYTHONPATH python -m muninn.cli 2> $S/tl/tui.err"
  ;;
keys) shift; tmux -L muninn send-keys -t tui "$@" ;;
shot) tmux -L muninn capture-pane -p -t tui ;;
stop) tmux -L muninn kill-server 2>/dev/null; kill $(cat $S/tl/pids) 2>/dev/null ;;
esac
