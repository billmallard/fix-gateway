# Receiver health and GPS quality from the Stratux JSON status feed (GDL4,
# AER-5652).
#
# GDL90 carries neither.  The receiver's web server answers `/getStatus` with
# its `globalStatus` struct as JSON (Stratux main/gen_gdl90.go `type status`,
# served by main/managementinterface.go handleStatusRequest; checked against
# the v1.6r1-eu023 source the bench FlightBox runs).  This module polls it on
# its own thread, so a slow or dead web server never holds up the UDP decode
# loop, and writes the STX* keys (config/database/stratux.yaml).
#
# Rules:
#   - The poll goes only to the plugin's `source` address: the same address
#     rule as the UDP feed.  With `source` empty or invalid there is nothing
#     to poll, and the keys are never written.
#   - A failed poll (refused, timed out, not JSON, not a status object) takes
#     every key old at once.  A poll that hangs writes nothing, and the keys'
#     tol takes them old.
#   - A Stratux "no data" value is `bad`, never a number: the 999999 m
#     accuracy isGPSValid() sets without a fix, the -99 C CPU temperature
#     before the first reading, and any other value outside what the field
#     can mean.  Nothing is left for the database to clamp: a clamped
#     sentinel reads as a plausible value.
#   - After a failure the next poll waits BACKOFF_S: the Pi 2 web API times
#     out under load, and polling it at 1 Hz while it struggles adds to it.

import json
import threading
import urllib.request

POLL_S = 1.0
TIMEOUT_S = 0.8
BACKOFF_S = 5.0

M_TO_FT = 1 / 0.3048

# GPS_solution (gen_gdl90.go updateStatus) to the GPS_FIX_TYPE scale in
# database/ahrs.yaml, so a later source select can copy it without a table:
# 0 no GPS, 1 no fix, 3 3D, 4 3D with differential (SBAS).  Dead reckoning
# is not a GNSS fix.  The SBAS string differs by line: "GPS + SBAS (WAAS)" in
# cyoung/stratux up to v1.6r1, "3D GPS + SBAS" in the eu builds and the
# current stratux/stratux.  A string not listed here ("Unknown", or one a
# later Stratux adds) is bad.
FIX_TYPE = {
    "Disconnected": 0,
    "No Fix": 1,
    "Dead Reckoning": 1,
    "3D GPS": 3,
    "3D GPS + SBAS": 4,
    "GPS + SBAS (WAAS)": 4,
}

# Stratux sets 999999 m when it has no valid fix (gps.go isGPSValid).  A real
# 95 % horizontal accuracy from HDOP is metres to a few hundred metres.
ACCURACY_NO_DATA_M = 99999.0
# The STXSATS/STXSATSV range and the STXCPUT range in database/stratux.yaml.
MAX_SATS = 200
MAX_CPU_C = 150.0

KEYS = (
    "STXALIVE",
    "STXGPS",
    "STXAHRS",
    "STXBARO",
    "STXGPSFIX",
    "STXSATS",
    "STXSATSV",
    "STXGPSACC",
    "STXCPUT",
)


def _number(status, name):
    value = status.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def decode(status):
    """The key writes for one /getStatus object: {key: (value, bad)}.

    Raises ValueError if it is not a status object at all, which the poller
    treats as a failed poll.
    """
    if not isinstance(status, dict) or "GPS_solution" not in status:
        raise ValueError("not a Stratux status object")
    out = {}
    for key, name in (
        ("STXGPS", "GPS_connected"),
        ("STXAHRS", "IMUConnected"),
        ("STXBARO", "BMPConnected"),
    ):
        value = status.get(name)
        out[key] = (bool(value), not isinstance(value, bool))

    solution = status.get("GPS_solution")
    fix = FIX_TYPE.get(solution)
    out["STXGPSFIX"] = (0 if fix is None else fix, fix is None)
    has_fix = fix is not None and fix >= 3

    for key, name in (("STXSATS", "GPS_satellites_locked"), ("STXSATSV", "GPS_satellites_seen")):
        value = _number(status, name)
        if value is None or not 0 <= value <= MAX_SATS:
            out[key] = (0, True)
        else:
            out[key] = (int(value), False)

    acc = _number(status, "GPS_position_accuracy")
    if acc is None or not has_fix or not 0 < acc < ACCURACY_NO_DATA_M:
        out["STXGPSACC"] = (0.0, True)
    else:
        out["STXGPSACC"] = (acc * M_TO_FT, False)

    # cputemp.go: invalidCpuTemp = -99, isCPUTempValid is t > 0.
    temp = _number(status, "CPUTemp")
    if temp is None or not 0 < temp < MAX_CPU_C:
        out["STXCPUT"] = (0.0, True)
    else:
        out["STXCPUT"] = (float(temp), False)

    out["STXALIVE"] = (True, False)
    return out


def fetch_status(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.loads(reply.read().decode("utf-8"))


class StatusPoller(threading.Thread):
    def __init__(self, get_item, log, host, status, fetch=None):
        super().__init__(name="stratux-status", daemon=True)
        self.log = log
        self.url = "http://%s/getStatus" % host
        self.status = status
        self.fetch = fetch
        self.items = {key: get_item(key) for key in KEYS}
        self.stop_event = threading.Event()
        self.failing = False
        self.polls = 0
        self.failures = 0

    def run(self):
        while not self.stop_event.is_set():
            ok = self.poll_once()
            self.stop_event.wait(POLL_S if ok else BACKOFF_S)

    def poll_once(self):
        self.polls += 1
        try:
            get = self.fetch if self.fetch is not None else fetch_status
            writes = decode(get(self.url, TIMEOUT_S))
        except Exception as e:
            self.fail(e)
            return False
        for key, (value, bad) in writes.items():
            self.items[key].set(value, old=False, bad=bad)
        if self.failing:
            self.log.info("status poll of %s recovered" % self.url)
            self.failing = False
        self.status["Status polls"] = self.polls
        return True

    def fail(self, e):
        self.failures += 1
        self.status["Status poll failures"] = self.failures
        if not self.failing:
            self.log.warning("status poll of %s failed: %s" % (self.url, e))
            self.failing = True
        self.items["STXALIVE"].set(False, old=True)
        for key in KEYS:
            if key != "STXALIVE":
                self.items[key].old = True

    def stop(self):
        self.stop_event.set()
