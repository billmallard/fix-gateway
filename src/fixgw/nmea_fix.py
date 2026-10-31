#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  NMEA 0183 fix quality -> the FIX database GPS_FIX_TYPE contract.
#
#  GPS_FIX_TYPE (config/database/ahrs.yaml) is the gpsd-mode / MAVLink fix_type
#  scale: 0-1 no fix, 2 2D, 3 3D, 4 3D with DGPS, 5 3D with RTK. The gpsd and
#  mavlink plugins write it natively. NMEA has no single field for it: GGA
#  carries a quality code (0 none, 1 GPS, 2 DGPS/WAAS, 4/5 RTK, 6 dead
#  reckoning, ...) and GSA carries the 2D/3D mode (1 none, 2 2D, 3 3D). Writing
#  the GGA quality straight into the key (AER-4224) made a valid GPS fix read as
#  "no fix" and a WAAS fix read as "2D", so the flight-plan engine's >= 3 gate
#  failed every guidance output from a Garmin navigator.
#
#  Shared by the garmin_gnx375 plugin and the navigator_adapter Garmin NMEA
#  adapter so the two NMEA writers of the key cannot drift apart again.

# GGA quality codes that are a GNSS position solution. 6 (estimated / dead
# reckoning), 7 (manual input) and 8 (simulator) are not, and map to no fix.
_GGA_GNSS_FIX = (1, 2, 3, 4, 5)
_GGA_DGPS = 2
_GGA_RTK = (4, 5)


def _int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def gps_fix_type(gga_qual, num_sats=None, gsa_mode=None):
    """Return the GPS_FIX_TYPE contract value for one NMEA fix epoch.

    gga_qual: GGA field 6 (fix quality).
    num_sats: GGA field 7 (satellites in use), or None if absent.
    gsa_mode: GSA field 2 (1 no fix, 2 2D, 3 3D) from the same epoch, or None
        if the source sent no GSA. GSA is authoritative for 2D vs 3D; without
        it, 4 or more satellites in use is taken as 3D, since a 3D solution
        needs at least four; fewer, or no count, is 2D (the conservative
        answer -- it fails a 3D integrity gate rather than passing it).

    GGA quality is authoritative for "is there a fix at all": a GSA that still
    says 3D while GGA says 0 is no fix.
    """
    qual = _int_or_none(gga_qual)
    if qual not in _GGA_GNSS_FIX:
        return 0

    mode = _int_or_none(gsa_mode)
    if mode in (1, 2, 3):
        dim = mode
    else:
        sats = _int_or_none(num_sats)
        dim = 3 if sats is not None and sats >= 4 else 2

    if dim != 3:
        return dim
    if qual == _GGA_DGPS:
        return 4
    if qual in _GGA_RTK:
        return 5
    return 3
