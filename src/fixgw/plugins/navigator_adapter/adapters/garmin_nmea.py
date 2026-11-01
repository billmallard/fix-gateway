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

from fixgw.nmea_fix import apb_guidance, gps_fix_type, rmb_cross_track_nm, rmc_magvar_east

from ..base import AdapterHealth, NormalizedNavMessage, ParserAdapter, Position


class GarminNmeaAdapter(ParserAdapter):
    """Parser for Garmin Aviation Output 1 NMEA sentence set."""

    def __init__(self) -> None:
        self._parse_errors = 0
        self._last_sentence_type: str | None = None
        self._gsa_mode: str | None = None
        self._magvar_east: float | None = None

    def name(self) -> str:
        return "garmin_nmea"

    def supported_protocols(self) -> List[str]:
        return ["NMEA 0183: $GPRMC, $GPGGA, $GPGSA, $GPRMB, $GPAPB"]

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
            "GSA": self._parse_gsa,
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
        self._gsa_mode = None
        self._magvar_east = None

    # ------------------------------------------------------------------
    # Sentence parsers — each returns a NormalizedNavMessage or None
    # ------------------------------------------------------------------

    def _parse_rmc(self, msg) -> NormalizedNavMessage | None:
        if msg.status != "A":
            return None
        # Held for APB, which may give the desired track as a true bearing.
        magvar = rmc_magvar_east(msg)
        if magvar is not None:
            self._magvar_east = magvar
        out = NormalizedNavMessage()
        out.position = Position(lat_deg=msg.latitude, lon_deg=msg.longitude)
        if msg.spd_over_grnd is not None:
            out.ground_speed_kt = float(msg.spd_over_grnd)
        if msg.true_course is not None:
            out.ground_track_deg_true = float(msg.true_course)
        return out

    def _parse_gsa(self, msg) -> NormalizedNavMessage | None:
        # Held for the next GGA, which carries gps_fix_type once per epoch.
        self._gsa_mode = msg.mode_fix_type
        return None

    def _parse_gga(self, msg) -> NormalizedNavMessage | None:
        # A GSA counts only for the GGA that follows it (see garmin_gnx375).
        gsa_mode, self._gsa_mode = self._gsa_mode, None
        qual = int(msg.gps_qual) if msg.gps_qual is not None else 0
        out = NormalizedNavMessage(gps_fix_type=gps_fix_type(msg.gps_qual, msg.num_sats, gsa_mode))
        if qual == 0:
            return out
        out.position = Position(lat_deg=msg.latitude, lon_deg=msg.longitude)
        if msg.altitude is not None:
            out.position.alt_ft_msl = float(msg.altitude) * 3.28084
        return out

    def _parse_rmb(self, msg) -> NormalizedNavMessage | None:
        xtrack = rmb_cross_track_nm(msg)
        if xtrack is None:
            return None
        return NormalizedNavMessage(cross_track_error_nm_signed=xtrack)

    def _parse_apb(self, msg) -> NormalizedNavMessage | None:
        dtk, to_from = apb_guidance(msg, self._magvar_east)
        return NormalizedNavMessage(desired_track_deg_mag=dtk, to_from=to_from)
