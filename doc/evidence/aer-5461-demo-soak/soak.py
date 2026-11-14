#  SPDX-License-Identifier: GPL-2.0-or-later
"""Run the gateway from a config file for ``hours`` and log one JSON line
every ``every_s`` seconds: the demo plugin's status, the process's memory
and threads, what the flight-plan engine is doing, and any key with two
writers. DEMO2's unattended-loop soak (AER-5461); see README.md.

    python soak.py <config/default.yaml> <hours> <every_s> <log>
"""

import json
import sys
import threading
import time

import fixgw.database as database
import fixgw.plugin as plugin
import fixgw.server as server

KEYS = ("FPLSEQ", "FPLLDSEQ", "FPLCMDACK", "FPLMSG", "FPLSTATE", "FPLAPR", "FPLPHASE", "WPNAME", "LAT", "LONG")


def rss_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return None


def main():
    config, hours, every_s, log = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    sys.argv = ["fixGw.py", "--config-file", config]
    args = server.main_setup()
    t0 = time.monotonic()

    def sample():
        end = t0 + hours * 3600.0
        while True:
            now = time.monotonic()
            line = {"wall": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "up_s": round(now - t0),
                    "rss_kb": rss_kb(), "threads": threading.active_count(),
                    "demo": dict(server.plugins["demo"].get_status() or {}),
                    "demo_alive": server.plugins["demo"].is_running()}
            for k in KEYS:
                v = database.read(k)
                line[k] = [v[0], bool(v[3])] if k in ("LAT", "LONG") else v[0]
            line["dual"] = {k: w for k, w in database.writers().items() if len(w) > 1}
            with open(log, "a") as f:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
            if now >= end:
                plugin.jobQueue.put("QUIT")
                return
            time.sleep(min(every_s, max(1.0, end - now)))

    threading.Thread(target=sample, daemon=True).start()
    server.run(args)


if __name__ == "__main__":
    main()
