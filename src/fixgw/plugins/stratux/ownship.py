# Ownship writer: the GDL90 Ownship Report (0x0A) and Ownship Geometric
# Altitude (0x0B) into the STX key block (config/database/stratux.yaml).
#
# The keys are the receiver's own, prefixed STX, and never the canonical
# LAT/LONG/TRACK/ALT (Bill's ruling on GDL1, packet AER-5615): position is
# source-selected, so a second GPS source must never fight this one for a
# key.  Selecting STX* into LAT/LONG is a separate step.
#
# A field the ICD marks unavailable is written with bad set, never as a
# number a reader could take at face value: 0 for a measurement, and the
# key's maximum for an uncertainty (STXRC, STXEPU, STXVFOM), so a reader that
# ignores the flag still sees the worst figure, not a perfect one.
#
# Citations are G§ as in gdl90.py.

from fixgw.plugins.stratux import gdl90

KEYS = (
    "STXLAT",
    "STXLONG",
    "STXPALT",
    "STXGS",
    "STXTRACK",
    "STXVS",
    "STXAIR",
    "STXNIC",
    "STXNACP",
    "STXRC",
    "STXEPU",
    "STXGALT",
    "STXVFOM",
    "STXVWARN",
)

TRUE_TRACK = 1  # G§3.5.1.5 Table 9, m bits 1-0


# (value, bad) for each 0x0A key, from a decoded report.  `worst` maps an
# uncertainty key to its maximum.
def report_values(r, worst):
    rc = None if r.pos_bad else gdl90.NIC_RC_M.get(r.nic)
    epu = gdl90.NACP_EPU_M.get(r.nacp)
    track_bad = r.ttype != TRUE_TRACK
    return {
        "STXLAT": (0.0 if r.pos_bad else r.lat, r.pos_bad),
        "STXLONG": (0.0 if r.pos_bad else r.lon, r.pos_bad),
        "STXPALT": (0.0 if r.alt_bad else r.alt, r.alt_bad),
        "STXGS": (0.0 if r.gs_bad else r.gs, r.gs_bad),
        # Heading (m = 2 or 3) is not track, so only a true track is written.
        "STXTRACK": (0.0 if track_bad else r.trk, track_bad),
        "STXVS": (0 if r.vs_bad else r.vs, r.vs_bad),
        "STXAIR": (r.airborne, False),
        "STXNIC": (0 if r.nic > 11 else r.nic, r.nic > 11),
        "STXNACP": (0 if r.nacp > 11 else r.nacp, r.nacp > 11),
        "STXRC": (worst["STXRC"] if rc is None else rc, rc is None),
        "STXEPU": (worst["STXEPU"] if epu is None else epu, epu is None),
    }


def geo_values(g, worst):
    return {
        "STXGALT": (g.alt, False),
        "STXVFOM": (worst["STXVFOM"] if g.vfom_bad else g.vfom, g.vfom_bad),
        "STXVWARN": (g.vwarn, False),
    }


class OwnshipWriter(object):
    # Raises KeyError naming the first STX key the database lacks.
    def __init__(self, get_item):
        self.items = {key: get_item(key) for key in KEYS}
        self.worst = {
            key: self.items[key].max for key in ("STXRC", "STXEPU", "STXVFOM")
        }

    def handle(self, msg):
        if not msg:
            return
        if msg[0] == gdl90.OWNSHIP:
            r = gdl90.parse_report(msg)
            if r is not None:
                self._write(report_values(r, self.worst))
        elif msg[0] == gdl90.GEO_ALTITUDE:
            g = gdl90.parse_geo_altitude(msg)
            if g is not None:
                self._write(geo_values(g, self.worst))

    def _write(self, values):
        for key, (value, bad) in values.items():
            self.items[key].set(value, bad=bad)
