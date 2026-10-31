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


#  External-navigator guidance: NMEA RMB/APB/RMC -> EXTCDI / EXTCRS / EXTTF.
#
#  A serial navigator is an external navigator in the GPSSRC sense
#  (config/connections/compute.yaml): it writes the EXT* inputs and the compute
#  selects route them through GPSCRS/GPSCDI/GPSTF into COURSE/CDI/TOFROM. It
#  never writes COURSE, CDI or XTRACK itself, because compute is the one
#  writer of those (AER-4473).
#
#  The field names are pynmea2's. The two plugins used to read cross_track_err,
#  dir_steer and bearing_present_dest, which pynmea2 does not have, so every
#  real RMB and APB raised AttributeError in the dispatcher and wrote nothing;
#  the unit tests fed MagicMocks carrying the same invented names.


def _float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rmb_cross_track_nm(msg):
    """Signed cross-track error in nm from an RMB, positive = right of track.

    RMB field 3 is the direction to steer: 'L' means the aircraft is right of
    track (positive), 'R' left of track (negative) -- the FPLXTK/XTRACK sign.
    Returns None for a void sentence or a missing or malformed field.
    """
    if msg.status != "A":
        return None
    xte = _float_or_none(msg.cross_track_error)
    steer = msg.cte_correction_dir
    if xte is None or steer not in ("L", "R"):
        return None
    return xte if steer == "L" else -xte


def cdi_from_cross_track(xtrack_nm, full_scale_nm):
    """Course deviation -xtrack / full_scale, clamped to [-1, 1].

    The FPLCDI convention, so the HSI needle behaves the same for the internal
    plan and an external navigator.
    """
    return max(-1.0, min(1.0, -xtrack_nm / full_scale_nm))


def rmc_magvar_east(msg):
    """Magnetic variation from an RMC, degrees, east positive; None if absent."""
    var = _float_or_none(msg.mag_variation)
    if var is None or msg.mag_var_dir not in ("E", "W"):
        return None
    return var if msg.mag_var_dir == "E" else -var


def apb_guidance(msg, magvar_east=None):
    """(desired track magnetic, TO/FROM) from an APB.

    The desired track is APB field 8, the bearing from the origin waypoint to
    the destination: the leg's DTK, which is what EXTCRS, FPLCRS and X-Plane's
    gps_course_degtm all carry. It is NOT field 11, the bearing from present
    position to the destination, which swings as the aircraft drifts off the
    leg. A true bearing is made magnetic with the latest RMC variation
    (magnetic = true - east variation); with no variation known it is None
    rather than a true value labelled magnetic.

    TO/FROM is 2 (FROM) once field 7 says the perpendicular at the destination
    has been passed, 1 (TO) otherwise, and 0 (off) when field 1 says the data
    is not valid -- the EXTTF scale. An invalid APB returns (None, 0).
    """
    if msg.status_gen != "A":
        return None, 0
    to_from = 2 if msg.perp_passed == "A" else 1
    dtk = _float_or_none(msg.bearing_to_dest)
    if dtk is not None:
        if msg.bearing_type == "T":
            dtk = None if magvar_east is None else dtk - magvar_east
        elif msg.bearing_type != "M":
            dtk = None
    if dtk is not None:
        dtk %= 360.0
    return dtk, to_from
