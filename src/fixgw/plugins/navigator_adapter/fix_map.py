"""Maps NormalizedNavMessage fields to FIX-Gateway database keys.

The navigator is an external navigator: its guidance goes to the EXT* inputs
(EXTCRS/EXTCDI/EXTTF), which compute's GPSSRC and NAVSRC selects route into
COURSE/CDI/TOFROM. It never writes COURSE, CDI or XTRACK, whose one writer is
compute (AER-4473).
"""

from __future__ import annotations

from typing import Callable

from fixgw.nmea_fix import cdi_from_cross_track

from .base import NormalizedNavMessage

_DEFAULT_CDI_FULL_SCALE_NM = 5.0


def apply_to_db(
    msg: NormalizedNavMessage,
    db_write: Callable[[str, object], None],
    cdi_full_scale_nm: float = _DEFAULT_CDI_FULL_SCALE_NM,
) -> None:
    """Write all non-None fields from msg to the FIX database via db_write."""
    pos = msg.position
    if pos.lat_deg is not None:
        db_write("LAT", pos.lat_deg)
    if pos.lon_deg is not None:
        db_write("LONG", pos.lon_deg)
    if pos.alt_ft_msl is not None:
        db_write("GPS_ELLIPSOID_ALT", pos.alt_ft_msl)

    if msg.ground_speed_kt is not None:
        db_write("GS", msg.ground_speed_kt)
    if msg.ground_track_deg_true is not None:
        db_write("TRACK", msg.ground_track_deg_true)
    if msg.desired_track_deg_mag is not None:
        db_write("EXTCRS", msg.desired_track_deg_mag)
    if msg.to_from is not None:
        db_write("EXTTF", msg.to_from)

    # The navigator's own cross-track error has no key yet, so only the
    # deviation it implies is written; XTRACK stays compute's.
    if msg.cross_track_error_nm_signed is not None:
        db_write("EXTCDI", cdi_from_cross_track(msg.cross_track_error_nm_signed, cdi_full_scale_nm))

    if msg.gps_fix_type is not None:
        db_write("GPS_FIX_TYPE", msg.gps_fix_type)

    wp = msg.waypoint
    if wp.ident is not None:
        db_write("WPNAME", wp.ident)
    if wp.lat_deg is not None:
        db_write("WPLAT", wp.lat_deg)
    if wp.lon_deg is not None:
        db_write("WPLON", wp.lon_deg)
