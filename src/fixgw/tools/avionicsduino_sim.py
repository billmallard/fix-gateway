#!/usr/bin/env python
#  Copyright (c) 2026 MakerPlane contributors
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

"""AvionicsDuino bus simulator.

Sends synthetic AvionicsDuino frames (AHRS V2.1, remote module V3, RDAM
V1.2), built from the layouts in fixgw.plugins.avionicsduino.protocol, onto
any python-can interface, so the avionicsduino plugin can be developed and
demonstrated with no hardware:

    python -m fixgw.tools.avionicsduino_sim --interface virtual --channel tcan0
    python -m fixgw.tools.avionicsduino_sim --without remote --duration 30
    python -m fixgw.tools.avionicsduino_sim --fuzz --duration 60

The scenario is a steady right turn at 3 deg/s, 25 deg of bank, with every
id sent at its sender's rate (20, 10 or 5 Hz).  It follows the senders'
unit quirks: roll in radians, pitch in degrees, track -180..+180, speeds in
km/h, raw magnetometer axes in uT.  --without <node> drops one node (ahrs,
remote, rdam or ems) to show its keys going old.

--fuzz sends uniformly random 11-bit frames instead; it exists to soak-test
that hostile traffic never kills the plugin thread.

These are synthetic frames, not a recording of a real installation.
"""

import argparse
import datetime
import math
import random
import time

import can

from fixgw.plugins.avionicsduino import protocol

TICK_HZ = 20.0
TURN_RATE_DPS = 3.0
BANK_DEG = 25.0
MAGVAR_DEG = -5.0  # true = magnetic - 5 here, an arbitrary east variation


def values_at(t, tick, utc=None):
    """{canid: {field: value}} for the scenario at t seconds."""
    if utc is None:
        utc = datetime.datetime(2026, 11, 10, 15, 30, 0) + datetime.timedelta(
            seconds=t
        )
    heading = (90.0 + TURN_RATE_DPS * t) % 360.0
    track = (heading + MAGVAR_DEG + 180.0) % 360.0 - 180.0
    h = math.radians(heading)
    ias = 170.0 + math.sin(t)  # km/h, so the integer steps show
    return {
        10: {"count": tick},
        20: {"roll_rad": math.radians(BANK_DEG), "pitch_deg": 2.0},
        22: {"acc_y": 0.05, "acc_z": -9.80665 / math.cos(math.radians(BANK_DEG))},
        24: {"vz_mps": 0.0, "track_deg": track},
        30: {"alt_msl_ft": 4500.0, "gs_kmh": 185.0},
        34: {
            "year": utc.year,
            "month": utc.month,
            "day": utc.day,
            "hour": utc.hour,
            "minute": utc.minute,
            "second": utc.second,
            "sats": 14,
        },
        36: {"lat_deg": 45.0 + 0.01 * math.cos(h)},
        37: {"lon_deg": -93.0 + 0.01 * math.sin(h)},
        38: {"nanosec": 0, "time_acc_ns": 25},
        28: {"heading_deg": heading},
        42: {"oat_c": 12.5},
        43: {"magx_ut": 20.0 * math.cos(h), "magy_ut": -20.0 * math.sin(h)},
        44: {"rh_pct": 60.0},
        45: {"magz_ut": 45.0},
        46: {
            "qnh_cinhg": 2992,
            "qnh_alt_ft": 4500,
            "density_alt_ft": 5200,
            "baro_vs_fpm": 0,
        },
        48: {
            "ias_kmh": int(ias + 0.5),
            "tas_kmh": int(ias * 1.07 + 0.5),
            "wind_kmh": 20,
            "wind_dir_deg": 270,
        },
        50: {"map": 24.5},
        52: {"cht2": 160.0, "cht3": 162.0},
        54: {"oil_temp": 95.0, "oil_press": 4200.0},
        56: {"egt3": 720.0, "egt4": 735.0},
        58: {"afr": 13.2},
        60: {"bus_current_a": 12.3, "battery_current_a": 1.5},
        26: {"lower_limit": 0, "upper_limit": 0, "rpm": 5200, "vbat": 13.9},
        32: {"fuel_flow": 18.5, "fuel_level": 60.0},
        33: {"total_seconds": int(t), "fuel_used": 3.2},
    }


def frames_for_tick(tick, t, without=(), utc=None):
    """[(canid, data)] due at this 20 Hz tick, each id at its own rate."""
    values = values_at(t, tick, utc)
    frames = []
    for canid, layout in protocol.LAYOUTS.items():
        if layout.node in without:
            continue
        rate = layout.rate_hz or 5
        if tick % int(TICK_HZ // rate):
            continue
        frames.append((canid, protocol.encode(canid, values[canid])))
    return frames


def fuzz_frame(rng):
    canid = rng.randrange(0, protocol.MAX_CANID + 1)
    data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
    return canid, data


def main():
    nodes = sorted({layout.node for layout in protocol.LAYOUTS.values()})
    parser = argparse.ArgumentParser(
        description="Send synthetic AvionicsDuino frames onto a CAN bus"
    )
    parser.add_argument("--interface", default="virtual", help="python-can interface")
    parser.add_argument("--channel", default="tcan0", help="python-can channel")
    parser.add_argument(
        "--duration", type=float, default=0.0, help="seconds to run (0 = forever)"
    )
    parser.add_argument(
        "--without",
        action="append",
        default=[],
        choices=nodes,
        help="leave one node's frames out (repeatable)",
    )
    parser.add_argument(
        "--fuzz", action="store_true", help="send random frames instead"
    )
    parser.add_argument("--seed", type=int, default=None, help="fuzz RNG seed")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    bus = can.Bus(args.channel, interface=args.interface)
    start = time.monotonic()
    tick = 0
    sent = 0
    try:
        while True:
            t = time.monotonic() - start
            if args.fuzz:
                frames = [fuzz_frame(rng)]
            else:
                frames = frames_for_tick(tick, t, args.without)
            for canid, data in frames:
                bus.send(can.Message(arbitration_id=canid, data=data, is_extended_id=False))
                sent += 1
            tick += 1
            time.sleep(max(0.0, start + tick / TICK_HZ - time.monotonic()))
            if args.duration and (time.monotonic() - start) >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
    print("sent {} frames in {:.1f}s".format(sent, time.monotonic() - start))


if __name__ == "__main__":
    main()
