#!/bin/sh
# AER-6622: the panel's RANGE + and - keys step the map range on the glass.
# As pyefis on the Beelink, both AER-6622 branches staged, and the live
# screen's moving_map carrying `range_key: MAPRANGE`:
#   flock -w 900 /tmp/pyefis-bench.lock sh map_range_probe.sh <outdir>
# Presses key 12 (+) twice, then key 11 (-) twice, with the firmware's own
# AD_PRESS.  After each press it prints MAPRANGE and grabs the screen.
set -u
OUT=${1:-.}
mkdir -p "$OUT"
export DISPLAY="${DISPLAY:-:0}"
G=$(xdotool getdisplaygeometry | tr ' ' x)
rd() {
    python3 - <<'PY'
import socket
s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")
s.sendall(b"@rMAPRANGE\n")
while True:
    l = f.readline().decode().strip()
    if l.startswith("@rMAPRANGE;"):
        print(l.split(";")[1])
        break
s.close()
PY
}
grab() { ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "$G" -i "$DISPLAY" -frames:v 1 "$OUT/frame-$1.png"; }
press() { printf 'AD_PRESS %s\n' "$1" > /dev/tessera; sleep 1.5; }
echo "start MAPRANGE $(rd)"; grab 0-start
n=1
for k in 12 12 11 11; do
    press $k
    echo "press key $k: MAPRANGE $(rd)"; grab "$n-key$k"
    n=$((n + 1))
done
