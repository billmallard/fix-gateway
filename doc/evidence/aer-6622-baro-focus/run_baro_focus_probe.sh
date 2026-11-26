#!/bin/sh
# AER-6622: run baro_focus_probe.py on the Beelink, recording the glass.
# Usage, as pyefis on the bench:
#   flock -w 900 /tmp/pyefis-bench.lock sh run_baro_focus_probe.sh <outdir>
set -u
OUT=${1:-.}
mkdir -p "$OUT"
export DISPLAY="${DISPLAY:-:0}"
G=$(xdotool getdisplaygeometry | tr ' ' x)
ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -framerate 10 -video_size "$G" -i "$DISPLAY" \
    -t 32 -c:v libx264 -pix_fmt yuv420p -preset veryfast "$OUT/clip.mp4" &
FF=$!
sleep 1
python3 -u "$(dirname "$0")/baro_focus_probe.py" > "$OUT/probe.fifo.log" 2>&1 &
PY=$!
seen=""
while kill -0 $PY 2>/dev/null; do
    for n in $(grep '^MARK' "$OUT/probe.fifo.log" | cut -d' ' -f2); do
        case " $seen " in *" $n "*) continue ;; esac
        seen="$seen $n"
        ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "$G" -i "$DISPLAY" \
            -frames:v 1 "$OUT/frame-$n.png"
    done
    sleep 0.1
done
wait $PY; rc=$?
wait $FF
mv "$OUT/probe.fifo.log" "$OUT/probe.log"
cat "$OUT/probe.log"
exit $rc
