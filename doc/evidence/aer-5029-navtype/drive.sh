# Drives NAVSRC and NAV1TYPE/NAV2TYPE over netfix on the bench and grabs the
# glass after each step. Run under the bench lock; restores NAVSRC at the end.
set -e
export DISPLAY=:0
D=~/bench-scratch/aer5029; mkdir -p $D
~/pyefis-venv/bin/python - "$D" <<'PY'
import fixgw.netfix as n, time, subprocess, sys
D=sys.argv[1]
c=n.Client("127.0.0.1",3490); c.connect(); time.sleep(0.5)
orig=c.read("NAVSRC")[1]
print("orig NAVSRC", orig)
steps=[("gps",{"NAVSRC":2}),
       ("nav1_unknown",{"NAVSRC":0,"NAV1TYPE":0}),
       ("nav1_vor",{"NAV1TYPE":1}),
       ("nav1_loc",{"NAV1TYPE":2}),
       ("nav2_loc",{"NAV2TYPE":2,"NAVSRC":1}),
       ("nav2_vor",{"NAV2TYPE":1})]
for name,w in steps:
    for k,v in w.items(): c.write(k,v)
    time.sleep(1.2)
    print(name, {k:c.read(k)[1:] for k in ("NAVSRC","NAV1TYPE","NAV2TYPE","NAVTYPE")})
    subprocess.run(["sh","-c",f"ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size 1920x1080 -i :0 -frames:v 1 {D}/{name}.png"],check=True)
c.write("NAVSRC", float(orig)); time.sleep(0.5)
print("restored NAVSRC", c.read("NAVSRC"))
c.disconnect()
PY
ls -la $D
