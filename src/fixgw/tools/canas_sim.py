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

"""CANaerospace bus simulator.

Sends scripted CANaerospace frames onto any python-can interface so the
canaerospace plugin can be developed and demonstrated with no hardware:

    python -m fixgw.tools.canas_sim --interface virtual --channel tcan0
    python -m fixgw.tools.canas_sim --script myscript.yaml --rate 20
    python -m fixgw.tools.canas_sim --fuzz --duration 60

Script files are YAML:

    frames:
      - canid: 0x12C
        node: 16
        type: USHORT       # any implemented CANaerospace data type name
        values: [2650]
        service_code: 0    # optional, default 0

Each listed frame is sent once per cycle at --rate cycles per second, with
a per-(canid, node) Message Code that increments and wraps at 255 like a
real CANaerospace sender.

The default script drives the FIX keys the Rotax mapfile skeleton targets
(TACH1, MAP1, OILP1, ...) using PLACEHOLDER canids in the User-Defined
High range -- they are demo values for desk work against a matching test
mapfile, NOT verified Rotax identifiers (see
src/fixgw/config/canaerospace/rotax_is_map.yaml for the verification
procedure).

--fuzz mode sends uniformly random frames (random ids, DLCs and payload
bytes) instead of the script; it exists to soak-test that hostile traffic
never kills the plugin thread.
"""

import argparse
import random
import time

import can
import yaml

from fixgw.plugins.canaerospace import protocol

# Demo placeholders in the User-Defined High range (200-299), NOT Rotax ids.
DEFAULT_SCRIPT = [
    {"canid": 0xC8, "node": 16, "type": "USHORT", "values": [2650]},  # TACH1-ish rpm
    {"canid": 0xC9, "node": 16, "type": "FLOAT", "values": [95.0]},  # MAP1-ish kPa
    {"canid": 0xCA, "node": 16, "type": "FLOAT", "values": [310.0]},  # OILP1-ish kPa
    {"canid": 0xCB, "node": 16, "type": "FLOAT", "values": [363.15]},  # OILT1-ish K
    {"canid": 0xCC, "node": 16, "type": "FLOAT", "values": [355.15]},  # H2OT1-ish K
    {"canid": 0xCD, "node": 16, "type": "UCHAR4", "values": [78, 80, 79, 81]},  # EGT/10
    {"canid": 0xCE, "node": 16, "type": "FLOAT", "values": [13.9]},  # VOLT
]


def load_script(path):
    with open(path) as f:
        doc = yaml.safe_load(f)
    frames = doc.get("frames") if isinstance(doc, dict) else None
    if not frames:
        raise SystemExit("Script '{}' has no 'frames' list".format(path))
    return frames


def script_messages(frames, message_codes):
    """Yield (arbitration_id, data) for one cycle of the script."""
    for frame in frames:
        canid = frame["canid"]
        node = frame.get("node", 0)
        try:
            data_type = protocol.TYPE_CODES[frame["type"]]
        except KeyError:
            raise SystemExit(
                "Frame canid {}: unknown data type '{}'".format(
                    canid, frame.get("type")
                )
            )
        key = (canid, node)
        code = message_codes.get(key, -1)
        code = (code + 1) % 256
        message_codes[key] = code
        msg = protocol.CanasMessage(
            canid=canid,
            node_id=node,
            data_type=data_type,
            service_code=frame.get("service_code", 0),
            message_code=code,
            values=tuple(frame.get("values", ())),
            channel=protocol.channel_for(canid),
        )
        yield protocol.build(msg)


def fuzz_message(rng):
    canid = rng.randrange(0, protocol.MAX_CANID + 1)
    data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
    return canid, data


def main():
    parser = argparse.ArgumentParser(
        description="Send scripted or random CANaerospace frames onto a CAN bus"
    )
    parser.add_argument("--interface", default="virtual", help="python-can interface")
    parser.add_argument("--channel", default="tcan0", help="python-can channel")
    parser.add_argument("--script", help="YAML frame script (default: built-in demo)")
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
        "--fuzz", action="store_true", help="send random frames instead of the script"
    )
    parser.add_argument("--seed", type=int, default=None, help="fuzz RNG seed")
    args = parser.parse_args()

    frames = load_script(args.script) if args.script else DEFAULT_SCRIPT
    rng = random.Random(args.seed)
    message_codes = {}
    period = 1.0 / args.rate if args.rate > 0 else 0.1

    bus = can.Bus(args.channel, interface=args.interface)
    start = time.monotonic()
    sent = 0
    try:
        while True:
            if args.fuzz:
                canid, data = fuzz_message(rng)
                bus.send(
                    can.Message(arbitration_id=canid, data=data, is_extended_id=False)
                )
                sent += 1
            else:
                for canid, data in script_messages(frames, message_codes):
                    bus.send(
                        can.Message(
                            arbitration_id=canid, data=data, is_extended_id=False
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
