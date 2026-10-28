#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Minimal ARINC 424-18 reader for the synthetic flight tier (SYN1).
#
"""Just enough of FAA CIFP (``FAACIFP18``, ARINC 424-18 fixed-width records)
to fly one approach: the header cycle, fix positions (terminal and en-route
waypoints, VHF navaids, runways, airports) and an approach's legs.

Written for the synthetic tier only. It is deliberately not shared with the
navigation engine (which reads no CIFP at all -- its route arrives on the
bus) nor taken from makerplane-data's ``packtools.arinc424``: the reference
navigator must not inherit another decoder's reading of the data.

Column positions are 1-based, as ARINC 424-18 section 5 prints them.
Anything this module does not understand raises :class:`CifpError`; it
never guesses a value the record does not carry.
"""

from dataclasses import dataclass


class CifpError(Exception):
    pass


def _col(line, first, last):
    return line[first - 1:last]


def _lat(field):
    # N/Sddmmsscc -- hundredths of a second
    hemi, d, m, s = field[0], int(field[1:3]), int(field[3:5]), int(field[5:9]) / 100.0
    v = d + m / 60.0 + s / 3600.0
    if hemi not in "NS":
        raise CifpError(f"bad latitude field {field!r}")
    return -v if hemi == "S" else v


def _lon(field):
    # E/Wdddmmsscc
    hemi, d, m, s = field[0], int(field[1:4]), int(field[4:6]), int(field[6:10]) / 100.0
    v = d + m / 60.0 + s / 3600.0
    if hemi not in "EW":
        raise CifpError(f"bad longitude field {field!r}")
    return -v if hemi == "W" else v


def _magvar(field):
    # E/Wddd.d -> degrees, east positive (ARINC convention; callers convert)
    field = field.strip()
    if not field:
        return None
    hemi, tenths = field[0], int(field[1:5])
    if hemi not in "EWT":
        raise CifpError(f"bad magnetic variation field {field!r}")
    return tenths / 10.0 * (-1.0 if hemi == "W" else 1.0)


@dataclass(frozen=True)
class Fix:
    ident: str
    lat: float
    lon: float
    kind: str              # "waypoint", "vor", "runway", "airport"
    magvar_east: float = None


@dataclass(frozen=True)
class ApproachLeg:
    """One primary approach-leg record, decoded but not interpreted."""
    route_type: str        # "A" transition, or the final's type letter
    transition: str        # transition ident, "" for the final/missed
    seq: int
    fix: str               # "" for a leg with no fix (CA, VA...)
    fix_section: str       # e.g. "PC", "EA", "D ", "PG"
    desc: str              # 4-char waypoint description code (cols 40-43)
    turn: str              # "L", "R" or ""
    path_term: str
    course_mag: float      # None when blank
    dist_or_time: str      # raw cols 75-78 ("0040", "T010", "")
    alt_desc: str
    alt1: str
    alt2: str

    @property
    def flyover(self):
        return self.desc[1:2] == "Y"

    @property
    def is_iaf(self):
        return self.desc[3:4] in ("A", "C", "D")

    @property
    def is_faf(self):
        return self.desc[3:4] == "F"

    @property
    def is_map(self):
        return self.desc[3:4] == "M"

    @property
    def first_missed(self):
        return self.desc[2:3] == "M"


