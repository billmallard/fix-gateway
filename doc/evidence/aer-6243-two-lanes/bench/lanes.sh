#!/bin/sh
# AER-6243 bench run: two private lanes on the Beelink, and the five-way kill of lane 1.
# Touches nothing live: its own gateways (netfix 3497, 3498), its own X-Plane sockets
# (49021, 49022), its own pyEfis on Xvfb :7 (lane 1) and :8 (lane 2).
#
#   sh lanes.sh setup FGW_SHA PYEFIS_SHA   clone both trees at those commits
#   sh lanes.sh start                      hub, both gateways, Xvfb, both pyEfis, recorder
#   sh lanes.sh run                        the five kills of lane 1, with grabs
#   sh lanes.sh stop                       stop everything this script started
#   sh lanes.sh feed                       dry run only: synthetic PITCH, ROLL, IAS, ALT
#                                          into both lanes at 10 Hz, when the sim is off
S=$HOME/bench-scratch/aer6243
R=$S/run
PY=$HOME/pyefis-venv/bin/python
H=$(dirname "$(readlink -f "$0")")

spawn() { # spawn NAME CMD...: detached, pid in $R/NAME.pid, output in $R/NAME.log
  n=$1; shift
  setsid sh -c 'echo $$ > "$0"; exec "$@"' "$R/$n.pid" "$@" > "$R/$n.log" 2>&1 < /dev/null &
  sleep 0.3
}
pid() { cat "$R/$1.pid" 2>/dev/null; }
alive() { p=$(pid "$1") && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
gw() {
  spawn "gw$1" env PYTHONPATH="$S/fgw/src" "$PY" -c \
    "import sys; sys.argv=['fixgw','--config-file','$S/lane$1/lane.yaml']; from fixgw.server import main; main()"
}
efis() {
  spawn "efis$1" env DISPLAY=:$((6 + $1)) QT_QPA_PLATFORM=xcb PYTHONPATH="$S/pyefis/src" "$PY" -c \
    "import sys; sys.argv=['pyefis','--config-file','$S/efis$1/default.yaml']; from pyefis.main import main; main()"
}
phase() { echo "$1" > "$R/phase"; echo "$(date -u +%H:%M:%S.%N | cut -c1-12) $1" >> "$R/run.log"; }
grab() { # grab NAME: both panes, full frame
  for l in 1 2; do
    DISPLAY=:$((6 + l)) ffmpeg -loglevel error -y -f x11grab -video_size 1920x1080 \
      -i :$((6 + l)) -frames:v 1 "$R/${1}_lane$l.png"
  done
  echo "$(date -u +%H:%M:%S) grab $1" >> "$R/run.log"
}

case "$1" in
setup)
  mkdir -p "$S" && cd "$S" || exit 1
  [ -d fgw ] || git clone -q --reference "$HOME/src/fix-gateway" https://github.com/billmallard/fix-gateway.git fgw
  git -C fgw fetch -q origin && git -C fgw checkout -q "$2"
  [ -d pyefis ] || git clone -q --reference "$HOME/src/pyEfis" https://github.com/billmallard/pyEfis.git pyefis
  git -C pyefis fetch -q origin && git -C pyefis checkout -q "$3"
  git -C fgw log --oneline -1; git -C pyefis log --oneline -1
  ;;
start)
  mkdir -p "$R"; rm -f "$R/CUT" "$R/phase"
  spawn hub python3 "$H/hub.py" "$R"
  sleep 1
  "$PY" "$H/build.py" "$S" || exit 1
  gw 1; gw 2
  spawn xvfb7 Xvfb :7 -screen 0 1920x1080x24 -nolisten tcp
  spawn xvfb8 Xvfb :8 -screen 0 1920x1080x24 -nolisten tcp
  sleep 3
  efis 1; efis 2
  spawn recorder python3 "$H/recorder.py" "$R" 3497 3498
  for n in hub gw1 gw2 xvfb7 xvfb8 efis1 efis2 recorder; do echo "$n $(pid $n) $(alive $n && echo up || echo DOWN)"; done
  ;;
feed)
  spawn feeder python3 -c '
import math, socket, threading, time
t0 = time.time()
def feed(port):  # one thread per lane, so a dead lane 1 never stops lane 2
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            while True:
                t = time.time() - t0
                s.sendall(("PITCH;%.2f\nROLL;%.2f\nIAS;%.1f\nALT;%.0f\n" % (
                    2 + 3 * math.sin(t / 7), 15 * math.sin(t / 11),
                    110 + 5 * math.sin(t / 13), 4500 + 200 * math.sin(t / 17))).encode())
                time.sleep(0.1)
        except OSError:
            time.sleep(0.5)
for port in (3497, 3498):
    threading.Thread(target=feed, args=(port,), daemon=True).start()
while True:
    time.sleep(60)
'
  ;;
run)
  for mode in gw_kill gw_stop efis_kill efis_stop can_cut; do
    phase "$mode:before"; sleep 15; grab "${mode}_1before"; sleep 5
    phase "$mode:fault"
    case $mode in
      gw_kill) kill -KILL "$(pid gw1)" ;;
      gw_stop) kill -STOP "$(pid gw1)" ;;
      efis_kill) kill -KILL "$(pid efis1)" ;;
      efis_stop) kill -STOP "$(pid efis1)" ;;
      can_cut) touch "$R/CUT" ;;
    esac
    sleep 10; grab "${mode}_2during"; sleep 10
    phase "$mode:recover"
    case $mode in
      gw_kill) gw 1 ;;
      gw_stop) kill -CONT "$(pid gw1)" ;;
      efis_kill) efis 1 ;;
      efis_stop) kill -CONT "$(pid efis1)" ;;
      can_cut) rm -f "$R/CUT" ;;
    esac
    sleep 25; grab "${mode}_3after"; sleep 5
  done
  phase "done"
  ;;
stop)
  for n in recorder feeder efis1 efis2 xvfb7 xvfb8 gw1 gw2 hub; do
    p=$(pid $n) && [ -n "$p" ] && kill -CONT "$p" 2>/dev/null && kill -TERM "$p" 2>/dev/null && echo "stopped $n"
  done
  sleep 3
  for n in recorder feeder efis1 efis2 xvfb7 xvfb8 gw1 gw2 hub; do
    p=$(pid $n) && [ -n "$p" ] && kill -KILL "$p" 2>/dev/null && echo "killed $n"
  done
  rm -f "$R"/*.pid
  ;;
esac
