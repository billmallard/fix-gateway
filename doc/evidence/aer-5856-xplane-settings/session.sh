# AER-5856 (AFC2b) sim-bench hooks for sim_follow.py.  sh session.sh start|stop
# start: a PRIVATE fixgw (the AFC2b branch, config from mkconfig.py, udp_in
#        49102, netfix 127.0.0.1:3592) and seq.py, which writes the pilot's
#        settings into it and reads X-Plane's dials on its own RREF socket
#        (49103). Nothing here touches the live fixgw, pyEfis, their config
#        trees or the bench lock.
# stop:  tell seq.py to stop, then stop both.
D=$(cd "$(dirname "$0")" && pwd)
PY=$HOME/pyefis-venv/bin/python
FGW=$HOME/bench-scratch/aer5856/fgw
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$D/session.log"; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

case "$1" in
start)
    say "start: fgw $(cd "$FGW" && git log --oneline -1)"
    if alive "$D/fixgw.pid"; then say "private fixgw already running"; exit 0; fi
    rm -f "$D/STOP"
    setsid sh -c "echo \$\$ > '$D/fixgw.pid'; cd '$D'; exec env PYTHONPATH='$FGW/src' '$PY' -c 'import sys; from fixgw.server import main; sys.exit(main())' --config-file '$D/config/default.yaml'" \
        > "$D/fixgw.log" 2>&1 < /dev/null &
    sleep 4
    if ! alive "$D/fixgw.pid"; then say "private fixgw did not start; NOT driving"; tail -5 "$D/fixgw.log" >> "$D/session.log"; exit 0; fi
    setsid sh -c "echo \$\$ > '$D/seq.pid'; exec '$PY' '$D/seq.py' '$D' --port 3592 --xp 10.110.10.167 --xp-port 49000 --truth-port 49103" \
        > "$D/seq.out" 2>&1 < /dev/null &
    sleep 1
    say "fixgw pid $(cat "$D/fixgw.pid"), seq pid $(cat "$D/seq.pid")"
    ;;
stop)
    touch "$D/STOP"
    sleep 4
    for p in seq fixgw; do
        if alive "$D/$p.pid"; then
            kill -TERM "$(cat "$D/$p.pid")" 2>/dev/null
            sleep 3
            alive "$D/$p.pid" && kill -KILL "$(cat "$D/$p.pid")" 2>/dev/null
        fi
        say "$p stopped: $(alive "$D/$p.pid" && echo NO || echo yes)"
    done
    say "stop: done"
    ;;
esac
