# AER-6622: live-value refresh rate of the Tessera panel through fixgw.
# Writes BARO at RATE Hz for SECS s over a raw netfix socket, and counts the
# plugin's page pushes and the panel's AD_CFG_END OK answers (its status
# counters) across the window.  Restores BARO to the value read first.
# Raw socket on purpose: netfix Client.stop() sends @xkill and stops fixgw.
import json, socket, sys, time

RATE, SECS = float(sys.argv[1]), float(sys.argv[2])
s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")


def ask(cmd, prefix):
    s.sendall(cmd.encode() + b"\n")
    while True:
        line = f.readline().decode().strip()
        if line.startswith(prefix):
            return line


def tessera():
    d = json.loads(ask("@xstatus", "@xstatus;")[len("@xstatus;") :])

    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "Connection: tessera":
                    return v
                r = find(v)
                if r:
                    return r

    return find(d)


orig = float(ask("@rBARO", "@rBARO;").split(";")[1])
print("BARO before:", orig)
a = tessera()
t0 = time.monotonic()
n = 0
while time.monotonic() - t0 < SECS:
    s.sendall("@wBARO;{:.2f}\n".format(29.00 + (n % 90) * 0.01).encode())
    n += 1
    time.sleep(max(0, t0 + n / RATE - time.monotonic()))
t1 = time.monotonic()
time.sleep(1.0)
b = tessera()
s.sendall("@wBARO;{:.2f}\n".format(orig).encode())
time.sleep(0.3)
print("BARO restored:", ask("@rBARO", "@rBARO;"))
s.close()
el = t1 - t0
for k in ("Pushes", "Acks", "Resends", "Invalid", "Presses", "Restarts"):
    print("%-8s %5d  (%.2f /s)" % (k, b[k] - a[k], (b[k] - a[k]) / el))
print(
    "writes %d over %.2f s (%.1f Hz); connected %s; last error %r"
    % (n, el, n / el, b["Connected"], b["Last Error"])
)
