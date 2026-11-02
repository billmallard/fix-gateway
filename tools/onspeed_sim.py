#!/usr/bin/env python3
"""OnSpeed Gen2 ONSPEED-sentence simulator (pyEfis fixgw_onspeed_in_spec.md 7.3).

Writes ONSPEED sentences at 10 Hz to a pseudo-terminal (the default) or to a
serial port, so the fix-gateway `onspeed` plugin, and pyEfis behind it, can be
driven without an OnSpeed box.  Each sentence is formatted by the plugin's own
copy of the firmware sprintf (fixgw.plugins.onspeed.sentence.encode), the one
the tests check against the spec's vectors.

Usage (from a fix-gateway checkout, with pyserial installed for --port):
  PYTHONPATH=src python3 tools/onspeed_sim.py            # prints the pty path
  PYTHONPATH=src python3 tools/onspeed_sim.py --port /dev/ttyUSB1
  PYTHONPATH=src python3 tools/onspeed_sim.py --count 5 --stdout

Then point connections/onspeed.yaml `port:` at the printed path.

The profile repeats: a few seconds of taxi (IAS under 30 kt, AOA and percent
lift sent as zero, as the box does below its mute speed), then for each flap
setting an AOA sweep from 0 deg up past that setting's stall-warning AOA and
back.  The setpoints are made-up but ordered as OnSpeed requires
(tones-on < fast <= slow <= stall warning); they are not any aircraft's
calibration.
"""

import argparse
import math
import os
import sys
import time

from fixgw.plugins.onspeed.sentence import encode

RATE_HZ = 10

# flaps deg -> (tones-on, OnSpeed fast, OnSpeed slow, stall warning), deg AOA.
SETPOINTS = {
    0: (6.0, 9.4, 11.2, 13.5),
    20: (5.0, 8.2, 10.0, 12.4),
    40: (4.0, 7.0, 9.0, 11.5),
}
TAXI_S = 3.0
SWEEP_S = 12.0  # 0 -> warn + OVERSHOOT -> 0
OVERSHOOT = 2.0  # deg past the warning AOA at the top of each sweep


def state_at(t):
    """Raw sentence values (FIRMWARE_FORMAT order) at time t into the profile."""
    period = TAXI_S + SWEEP_S * len(SETPOINTS)
    t %= period
    flaps = 0
    if t < TAXI_S:
        ias, aoa = 18.0 + 2.0 * t, 0.0
    else:
        k, u = divmod(t - TAXI_S, SWEEP_S)
        flaps = sorted(SETPOINTS)[int(k)]
        warn = SETPOINTS[flaps][3]
        aoa = (warn + OVERSHOOT) * math.sin(math.pi * u / SWEEP_S)
        ias = max(45.0, 130.0 - 6.0 * aoa)
    tones, fast, slow, warn = SETPOINTS[flaps]
    low = ias < 25.0  # the box's shipped mute speed
    percent_lift = 0 if low else max(0, min(99, round(aoa / warn * 90)))
    flight_path = -3.0 if not low else 0.0
    pitch = (aoa + flight_path) if not low else -0.8
    return [
        round(pitch * 10),
        0,  # roll
        round(ias * 10),
        1200,  # pressure altitude, ft
        0,  # rate of turn
        0,  # lateral G
        10,  # vertical G, 0.1 g
        percent_lift,
        0 if low else round(aoa * 10),
        round(-500 / 10) if not low else 0,  # iVSI, 10 fpm
        0,  # OAT: hardcoded zero in the firmware
        round(flight_path * 10),
        flaps,
        round(warn * 10),
        round(slow * 10),
        round(fast * 10),
        round(tones * 10),
        0,  # G onset rate: hardcoded zero
        0,  # spin recovery cue: hardcoded zero
        0,  # DataMark
    ]


def sentences(count=None, dt=1.0 / RATE_HZ):
    n = 0
    while count is None or n < count:
        yield (encode(state_at(n * dt)) + "\r\n").encode("ascii")
        n += 1


def _open_output(args):
    if args.stdout:
        return sys.stdout.buffer.write, sys.stdout.flush
    if args.port:
        import serial

        port = serial.Serial(args.port, baudrate=args.baud)
        return port.write, port.flush
    import pty
    import tty

    master, slave = pty.openpty()
    tty.setraw(slave)
    print(os.ttyname(slave), flush=True)
    return (lambda b: os.write(master, b)), (lambda: None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", help="serial port to write instead of a pty")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--stdout", action="store_true", help="write to stdout")
    ap.add_argument("--count", type=int, help="stop after this many sentences")
    args = ap.parse_args(argv)
    write, flush = _open_output(args)
    start = time.monotonic()
    try:
        for i, line in enumerate(sentences(args.count)):
            # Pace against the start time, not per sleep, so it holds 10 Hz.
            delay = start + i / RATE_HZ - time.monotonic()
            if delay > 0 and not args.stdout:
                time.sleep(delay)
            write(line)
            flush()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
