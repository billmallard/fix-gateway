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

"""DroneCAN bus simulator.

Sends DroneCAN broadcasts onto any python-can interface so the dronecan
plugin can be developed and demonstrated with no hardware:

    python -m fixgw.tools.dronecan_sim --interface virtual --channel tdc0
    python -m fixgw.tools.dronecan_sim --script myscript.yaml --rate 20
    python -m fixgw.tools.dronecan_sim --health error
    python -m fixgw.tools.dronecan_sim --fuzz --duration 60

Messages are real DSDL objects encoded by the dronecan library's own
transport (multi-frame transfers, transfer CRC, toggle bits and transfer
ids included), so the plugin's receive path is exercised exactly as it
would be by a real sensor.

Script files are YAML:

    messages:
      - message: uavcan.equipment.ahrs.MagneticFieldStrength2
        node: 42
        fields:
          sensor_id: 0
          magnetic_field_ga: [0.21, -0.05, 0.40]

Every listed message is sent once per cycle at --rate cycles per second,
and each sending node also broadcasts uavcan.protocol.NodeStatus once a
second, as every DroneCAN node must.

With no --script, the built-in demo is a magnetometer (node 42) whose
field vector turns through a full 360 degrees of heading every 36 s, plus
a power monitor (node 43: CircuitStatus 0 and StaticTemperature).  The
field strength is a plausible mid-latitude value, not a calibrated one.

--fuzz sends uniformly random frames (11- and 29-bit ids, random DLCs and
payload bytes) instead of the script; it exists to soak-test that hostile
traffic never kills the plugin thread.
"""

import argparse
import math
import random
import time

import can
import dronecan
import yaml
from dronecan import transport

from fixgw.plugins.dronecan import mapping

MAG_NODE = 42
POWER_NODE = 43
# Mid-latitude field in gauss: horizontal and vertical (down) components.
FIELD_H = 0.22
FIELD_V = 0.42
# Degrees of heading per second in the demo sweep.
SWEEP_RATE = 10.0
PRIORITY = 24  # DroneCAN "medium"

HEALTH = {"ok": 0, "warning": 1, "error": 2, "critical": 3}


def build(message, fields):
    """Instantiate a DSDL message object by full type name."""
    dtype = mapping.resolve_type(message)
    if dtype is None:
        raise SystemExit("unknown DroneCAN message type '{}'".format(message))
    return dtype(**(fields or {}))


def mag_vector(heading_deg):
    """Body-frame field (x fwd, y right, z down) for a level aircraft."""
    h = math.radians(heading_deg)
    return [FIELD_H * math.cos(h), -FIELD_H * math.sin(h), FIELD_V]


def default_messages(t):
    """One cycle of the built-in demo at time t (seconds)."""
    heading = (t * SWEEP_RATE) % 360.0
    return [
        (
            MAG_NODE,
            build(
                "uavcan.equipment.ahrs.MagneticFieldStrength2",
                {"sensor_id": 0, "magnetic_field_ga": mag_vector(heading)},
            ),
        ),
        (
            POWER_NODE,
            build(
                "uavcan.equipment.power.CircuitStatus",
                {"circuit_id": 0, "voltage": 13.9, "current": 12.5},
            ),
        ),
        (
            POWER_NODE,
            build(
                "uavcan.equipment.air_data.StaticTemperature",
                {"static_temperature": 288.15},
            ),
        ),
    ]


def load_script(path):
    with open(path) as f:
        doc = yaml.safe_load(f)
    messages = doc.get("messages") if isinstance(doc, dict) else None
    if not messages:
        raise SystemExit("Script '{}' has no 'messages' list".format(path))
    # Build once up front so a bad script fails before anything is sent.
    return [
        (m.get("node", MAG_NODE), build(m["message"], m.get("fields")))
        for m in messages
    ]


def frames(msg, node, transfer_ids, priority=PRIORITY):
    """Encode one broadcast into [(can_id, data)], advancing its transfer id."""
    key = (dronecan.get_dronecan_data_type(msg).default_dtid, node)
    tid = transfer_ids.get(key, 0)
    transfer_ids[key] = (tid + 1) & 0x1F
    tr = transport.Transfer(
        payload=msg,
        source_node_id=node,
        transfer_id=tid,
        transfer_priority=priority,
        service_not_message=False,
    )
    return [(f.message_id, bytes(f.bytes)) for f in tr.to_frames()]


def node_status(uptime, health):
    return build(
        "uavcan.protocol.NodeStatus",
        {"uptime_sec": int(uptime), "health": health, "mode": 0},
    )


def cycle_frames(messages, transfer_ids):
    """All frames for one cycle of (node, msg) pairs."""
    out = []
    for node, msg in messages:
        out.extend(frames(msg, node, transfer_ids))
    return out


def fuzz_message(rng):
    """(can_id, data, extended) with random id width, DLC and payload."""
    extended = rng.random() < 0.5
    canid = rng.randrange(0, (1 << 29) if extended else (1 << 11))
    data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
    return canid, data, extended


def main():
    parser = argparse.ArgumentParser(
        description="Send scripted or random DroneCAN broadcasts onto a CAN bus"
    )
    parser.add_argument("--interface", default="virtual", help="python-can interface")
    parser.add_argument("--channel", default="tdc0", help="python-can channel")
    parser.add_argument("--script", help="YAML message script (default: built-in demo)")
    parser.add_argument(
        "--rate",
        type=float,
        default=10.0,
        help="script cycles (or fuzz frames) per second",
    )
    parser.add_argument(
        "--duration", type=float, default=0.0, help="seconds to run (0 = forever)"
    )
    parser.add_argument(
        "--health",
        choices=sorted(HEALTH),
        default="ok",
        help="health every simulated node reports in its NodeStatus",
    )
    parser.add_argument(
        "--fuzz", action="store_true", help="send random frames instead of the script"
    )
    parser.add_argument("--seed", type=int, default=None, help="fuzz RNG seed")
    args = parser.parse_args()

    script = load_script(args.script) if args.script else None
    rng = random.Random(args.seed)
    transfer_ids = {}
    period = 1.0 / args.rate if args.rate > 0 else 0.1
    health = HEALTH[args.health]

    bus = can.Bus(args.channel, interface=args.interface)
    start = time.monotonic()
    last_status = None
    sent = 0
    try:
        while True:
            now = time.monotonic() - start
            if args.fuzz:
                batch = [fuzz_message(rng)]
            else:
                messages = script if script is not None else default_messages(now)
                if last_status is None or now - last_status >= 1.0:
                    last_status = now
                    for node in sorted({n for n, _ in messages}):
                        messages = messages + [(node, node_status(now, health))]
                batch = [(i, d, True) for i, d in cycle_frames(messages, transfer_ids)]
            for canid, data, extended in batch:
                bus.send(
                    can.Message(
                        arbitration_id=canid, data=data, is_extended_id=extended
                    )
                )
                sent += 1
            time.sleep(period)
            if args.duration and (time.monotonic() - start) >= args.duration:
                break
    except KeyboardInterrupt:
        pass
    finally:
        bus.shutdown()
    print("sent {} frames in {:.1f}s".format(sent, time.monotonic() - start))


if __name__ == "__main__":
    main()
