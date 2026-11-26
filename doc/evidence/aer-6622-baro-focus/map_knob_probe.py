# AER-6622: the MAP RANGE key gives the Knobster to the map; the inner knob
# zooms smoothly, the push snaps to a rung, the outer ring steps the ladder.
#
# As pyefis on the Beelink, both AER-6622 branches staged, the live map bound
# to MAPRANGE (ladder 2,10,50,200,500,2000) and at 10 NM:
#   flock -w 900 /tmp/pyefis-bench.lock python3 -u map_knob_probe.py <outdir>
# Knob writes go to the bus as the Knobster plugin writes them (ENC3 inner,
# ENC4 outer, BTN3 push; connections/knobster.yaml). A frame is grabbed
# after each step; the range is read from the map's status text in them.
import os, socket, subprocess, sys, time

out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)
env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")


def grab(name):
    time.sleep(0.8)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab",
                    "-draw_mouse", "0", "-video_size", "1920x1080", "-i",
                    env["DISPLAY"], "-frames:v", "1",
                    os.path.join(out, "frame-%s.png" % name)], env=env, check=True)
    print("frame", name, "MAPRANGE", read("MAPRANGE"), flush=True)


def read(key):
    s.sendall(("@r%s\n" % key).encode())
    while True:
        line = f.readline().decode().strip()
        if line.startswith("@r%s;" % key):
            return line.split(";")[1]


def write(key, value):
    s.sendall(("%s;%s;0\n" % (key, value)).encode())
    time.sleep(0.3)


grab("0-before")
port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
os.write(port, b"AD_PRESS 10\n")
os.close(port)
grab("1-focused")
for n in range(3):
    write("ENC3", 1)
grab("2-inner-x3")
write("BTN3", 1)
write("BTN3", 0)
grab("3-push")
write("ENC4", 1)
grab("4-outer-up")
write("ENC4", -1)
grab("5-outer-down")
time.sleep(11)
grab("6-timed-out")
s.close()
