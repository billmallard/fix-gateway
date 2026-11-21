#!/usr/bin/env python3
"""Capture what a gdl90_out gateway sends a tablet, with the bus beside it.

Plays the tablet: binds a UDP port, announces itself to the gateway's
discovery port every 5 s exactly as ForeFlight does (the JSON of its GDL 90
Extended Specification, sent unicast here rather than broadcast), and records
every GDL-90 datagram it is sent.  Twice a second it also reads the keys the
plugin encodes from the same gateway over netfix, with their flags, so each
message can be checked against what the plugin read.  Stdlib only.

    python3 capture_gdl90_out.py --discovery 127.0.0.1:63193 \\
        --netfix 127.0.0.1:3496 --seconds 1800 --out cap.jsonl [--stop FILE]

Each output line is one JSON object:
    {"t": <monotonic s>, "utc": <epoch s>, "gdl": "<datagram hex>"}
    {"t": ..., "utc": ..., "bus": {"LAT": ["34.39", "00000"], ...}}
netfix flags are the characters a, o, b, f, s (annunciate, old, bad, fail,
secondary fail).  A key the gateway does not have reads ["", "missing"].
"""

import argparse
import json
import os
import socket
import time

KEYS = (
    "LAT", "LONG", "PALT", "ALT", "GS", "VS", "TRACK", "GPS_FIX_TYPE",
    "GPS_ACCURACY_HORIZ", "GPS_ACCURACY_VERTICAL", "GPS_ELLIPSOID_ALT",
    "ROLL", "PITCH", "HEAD", "IAS", "TAS", "BARO",
)


def hostport(text):
    host, _, port = text.rpartition(":")
    return host, int(port)


def read_bus(addr):
    out = {}
    try:
        with socket.create_connection(addr, timeout=2) as s:
            f = s.makefile("rw")
            for key in KEYS:
                f.write("@r%s\n" % key)
                f.flush()
                line = f.readline().strip()
                parts = line.split(";")
                if line.startswith("@r%s;" % key) and len(parts) >= 3:
                    out[key] = [parts[1], parts[2]]
                else:
                    out[key] = ["", "missing"]
    except OSError as e:
        out["_error"] = str(e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", default="127.0.0.1:63093")
    ap.add_argument("--netfix", default="127.0.0.1:3490")
    ap.add_argument("--listen", default="127.0.0.1:0", help="the tablet's GDL-90 address")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--stop", default=None, help="end early when this file exists")
    ap.add_argument("--out", default="cap.jsonl")
    a = ap.parse_args()

    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(hostport(a.listen))
    port = rx.getsockname()[1]
    rx.settimeout(0.05)
    announce = json.dumps({"App": "ForeFlight", "GDL90": {"port": port}}).encode()
    disc = hostport(a.discovery)
    bus = hostport(a.netfix)

    end = time.monotonic() + a.seconds
    next_announce = next_bus = 0.0
    with open(a.out, "w") as out:
        while time.monotonic() < end:
            now = time.monotonic()
            if a.stop and os.path.exists(a.stop):
                break
            if now >= next_announce:
                next_announce = now + 5.0
                rx.sendto(announce, disc)
            if now >= next_bus:
                next_bus = now + 0.5
                row = {"t": round(now, 3), "utc": round(time.time(), 3), "bus": read_bus(bus)}
                out.write(json.dumps(row) + "\n")
            try:
                d, _adr = rx.recvfrom(4096)
            except socket.timeout:
                continue
            row = {"t": round(time.monotonic(), 3), "utc": round(time.time(), 3), "gdl": d.hex()}
            out.write(json.dumps(row) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
