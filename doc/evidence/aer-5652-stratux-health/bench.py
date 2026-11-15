# GDL4 bench run (AER-5652): the branch's stratux plugin, receiving the
# FlightBox's GDL90 on UDP 4000 with source 10.110.10.169, for --seconds.
# Counts 0x4C AHRS messages per second (each writes PITCH) and logs the STX
# keys once a second as JSON lines.  --status on|off sets the poll.
import argparse, json, logging, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import fixgw.database as database
import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import status as sx

ap = argparse.ArgumentParser()
ap.add_argument("--status", choices=["on", "off"], required=True)
ap.add_argument("--seconds", type=int, default=60)
ap.add_argument("--out", required=True)
a = ap.parse_args()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
database.init(os.path.join(os.path.dirname(os.path.abspath(__file__)), "src/fixgw/config/database.yaml"))
ahrs = [0]
database.callback_add("bench", "PITCH", lambda *x: ahrs.__setitem__(0, ahrs[0] + 1), None)
p = stratux.Plugin("stratux", {"host": "", "port": 4000, "source": "10.110.10.169",
                               "traffic": False, "status": a.status == "on"}, {})
p.run()
t0 = time.monotonic()
with open(a.out, "w") as f:
    for i in range(1, a.seconds + 1):
        time.sleep(max(0, t0 + i - time.monotonic()))
        n, ahrs[0] = ahrs[0], 0
        row = {"t": round(time.time(), 2), "ahrs_per_s": n}
        for k in sx.KEYS:
            v = database.read(k)
            row[k] = [round(v[0], 2) if isinstance(v[0], float) else v[0], "old" if v[2] else "", "bad" if v[3] else ""]
        f.write(json.dumps(row) + "\n"); f.flush()
p.stop()
print("status", dict(p.get_status()))
