"""Write nexrad_oracle.json: what fisb-decode makes of the NEXRAD blocks
(products 63 and 64) in a .978 uplink file.  test_nexrad.py compares fixgw's
decoder against it.

fisb-decode (https://github.com/rand-projects/fisb-decode, BSD-2-Clause
Plus Patent) is an independent decoder written against DO-358B.  Its level 0
decodes each block, its level 2 (msgBlock) expands empty-block runs into
blocks, and its image writer (db/harvest/images.py, getCoordsOfSplitBin) gives
each block's corners in degrees.  It is not a dependency: run this by hand
with a checkout of it, as the README says.  images.py imports GDAL for the
GeoTIFF writer, which this does not call, so a stub stands in for it.

    python3 make_nexrad_oracle.py <fisb-decode checkout> <.978 file> > nexrad_oracle.json

One entry per uplink line holding a block: the line index and, per block in
the order sent, [product, hour, minute, scale, north, west, south, east,
bins], corners in arcminutes (east-positive) and bins a string of 128 digits,
or null for an empty block.
"""
import gzip
import json
import sys
import types

sys.path.insert(0, sys.argv[1])
for name in ("osgeo", "osgeo.gdal", "osgeo.osr"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["osgeo"].gdal = sys.modules["osgeo.gdal"]
sys.modules["osgeo"].osr = sys.modules["osgeo.osr"]

import fisb.level0.level0Config as cfg  # noqa: E402

cfg.CALCULATE_RSR = False
from fisb.level0.ground_uplink_message import groundUplinkMessage  # noqa: E402
import fisb.level2.msgBlock as msgBlock  # noqa: E402
import db.harvest.images as images  # noqa: E402


def arcmin(deg):
    m = deg * 60.0
    assert abs(m - round(m)) < 1e-6, deg
    return int(round(m))


def corners(alt_bn, scale):
    lat_bin, lon_bin = images.splitBinNum(alt_bn)
    north, west = images.getCoordsOfSplitBin(lat_bin, lon_bin, images.UL, scale)
    south, east = images.getCoordsOfSplitBin(lat_bin, lon_bin, images.LR, scale)
    return [arcmin(north), arcmin(west), arcmin(south), arcmin(east)]


opener = gzip.open if sys.argv[2].endswith(".gz") else open
lines = [ln.strip() for ln in opener(sys.argv[2], "rt") if ln.startswith("+")]
out = []
for i, line in enumerate(lines):
    d = groundUplinkMessage(line if ";" in line else line + ";", False, False)
    blocks = []
    for f in (d or {}).get("frames", []):
        pid = f.get("product_id")
        if pid not in (63, 64):
            continue
        c = f["contents"]
        # the received date only places the APDU time; it does not change
        # the blocks
        msgs = msgBlock.msgBlock(c, pid, 2020, 10, 30, 9, 0, f["hour"], f["minute"])
        for m in msgs:
            empty = c["element_id"] == 0
            blocks.append(
                [pid, f["hour"], f["minute"], m["scale_factor"]]
                + corners(m["alt_bn"], m["scale_factor"])
                + [None if empty else "".join(str(ord(b)) for b in m["bins"])]
            )
    if blocks:
        out.append([i, blocks])
json.dump({"uplinks": len(lines), "block_uplinks": out}, sys.stdout, separators=(",", ":"))
sys.stdout.write("\n")
