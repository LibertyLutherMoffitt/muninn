#!/usr/bin/env bash
# Qt GUI lab: sim hub + bots + the GUI (from source) on a headless X display.
#   tools/gui_lab.sh start            hub, bots bob (echo/react/play) + carol, GUI as "me"
#   tools/gui_lab.sh shot [out.png]   screenshot (default $S/gl/shot.png)
#   tools/gui_lab.sh type 'text'      type into the focused widget
#   tools/gui_lab.sh key Return       xdotool key names (ctrl+k, Escape, …)
#   tools/gui_lab.sh click X Y [btn]  click (btn 3 = right click)
#   tools/gui_lab.sh move X Y         hover
#   tools/gui_lab.sh bot 'send to=me text=hi'   drive bob (port 7541) via botctl
#   tools/gui_lab.sh log              GUI stderr
#   tools/gui_lab.sh stop
# Run inside `nix develop`. Size: MUNINN_LAB_SIZE (default 1280x800).
S=${MUNINN_LAB:-/tmp/muninn-gui-lab}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH=$ROOT/python/src DISPLAY=${MUNINN_LAB_DISPLAY:-:97}
mkdir -p "$S/gl"
case "$1" in
start)
  "$0" stop >/dev/null 2>&1
  rm -rf "$S/gl"; mkdir -p "$S/gl"
  Xvfb "$DISPLAY" -screen 0 "${MUNINN_LAB_SIZE:-1280x800}x24" >/dev/null 2>&1 & echo $! >> "$S/gl/pids"
  python -m muninn.sim.hub --port 7440 --decoys 30 > "$S/gl/hub.log" 2>&1 & echo $! >> "$S/gl/pids"
  sleep 1.5
  MUNINN_SIM=127.0.0.1:7440 python -m muninn.bot --name bob --pos 1,0 --echo --react --play \
    --control 7541 --data "$S/gl/bob" > "$S/gl/bob.log" 2>&1 & echo $! >> "$S/gl/pids"
  MUNINN_SIM=127.0.0.1:7440 python -m muninn.bot --name carol --pos 5,0 --echo \
    --control 7542 --data "$S/gl/carol" > "$S/gl/carol.log" 2>&1 & echo $! >> "$S/gl/pids"
  # private D-Bus: tray icons / notifications must not land on the host's desktop
  DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent \
  QT_QPA_PLATFORM=xcb QT_QUICK_BACKEND=software MUNINN_SIM=127.0.0.1:7440 \
    MUNINN_DATA="$S/gl/me" MUNINN_NAME=me \
    python -c 'from muninn.gui.main import main; main()' > "$S/gl/gui.log" 2>&1 & echo $! >> "$S/gl/pids"
  ;;
shot) import -window root "${2:-$S/gl/shot.png}" && echo "${2:-$S/gl/shot.png}" ;;
type) xdotool type --delay 20 "$2" ;;
key) shift; xdotool key "$@" ;;
click) xdotool mousemove "$2" "$3" click "${4:-1}" ;;
move) xdotool mousemove "$2" "$3" ;;
bot) shift; python "$ROOT/tools/botctl.py" 7541 $1 ;;
log) grep -v Fontconfig "$S/gl/gui.log" | tail -${2:-30} ;;
stop) [ -f "$S/gl/pids" ] && kill $(cat "$S/gl/pids") 2>/dev/null; rm -f "$S/gl/pids" ;;
*) sed -n 2,12p "$0" ;;
esac
