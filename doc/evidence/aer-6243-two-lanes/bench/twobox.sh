#!/bin/sh
# AER-6243 two-box run (Bill's one-night exception, 2026-11-22: X-Plane on a private lane on
# the Pi 5, bus over Ethernet).  Lane 1 is on the Pi (lane1d.py drives it), lane 2 here on the
# Beelink with its pyEfis on Xvfb :9.  The bus is bridge.py on each host, UDP 47650 <-> 47651.
# Neither host's own gateway, pyEfis or sensor rig is touched.
#
#   sh twobox.sh setup FGW_SHA PYEFIS_SHA   clone the trees here
#   sh twobox.sh start | run | stop
#   sh twobox.sh feed                       dry run only, when X-Plane is not flying
#   sh twobox.sh night                      sim_follow on-start: detach the whole run
#   sh twobox.sh detached                   wait for X-Plane, settle, run, judge, stop
S=$HOME/bench-scratch/aer6243-2box
R=$S/run
PI=10.110.10.199
PY=$HOME/pyefis-venv/bin/python
H=$(dirname "$(readlink -f "$0")")

spawn() {
  n=$1; shift
  setsid sh -c 'echo $$ > "$0"; exec "$@"' "$R/$n.pid" "$@" > "$R/$n.log" 2>&1 < /dev/null &
  sleep 0.3
}
pid() { cat "$R/$1.pid" 2>/dev/null; }
alive() { p=$(pid "$1") && [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
pi() { # pi CMD: one command to lane1d.py on the Pi; prints its reply
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(15)
s.sendto(sys.argv[1].encode(), ("10.110.10.199", 47660))
try:
    print(s.recvfrom(512)[0].decode())
except socket.timeout:
    print("no reply from lane1d for", sys.argv[1])
PY
  echo "$(date -u +%H:%M:%S.%N | cut -c1-12) pi $1" >> "$R/run.log"
}
phase() { echo "$1" > "$R/phase"; echo "$(date -u +%H:%M:%S.%N | cut -c1-12) $1" >> "$R/run.log"; }
grab() {
  DISPLAY=:9 ffmpeg -loglevel error -y -f x11grab -video_size 1920x1080 -i :9 -frames:v 1 "$R/${1}_lane2.png"
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
  if ss -ltn | grep -q ':3498 '; then echo "port 3498 busy: the one-box run is still up"; exit 1; fi
  mkdir -p "$R"; rm -f "$R/CUT" "$R/phase" "$R/bridge.json"
  spawn bridge python3 "$H/bridge.py" "$R" 47650 "$PI" 47651
  sleep 1
  "$PY" "$H/build.py" "$S" 2 || exit 1
  spawn gw2 env PYTHONPATH="$S/fgw/src" "$PY" -c \
    "import sys; sys.argv=['fixgw','--config-file','$S/lane2/lane.yaml']; from fixgw.server import main; main()"
  spawn xvfb9 Xvfb :9 -screen 0 1920x1080x24 -nolisten tcp
  sleep 3
  spawn efis2 env DISPLAY=:9 QT_QPA_PLATFORM=xcb PYTHONPATH="$S/pyefis/src" "$PY" -c \
    "import sys; sys.argv=['pyefis','--config-file','$S/efis2/default.yaml']; from pyefis.main import main; main()"
  # Lane 1's netfix is on the Pi's loopback; lane1d records it there.  Port 1 here is a stand-in.
  spawn recorder python3 "$H/recorder.py" "$R" 1 3498
  pi start
  for n in bridge gw2 xvfb9 efis2 recorder; do echo "$n $(pid $n) $(alive $n && echo up || echo DOWN)"; done
  ;;
feed)
  pi feed_on
  spawn feeder python3 -c '
import math, socket, time
t0 = time.time()
while True:
    try:
        s = socket.create_connection(("127.0.0.1", 3498), timeout=2)
        while True:
            t = time.time() - t0
            s.sendall(("PITCH;%.2f\nROLL;%.2f\nIAS;%.1f\nALT;%.0f\n" % (
                2 + 3 * math.sin(t / 7), 15 * math.sin(t / 11),
                110 + 5 * math.sin(t / 13), 4500 + 200 * math.sin(t / 17))).encode())
            time.sleep(0.1)
    except OSError:
        time.sleep(0.5)
'
  ;;
run)
  for mode in host_drop gw_kill link_cut; do
    phase "$mode:before"; sleep 15; grab "${mode}_1before"; sleep 5
    phase "$mode:fault"
    case $mode in
      host_drop) pi host_drop ;;
      gw_kill) pi gw_kill ;;
      link_cut) touch "$R/CUT" ;;
    esac
    sleep 10; grab "${mode}_2during"; sleep 10
    phase "$mode:recover"
    case $mode in
      host_drop|gw_kill) pi restart ;;
      link_cut) rm -f "$R/CUT" ;;
    esac
    sleep 25; grab "${mode}_3after"; sleep 5
  done
  phase "done"
  ;;
night)
  setsid sh "$H/twobox.sh" detached > "$S/night.out" 2>&1 < /dev/null &
  echo "two-box run detached"
  ;;
detached)
  cd "$S" || exit 1
  [ -d run ] && mv run "run.before.$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir run
  sh "$H/twobox.sh" start || exit 1
  python3 - <<'PY' || { echo "no X-Plane on lane 2"; sh "$H/twobox.sh" stop; exit 1; }
import socket, sys, time
deadline, fresh = time.time() + 600, 0
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", 3498), timeout=3) as s:
            f = s.makefile("rb")
            while time.time() < deadline:
                s.sendall(b"@rPITCH\n")
                line = f.readline().decode()
                fresh = fresh + 1 if line.startswith("@rPITCH;") and line.strip().endswith(";00000") else 0
                if fresh >= 10:
                    print("X-Plane live on lane 2:", line.strip()); sys.exit(0)
                time.sleep(1)
    except OSError:
        time.sleep(2)
sys.exit(1)
PY
  sleep 60
  sh "$H/twobox.sh" run
  "$PY" "$H/analyze.py" "$R"
  sh "$H/twobox.sh" stop
  touch "$R/NIGHT_DONE"
  ;;
end)
  sh "$H/twobox.sh" stop
  ;;
stop)
  pi stop
  for n in recorder feeder efis2 xvfb9 gw2 bridge; do
    p=$(pid $n) && [ -n "$p" ] && kill -CONT "$p" 2>/dev/null && kill -TERM "$p" 2>/dev/null && echo "stopped $n"
  done
  sleep 3
  for n in recorder feeder efis2 xvfb9 gw2 bridge; do
    p=$(pid $n) && [ -n "$p" ] && kill -KILL "$p" 2>/dev/null && echo "killed $n"
  done
  rm -f "$R"/*.pid
  ;;
esac
