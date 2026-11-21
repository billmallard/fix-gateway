#!/bin/sh
# AER-6243: the sim-session hooks for sim_follow.py.  Touches nothing live.
#   sh night.sh start   on-start: returns at once; the run goes on detached
#   sh night.sh end     on-end: stop whatever is still running
# The detached run: start both lanes, wait for X-Plane on lane 2 (10 s of fresh PITCH),
# let the flight settle 60 s, kill lane 1 five ways, judge it, stop.
S=$HOME/bench-scratch/aer6243
H=$(dirname "$(readlink -f "$0")")
case "$1" in
start)
  setsid sh "$H/night.sh" detached > "$S/night.out" 2>&1 < /dev/null &
  echo "night run detached"
  ;;
detached)
  cd "$S" || exit 1
  [ -d run ] && mv run "run.before.$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir run
  sh "$H/lanes.sh" start
  python3 - <<'PY' || { echo "no X-Plane on lane 2"; sh "$H/lanes.sh" stop; exit 1; }
import socket, sys, time
deadline, fresh = time.time() + 600, 0
while time.time() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", 3498), timeout=3) as s:
            f = s.makefile("rb")
            while time.time() < deadline:
                s.sendall(b"@rPITCH\n")
                line = f.readline().decode()
                ok = line.startswith("@rPITCH;") and line.strip().endswith(";00000")
                fresh = fresh + 1 if ok else 0
                if fresh >= 10:
                    print("X-Plane live on lane 2:", line.strip()); sys.exit(0)
                time.sleep(1)
    except OSError:
        time.sleep(2)
sys.exit(1)
PY
  sleep 60
  sh "$H/lanes.sh" run
  "$HOME/pyefis-venv/bin/python" "$H/analyze.py" "$S/run"
  sh "$H/lanes.sh" stop
  touch "$S/run/NIGHT_DONE"
  ;;
end)
  sh "$H/lanes.sh" stop
  ;;
esac
