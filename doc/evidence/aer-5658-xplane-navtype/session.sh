# AER-5658 sim-bench hooks for sim_follow.py.  sh session.sh start|stop
# start: deploy fix-gateway dev (#178), add the NAV1TYPE/NAV2TYPE rows to the
#        hand-edited xplane.yaml (they stay: shipped behaviour), decouple the
#        XPHSISRC write for this session only, restart, launch drive.py.
# stop:  stop drive.py, re-couple XPHSISRC, restart. The NAV rows stay.
# Selecting NAVSRC=0 with XPHSISRC coupled would switch X-Plane's HSI source
# to NAV1 and pull the pilotless driver's autopilot off GPS.
D=$(cd "$(dirname "$0")" && pwd)
Y=$HOME/makerplane/fixgw/config/connections/xplane.yaml
LOCK=/tmp/pyefis-bench.lock
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$D/session.log"; }
locked() { flock -w 180 $LOCK sh "$D/session.sh" "$@"; }

case "$1" in
start)
    # No deploy here: night 1's deploy waited 14 min on the bench lock and the
    # hook hit sim_follow's 900 s timeout. The bench already runs #178.
    say "start: fix-gateway $(cd ~/src/fix-gateway && git log --oneline -1)"
    if ! locked _edit || ! grep -q '^        # AER-5658-TEMP XPHSISRC:' "$Y"; then
        say "decouple did not land; NOT driving (NAVSRC=0 would steer the driver)"
        exit 0
    fi
    say "xplane.yaml now: $(grep -n 'NAV.TYPE:\|XPHSISRC' "$Y" | tr '\n' '|')"
    locked _restart
    setsid sh -c "echo \$\$ > '$D/drive.pid'; exec flock -w 900 $LOCK $HOME/pyefis-venv/bin/python '$D/drive.py' '$D'" \
        > "$D/drive.out" 2>&1 < /dev/null &
    sleep 2
    say "drive.py pid $(cat "$D/drive.pid")"
    ;;
stop)
    if [ -f "$D/drive.pid" ] && kill -0 "$(cat "$D/drive.pid")" 2>/dev/null; then
        say "drive.py still running at session end; stopping it"
        kill -TERM "-$(cat "$D/drive.pid")" 2>/dev/null || kill "$(cat "$D/drive.pid")"
        sleep 3
    fi
    locked _recouple
    say "xplane.yaml now: $(grep -n 'NAV.TYPE:\|XPHSISRC' "$Y" | tr '\n' '|')"
    locked _restart
    say "stop: done"
    ;;
_edit)
    cp -p "$Y" "$Y.bak-aer5658-$(date -u +%Y%m%dT%H%M%SZ)"
    python3 - "$Y" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
if "NAV1TYPE:" not in s:
    anchor = next(l for l in s.splitlines(True) if l.lstrip().startswith("VOR2BRG:"))
    rows = ("        # AER-5658 (fix-gateway#178): tuned navaid type per radio. X-Plane\n"
            "        # nav_type 3 VOR, 4 ILS, 5 LOC -> 1 VOR, 2 LOC; compute selects NAVTYPE.\n"
            "        NAV1TYPE: [\"sim/cockpit/radios/nav_type[0]\", {3: 1, 4: 2, 5: 2}]\n"
            "        NAV2TYPE: [\"sim/cockpit/radios/nav_type[1]\", {3: 1, 4: 2, 5: 2}]\n")
    s = s.replace(anchor, anchor + rows, 1)
old = "        XPHSISRC: sim/cockpit2/radios/actuators/HSI_source_select_pilot\n"
if old in s:
    # Comment the key out, never write `{}`: an empty mapping makes fixgw's
    # cfg.from_yaml re-read the file and die with "Include loop detected"
    # (AER-5669). An emptied mapping loads as None and dict(None) kills the
    # plugin; an absent key falls back to cfg.get("dataref_writes", {}).
    s = s.replace("    dataref_writes:\n", "    # AER-5658-TEMP dataref_writes:\n", 1)
    s = s.replace(old, "        # AER-5658-TEMP " + old.lstrip(), 1)
open(p, "w").write(s)
PY
    ;;
_recouple)
    sed -i -e 's/^        # AER-5658-TEMP XPHSISRC:/        XPHSISRC:/' \
        -e 's/^    # AER-5658-TEMP dataref_writes:$/    dataref_writes:/' "$Y"
    ;;
_restart)
    sudo systemctl restart fixgw.service; sleep 8
    say "fixgw $(systemctl is-active fixgw.service)"
    systemctl --user restart pyefis.service; sleep 15
    say "pyefis $(systemctl --user is-active pyefis.service)"
    ;;
esac
