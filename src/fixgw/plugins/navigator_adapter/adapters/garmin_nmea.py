"""Garmin NMEA 0183 adapter — reference implementation of ParserAdapter.

Handles the Aviation Output 1 sentence set from Garmin GPS navigators
(GNX 375, GNS 430/530, GTN 650/750, GPS 175, GNC 355, and any NMEA
0183-compliant navigator outputting RMC/GGA/RMB/APB sentences).

Sign convention for cross_track_error_nm_signed:
  Negative → aircraft LEFT of desired course (steer right to return)
  Positive → aircraft RIGHT of desired course (steer left to return)

In $GPRMB the steer-direction flag encodes this as:
  'L' (steer left) → aircraft RIGHT of track → positive XTE
  'R' (steer right) → aircraft LEFT of track  → negative XTE
"""

from __future__ import annotations

from typing import List

try:
    import pynmea2
    _PYNMEA2_AVAILABLE = True
except ImportError:
    _PYNMEA2_AVAILABLE = False

from ..base import AdapterHealth, NormalizedNavMessage, ParserAdapter, Position


class GarminNmeaAdapter(ParserAdapter):
    """Parser for Garmin Aviation Output 1 NMEA sentence set."""

    def __init__(self) -> None:
        self._parse_errors = 0
        self._last_sentence_type: str | None = None

    def name(self) -> str:
        return "garmin_nmea"

    def supported_protocols(self) -> List[str]:
        return ["NMEA 0183: $GPRMC, $GPGGA, $GPRMB, $GPAPB"]

    def parse_line(self, raw: bytes) -> List[NormalizedNavMessage]:
        if not _PYNMEA2_AVAILABLE:
            return []
        try:
            line = raw.decode("ascii", errors="replace").strip()
            msg = pynmea2.parse(line)
        except Exception:
            self._parse_errors += 1
            return []

        stype = msg.sentence_type
        self._last_sentence_type = stype

        handler = {
            "RMC": self._parse_rmc,
            "GGA": self._parse_gga,
            "RMB": self._parse_rmb,
            "APB": self._parse_apb,
        }.get(stype)

        if handler is None:
            return []

        try:
            result = handler(msg)
            return [result] if result is not None else []
        except Exception:
            self._parse_errors += 1
            return []

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            parse_errors=self._parse_errors,
            last_sentence_type=self._last_sentence_type,
        )

    def reset(self, reason: str = "") -> None:
        self._parse_errors = 0
        self._last_sentence_type = None

    # ------------------------------------------------------------------
    # Sentence parsers — each returns a NormalizedNavMessage or None
    # ------------------------------------------------------------------

    def _parse_rmc(self, msg) -> NormalizedNavMessage | None:
        if msg.status != "A":
            return None
        out = NormalizedNavMessage()
        out.position = Position(lat_deg=msg.latitude, lon_deg=msg.longitude)
        if msg.spd_over_grnd is not None:
            out.ground_speed_kt = float(msg.spd_over_grnd)
        if msg.true_course is not None:
            out.ground_track_deg_true = float(msg.true_course)
        return out

    def _parse_gga(self, msg) -> NormalizedNavMessage | None:
        qual = int(msg.gps_qual) if msg.gps_qual is not None else 0
        out = NormalizedNavMessage(gps_fix_type=qual)
        if qual == 0:
            return out
        out.position = Position(lat_deg=msg.latitude, lon_deg=msg.longitude)
        if msg.altitude is not None:
            out.position.alt_ft_msl = float(msg.altitude) * 3.28084
        return out

    def _parse_rmb(self, msg) -> NormalizedNavMessage | None:
        if msg.status != "A":
            return None
        if msg.cross_track_err is None or msg.dir_steer not in ("L", "R"):
            return None
        xte_mag = float(msg.cross_track_err)
        xtrack = xte_mag if msg.dir_steer == "L" else -xte_mag
        return NormalizedNavMessage(cross_track_error_nm_signed=xtrack)

    def _parse_apb(self, msg) -> NormalizedNavMessage | None:
        if msg.bearing_present_dest is None:
            return None
        return NormalizedNavMessage(bearing_to_wp_deg=float(msg.bearing_present_dest))
