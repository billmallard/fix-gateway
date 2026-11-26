#!/bin/sh
# AER-6630: a real touch (xdotool click) on the HSI HDG readout, on dev.
OUT=$1; mkdir -p "$OUT"
export DISPLAY=:0
G=$(xdotool getdisplaygeometry | tr ' ' x)
f() { ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "$G" -i :0 -frames:v 1 "$OUT/$1.png"; }
f before
xdotool mousemove 566 678 click 1
sleep 1.5; f touched
sleep 12; f timed_out
xdotool mousemove 1900 1070
