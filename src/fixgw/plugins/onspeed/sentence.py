#  Copyright (c) 2026 MakerPlane
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

"""Decoder for the OnSpeed Gen2 ``ONSPEED`` serial sentence.

Pure and stdlib-only, so it can be tested without a port or the database.

The authority is ``writeSerialData()`` in
``Software/OnSpeedTeensy_AHRS/DisplaySerial.ino`` of OnSpeed-Gen2 at
``be1481c``.  ``FIRMWARE_FORMAT`` below is that ``sprintf`` format string,
copied verbatim; Python's ``%`` operator accepts it unchanged, so
``encode()`` is the firmware's own formatter, not a re-derivation.  See
pyEfis ``docs/fixgw_onspeed_in_spec.md`` section 3.
"""

import re
from dataclasses import dataclass

# DisplaySerial.ino @ be1481c, the ONSPEED branch of writeSerialData().
FIRMWARE_FORMAT = (
    "#1%+04i%+05i%04u%+06i%+05i%+03i%+03i%02u%+04i%+04i%+03i"
    "%+04i%+03i%+04i%+04i%+04i%+04i%+04i%+02i%02u"
)

# The checksum loop is `for (int i=0;i<=75;i++)`: a fixed bound, not
# "everything before the checksum".  It still holds when a field overflows.
CHECKSUM_SPAN = 76

# Body bytes 0-73 are fixed width; DataMark (74+) is matched on its own
# because it is the one field allowed to widen (spec section 3.4, rule 4).
FIXED_SPAN = 74

# (name, offset, width, signed, scale).  Spec section 3.2, normative.  The
# value in engineering units is int(raw) * scale.
FIELDS = (
    ("pitch", 2, 4, True, 0.1),  # deg, + nose up
    ("roll", 6, 5, True, 0.1),  # deg, + right
    ("ias", 11, 4, False, 0.1),  # kt
    ("palt", 15, 6, True, 1),  # ft
    ("yaw_rate", 21, 5, True, 0.1),  # deg/s, body yaw rate (gYaw)
    ("lateral_g", 26, 3, True, 0.01),  # g, + leftward
    ("vertical_g", 29, 3, True, 0.1),  # g, ceil()ed in the firmware
    ("percent_lift", 32, 2, False, 1),  # 0-99
    ("aoa", 34, 4, True, 0.1),  # deg
    ("vsi", 38, 4, True, 10),  # fpm, floor()ed to 10 fpm
    ("oat", 42, 3, True, 1),  # degC, hardcoded 0 in this firmware
    ("flight_path", 45, 4, True, 0.1),  # deg
    ("flaps", 49, 3, True, 1),  # deg
    ("stall_warn_aoa", 52, 4, True, 0.1),  # deg, current flap setting
    ("onspeed_slow_aoa", 56, 4, True, 0.1),  # deg
    ("onspeed_fast_aoa", 60, 4, True, 0.1),  # deg
    ("tones_on_aoa", 64, 4, True, 0.1),  # deg, LDmaxAOA
    ("g_onset_rate", 68, 4, True, 0.01),  # g/s, hardcoded 0
    ("spin_recovery_cue", 72, 2, True, 1),  # -1/0/+1, hardcoded 0
)


def _field_pattern(width, signed):
    return r"[+-]\d{%d}" % (width - 1) if signed else r"\d{%d}" % width


_FIXED_RE = re.compile(
    "^#1" + "".join(_field_pattern(w, s) for _, _, w, s, _ in FIELDS) + "$"
)
# A %02u that needed more than two digits is 100 or more, so never has a
# leading zero.  That clause is what stops an overflow in the field just
# before DataMark from being read as a wider DataMark.
_DATAMARK_RE = re.compile(r"^(\d{2}|[1-9]\d{2,})$")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]{2}$")

REASONS = ("length", "checksum", "grammar", "wrong_format", "encoding")


class SentenceError(ValueError):
    """A line that is not a usable ONSPEED sentence.  ``reason`` is one of
    ``REASONS``."""

    def __init__(self, reason, detail=""):
        super().__init__(reason if not detail else "%s: %s" % (reason, detail))
        self.reason = reason


@dataclass(frozen=True)
class OnSpeedFrame:
    pitch: float
    roll: float
    ias: float
    palt: int
    yaw_rate: float
    lateral_g: float
    vertical_g: float
    percent_lift: int
    aoa: float
    vsi: int
    oat: int
    flight_path: float
    flaps: int
    stall_warn_aoa: float
    onspeed_slow_aoa: float
    onspeed_fast_aoa: float
    tones_on_aoa: float
    g_onset_rate: float
    spin_recovery_cue: int
    datamark: int
    datamark_overflow: bool


def checksum(body):
    """The firmware's checksum: low byte of the sum of body bytes 0-75."""
    return sum(body[:CHECKSUM_SPAN]) & 0xFF


def encode(values):
    """Format a sentence exactly as the firmware does, without CR LF.

    ``values`` is the 20 raw integers in ``FIRMWARE_FORMAT`` order (the
    ``FIELDS`` order, then DataMark).  For the simulator and the tests.
    """
    body = FIRMWARE_FORMAT % tuple(values)
    return body + "%02X" % checksum(body.encode("ascii"))


def parse(line):
    """Decode one line (bytes, with or without CR LF) to an OnSpeedFrame.

    Raises SentenceError and never returns a partial frame.
    """
    line = bytes(line).rstrip(b"\r\n")
    if any(b > 0x7F for b in line):
        raise SentenceError("encoding")
    text = line.decode("ascii")
    if text.startswith("=11"):
        raise SentenceError("wrong_format", "G3X sentence")
    if not text.startswith("#1"):
        raise SentenceError("grammar", "not an ONSPEED sentence")
    # Locate the checksum from the end, never by offset: an overflowed
    # DataMark moves it.
    body, check = text[:-2], text[-2:]
    if len(body) < CHECKSUM_SPAN:
        raise SentenceError("length", "%d body bytes" % len(body))
    if not _HEX_RE.match(check) or int(check, 16) != checksum(
        body.encode("ascii")
    ):
        raise SentenceError("checksum")
    datamark = body[FIXED_SPAN:]
    if not _FIXED_RE.match(body[:FIXED_SPAN]) or not _DATAMARK_RE.match(datamark):
        raise SentenceError("grammar")
    values = {}
    for name, offset, width, _signed, scale in FIELDS:
        raw = int(body[offset : offset + width])
        values[name] = raw * scale if isinstance(scale, int) else round(raw * scale, 2)
    return OnSpeedFrame(
        datamark=int(datamark),
        datamark_overflow=len(body) > CHECKSUM_SPAN,
        **values,
    )
