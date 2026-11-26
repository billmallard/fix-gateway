# AER-6704 (MENU1b): the Tessera MENU gear opens pyEfis's system menu.
# Writes "AD_PRESS 18" to the panel's port from a second writer (a plain
# os.open, no termios or modem-line change), as press_probe.py does for
# AER-6622: the firmware queues it exactly as a touch on key 18
# (deck_model_record_press -> record_event), fixgw's tessera plugin pulses
# SYSMENU, and pyEfis opens its menu on the rising edge.  Logs every SYSMENU
# update netfix sends, and grabs the Beelink glass with x11grab (not SIGUSR1,
# which blanks the GL attitude panel) before, while open, after a second press
# while open, and after the menu's 10 s idle close.
import os, select, socket, subprocess, sys, time

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/bench-scratch/aer-6704")
os.makedirs(OUT, exist_ok=True)
env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
geom = subprocess.check_output(["xdotool", "getdisplaygeometry"], env=env).decode().split()
T0 = time.monotonic()


def stamp():
    return "%6.3f" % (time.monotonic() - T0)


def frame(name):
    path = os.path.join(OUT, name + ".png")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "0",
         "-video_size", "%sx%s" % tuple(geom), "-i", env["DISPLAY"], "-frames:v", "1", path],
        env=env, check=True)
    print(stamp(), "frame", path, flush=True)


s = socket.create_connection(("127.0.0.1", 3490), 5)
buf = b""


def readline(timeout):
    """One netfix line, or None after `timeout` seconds (select, so a
    timeout never breaks the socket)."""
    global buf
    end = time.monotonic() + timeout
    while b"\n" not in buf:
        left = end - time.monotonic()
        if left <= 0 or not select.select([s], [], [], left)[0]:
            return None
        chunk = s.recv(4096)
        if not chunk:
            return None
        buf += chunk
    line, buf = buf.split(b"\n", 1)
    return line.decode().strip()


def ask(cmd, prefix):
    s.sendall(cmd.encode() + b"\n")
    while True:
        line = readline(5.0)
        if line is None:
            raise RuntimeError("no answer to " + cmd)
        if line.startswith(prefix):
            return line
        if line.startswith("SYSMENU;"):
            print(stamp(), "netfix", line, flush=True)


def drain(seconds):
    """Print every SYSMENU update netfix sends for `seconds`."""
    end = time.monotonic() + seconds
    while True:
        line = readline(max(0.0, end - time.monotonic()))
        if line is None:
            return
        if line.startswith("SYSMENU;"):
            print(stamp(), "netfix", line, flush=True)


status = ask("@xstatus", "@xstatus")
i = status.find("tessera")
print("tessera status:", status[i:i + 400] if i >= 0 else "(no tessera in status)")
print("SYSMENU before:", ask("@rSYSMENU", "@rSYSMENU;"))
ask("@sSYSMENU", "@sSYSMENU")
frame("m0_before")
port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
print(stamp(), "AD_PRESS 18 (first)", flush=True)
os.write(port, b"AD_PRESS 18\n")
drain(1.5)
frame("m1_open")
drain(2.0)
print(stamp(), "AD_PRESS 18 (second, menu open)", flush=True)
os.write(port, b"AD_PRESS 18\n")
drain(1.5)
frame("m2_second_press_while_open")
os.close(port)
drain(9.0)  # past the 10 s idle close from the first press
frame("m3_after_idle_close")
ask("@uSYSMENU", "@uSYSMENU")
print("SYSMENU after:", ask("@rSYSMENU", "@rSYSMENU;"))
s.close()