class Cifp:
    """Decoded subset of one CIFP file. ``cycle`` is the 4-digit AIRAC
    cycle from the HDR01 record (``"2611"``)."""

    def __init__(self, path):
        self.path = str(path)
        self.cycle = None
        self._lines = []
        with open(path, "r", encoding="ascii", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\r\n")
                if line.startswith("HDR01"):
                    # "HDR01FAACIFP18      001P013203973042611  07-OCT-2026..."
                    self.cycle = _col(line, 36, 39)
                elif line.startswith("S"):
                    self._lines.append(line)
        if not self.cycle or not self.cycle.isdigit():
            raise CifpError(f"{path}: no HDR01 cycle")

    # -- fixes -------------------------------------------------------------
    def fix(self, ident, airport=None, section=None):
        """The position of ``ident``. ``section`` is the 2-char section code
        a leg record names (``PC`` terminal waypoint, ``EA`` en-route
        waypoint, ``D `` VHF navaid, ``PG`` runway); with ``airport`` it
        disambiguates a terminal waypoint or runway. Raises if absent or
        ambiguous."""
        found = []
        for line in self._lines:
            sec = _col(line, 5, 5)
            if sec == "P":
                sub = _col(line, 13, 13)
                if airport and _col(line, 7, 10).strip() != airport:
                    continue
                if sub == "C" and section in (None, "PC") and _col(line, 14, 18).strip() == ident:
                    found.append(Fix(ident, _lat(_col(line, 33, 41)), _lon(_col(line, 42, 51)),
                                     "waypoint", _magvar(_col(line, 75, 79))))
                elif sub == "G" and section in (None, "PG") and _col(line, 14, 18).strip() == ident:
                    found.append(Fix(ident, _lat(_col(line, 33, 41)), _lon(_col(line, 42, 51)), "runway"))
                elif sub == "A" and section in (None, "PA") and _col(line, 7, 10).strip() == ident:
                    found.append(Fix(ident, _lat(_col(line, 33, 41)), _lon(_col(line, 42, 51)),
                                     "airport", _magvar(_col(line, 52, 56))))
            elif sec == "E" and _col(line, 6, 6) == "A" and section in (None, "EA"):
                if _col(line, 14, 18).strip() == ident:
                    found.append(Fix(ident, _lat(_col(line, 33, 41)), _lon(_col(line, 42, 51)),
                                     "waypoint", _magvar(_col(line, 75, 79))))
            elif sec == "D" and _col(line, 6, 6) == " " and section in (None, "D ", "D"):
                if _col(line, 14, 17).strip() == ident and _col(line, 33, 33) in "NS":
                    found.append(Fix(ident, _lat(_col(line, 33, 41)), _lon(_col(line, 42, 51)),
                                     "vor", _magvar(_col(line, 75, 79))))
        if not found:
            raise CifpError(f"{ident}: no fix record (section {section!r}, airport {airport!r}) in {self.path}")
        if len(set((f.lat, f.lon) for f in found)) > 1:
            raise CifpError(f"{ident}: ambiguous ({len(found)} records); name the section")
        return found[0]

    # -- approaches --------------------------------------------------------
    def approach_legs(self, airport, approach):
        """Primary records (continuation 0/1) of ``approach`` at
        ``airport`` in file order."""
        legs = []
        for line in self._lines:
            if _col(line, 5, 5) != "P" or _col(line, 13, 13) != "F":
                continue
            if _col(line, 7, 10).strip() != airport or _col(line, 14, 19).strip() != approach:
                continue
            if _col(line, 39, 39) not in ("0", "1"):
                continue   # continuation records carry no leg
            crs = _col(line, 71, 74).strip()
            legs.append(ApproachLeg(
                route_type=_col(line, 20, 20),
                transition=_col(line, 21, 25).strip(),
                seq=int(_col(line, 27, 29)),
                fix=_col(line, 30, 34).strip(),
                fix_section=_col(line, 37, 38),
                desc=_col(line, 40, 43),
                turn=_col(line, 44, 44).strip(),
                path_term=_col(line, 48, 49),
                course_mag=int(crs) / 10.0 if crs else None,
                dist_or_time=_col(line, 75, 78).strip(),
                alt_desc=_col(line, 83, 83).strip(),
                alt1=_col(line, 85, 89).strip(),
                alt2=_col(line, 90, 94).strip(),
            ))
        if not legs:
            raise CifpError(f"{airport} {approach}: no approach records in {self.path}")
        return legs
