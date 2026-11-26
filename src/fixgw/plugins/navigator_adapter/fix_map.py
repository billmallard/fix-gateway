"""Maps NormalizedNavMessage fields to FIX-Gateway database keys.

The navigator is an external navigator: its guidance goes to the EXT* inputs
(EXTCRS/EXTCDI/EXTTF/EXTXTK), which compute's GPSSRC and NAVSRC selects route
into COURSE/CDI/TOFROM/XTRACK. It never writes COURSE, CDI or XTRACK, whose one writer is
compute (AER-4473).

A message whose ``integrity`` is anything but ``valid`` writes its position
(LAT, LONG, TALT), its kinematics (GS, TRACK) and its EXT* guidance bad, and a
valid one clears the flag (SRC6, AER-6674). GPS_FIX_TYPE and the waypoint keys
carry no bad flag from it: the first is itself a quality key, the second names
a fix, not a measurement.
"""

from __future__ import annotations

from typing import Callable, Optional

from fixgw.nmea_fix import cdi_from_cross_track

from .base import NormalizedNavMessage

_DEFAULT_CDI_FULL_SCALE_NM = 5.0


def apply_to_db(
    msg: NormalizedNavMessage,
    db_write: Callable[[str, object], None],
    cdi_full_scale_nm: float = _DEFAULT_CDI_FULL_SCALE_NM,
    set_value: Optional[Callable[[str, object, bool], None]] = None,
) -> None:
    """Write all non-None fields from msg to the FIX database.

    ``set_value(key, value, bad)`` writes a value with its bad flag. Every key
    the message's ``integrity`` covers goes through it, and so does TALT, bad
    below a 3-D fix as well (TALTSRC1, AER-6458). A plain ``db_write`` never
    clears ``bad``, so without ``set_value`` those keys are written with no
    flag at all; the plugin always passes one."""
    if set_value is None:
        def set_value(key, value, bad):
            db_write(key, value)

    # Anything but "valid" is bad, so a value an adapter misspells fails safe.
    bad = msg.integrity != "valid"

    pos = msg.position
    if pos.lat_deg is not None:
        set_value("LAT", pos.lat_deg, bad)
    if pos.lon_deg is not None:
        set_value("LONG", pos.lon_deg, bad)
    if pos.alt_ft_msl is not None:
        # MSL, so TALT, never GPS_ELLIPSOID_ALT (GALT1/TALTSRC1, AER-6458).
        # Bad below a 3-D fix, when the navigator says which fix it has.
        below_3d = msg.gps_fix_type is not None and msg.gps_fix_type < 3
        set_value("TALT", pos.alt_ft_msl, bad or below_3d)

    if msg.ground_speed_kt is not None:
        set_value("GS", msg.ground_speed_kt, bad)
    if msg.ground_track_deg_true is not None:
        set_value("TRACK", msg.ground_track_deg_true, bad)
    if msg.desired_track_deg_mag is not None:
        set_value("EXTCRS", msg.desired_track_deg_mag, bad)
    if msg.to_from is not None:
        set_value("EXTTF", msg.to_from, bad)

    if msg.cross_track_error_nm_signed is not None:
        xtk = msg.cross_track_error_nm_signed
        set_value("EXTXTK", xtk, bad)
        set_value("EXTCDI", cdi_from_cross_track(xtk, cdi_full_scale_nm), bad)

    if msg.gps_fix_type is not None:
        db_write("GPS_FIX_TYPE", msg.gps_fix_type)

    wp = msg.waypoint
    if wp.ident is not None:
        db_write("WPNAME", wp.ident)
    if wp.lat_deg is not None:
        db_write("WPLAT", wp.lat_deg)
    if wp.lon_deg is not None:
        db_write("WPLON", wp.lon_deg)
