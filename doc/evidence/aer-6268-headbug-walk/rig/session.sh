# AER-6268 sim-bench hooks for sim_follow.py.  sh session.sh start|stop
# start: a PRIVATE fixgw (the branch under test; its own config tree,
#        udp_in 49104, netfix 127.0.0.1:3594), then seq.py.
# stop:  stop seq.py and fixgw. Nothing here touches the live fixgw,
#        pyefis.service, display :0, their config trees or the bench lock:
#        the private gateway reads X-Plane over its own RREF subscriptions.
D=$(cd "$(dirname "$0")" && pwd)
W=$HOME/bench-scratch/aer6268
PY=$HOME/pyefis-venv/bin/python
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$D/session.log"; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

case "$1" in
start)
    say "start: fgw $(git -C "$W/fgw" log --oneline -1)"
    if alive "$D/fixgw.pid"; then say "private fixgw already running"; exit 0; fi
    rm -f "$D/STOP"
    setsid sh -c "echo \$\$ > '$D/fixgw.pid'; cd '$D'; exec env PYTHONPATH='$W/fgw/src' '$PY' -c 'import sys; from fixgw.server import main; sys.exit(main())' --config-file '$W/fcfg/default.yaml'" \
        > "$D/fixgw.log" 2>&1 < /dev/null &
    sleep 5
    if ! alive "$D/fixgw.pid"; then say "private fixgw did not start; NOT driving"; tail -5 "$D/fixgw.log" >> "$D/session.log"; exit 0; fi
    setsid sh -c "echo \$\$ > '$D/seq.pid'; exec '$PY' '$W/seq.py' '$D' --port 3594 $SEQ_ARGS" \
        > "$D/seq.out" 2>&1 < /dev/null &
    sleep 1
    say "fixgw $(cat "$D/fixgw.pid"), seq $(cat "$D/seq.pid")"
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
    ;;
esac
