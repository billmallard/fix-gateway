# AER-6622: press-to-FIX-write latency through the real Tessera plugin.
# Writes "AD_PRESS <id>" to the panel's port from a second writer (a plain
# os.open, no termios or modem-line change).  The firmware queues it exactly as
# a touch tile's click (deck_model_record_press -> record_event), sends the
# deck_event to fixgw's plugin, and the plugin pulses the key's bool.  It times
# from the AD_PRESS write to the netfix update of that bool, which is what pyEfis sees.
# Excluded: the touch controller scan and the finger lift (LVGL CLICKED).
import os, socket, statistics, sys, time

KEY_ID, BOOL, N = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
RESTORE = sys.argv[4] if len(sys.argv) > 4 else None  # a key to put back after
# "any": time to any update of BOOL (a key pyEfis writes in answer, e.g. HEADBUG)
ANY = len(sys.argv) > 5 and sys.argv[5] == "any"
s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")


def ask(cmd, prefix):
    s.sendall(cmd.encode() + b"\n")
    while True:
        line = f.readline().decode().strip()
        if line.startswith(prefix):
            return line


orig = ask("@r" + RESTORE, "@r" + RESTORE + ";").split(";")[1] if RESTORE else None
ask("@s" + BOOL, "@s" + BOOL)
port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
s.settimeout(2.0)
lat = []
misses = 0
for i in range(N):
    t0 = time.monotonic()
    os.write(port, "AD_PRESS {}\n".format(KEY_ID).encode())
    got = None
    try:
        while got is None:
            line = f.readline().decode().strip()
            if line.startswith(BOOL + ";") and (
                ANY or line.split(";")[1] in ("True", "1")
            ):
                got = time.monotonic()
    except socket.timeout:
        misses += 1
    if got:
        lat.append((got - t0) * 1000)
    time.sleep(0.8)
os.close(port)
s.settimeout(None)
if RESTORE:
    print("after:", ask("@r" + RESTORE, "@r" + RESTORE + ";"))
ask("@u" + BOOL, "@u" + BOOL)
if RESTORE:
    s.sendall("@w{};{}\n".format(RESTORE, orig).encode())
    time.sleep(0.3)
    print("restored", ask("@r" + RESTORE, "@r" + RESTORE + ";"), "(was", orig + ")")
s.close()
lat.sort()
print("presses %d, timed %d, missed %d" % (N, len(lat), misses))
if lat:
    print(
        "ms: min %.1f  median %.1f  p90 %.1f  max %.1f  mean %.1f"
        % (
            lat[0],
            statistics.median(lat),
            lat[int(0.9 * (len(lat) - 1))],
            lat[-1],
            statistics.mean(lat),
        )
    )
    print("all:", " ".join("%.1f" % x for x in lat))
