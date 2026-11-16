"""Write fisb_oracle.json: what fisb-decode makes of the text products in a
dump978 uplink file.  The golden tests compare fixgw's decoder against it.

fisb-decode (https://github.com/rand-projects/fisb-decode, BSD-2-Clause
Plus Patent) is an independent decoder written against DO-358B.  It is not a
dependency: run this by hand with a checkout of it, as the README says.

    python3 make_fisb_oracle.py <fisb-decode checkout> <dump978 file> > fisb_oracle.json
"""
import gzip
import json
import sys

sys.path.insert(0, sys.argv[1])
import fisb.level0.level0Config as cfg  # noqa: E402

cfg.CALCULATE_RSR = False
from fisb.level0.ground_uplink_message import groundUplinkMessage  # noqa: E402
from fisb.level1.Unsegmenter import Unsegmenter  # noqa: E402

TWGO = (8, 11, 12, 13, 14, 15, 16, 17)


def norm(text):
    # fisb-decode writes ETX, NC and RS as '~' and keeps trailing blanks
    text = text.replace("~", "")
    return "\n".join(line.rstrip() for line in text.rstrip().split("\n"))


def twgo_text(pid, f):
    c = f["contents"]
    return {
        "product_id": pid,
        "hour": f["hour"],
        "minute": f["minute"],
        "location": c["location"].replace("~", "").strip(),
        "records": [
            [r["report_number"], r["report_year"], bool(r["report_status"]),
             norm(r.get("text", ""))]
            for r in c["records"]
        ],
    }


opener = gzip.open if sys.argv[2].endswith(".gz") else open
lines = [ln.strip() for ln in opener(sys.argv[2], "rt") if ln.startswith("+")]
uplinks = []
segments = Unsegmenter(60)
joined = []
for i, line in enumerate(lines):
    d = groundUplinkMessage(line if ";" in line else line + ";", False, False)
    apdus = []
    for f in (d or {}).get("frames", []):
        pid = f.get("product_id")
        if f.get("s_flag") == 1:
            out = segments.processFrame(f, 0)
            if out:
                joined.append(dict(twgo_text(pid, out), last_line=i))
        elif pid == 413:
            apdus.append({"product_id": 413, "hour": f["hour"],
                          "minute": f["minute"], "text": norm(f["contents"])})
        elif pid in TWGO and f["contents"].get("record_format") == 2:
            apdus.append(twgo_text(pid, f))
    if apdus:
        uplinks.append({"line": i, "apdus": apdus})
json.dump({"uplinks": len(lines), "text_uplinks": uplinks, "joined": joined},
          sys.stdout, indent=1)
sys.stdout.write("\n")
