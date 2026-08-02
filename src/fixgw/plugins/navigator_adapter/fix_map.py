"""Maps NormalizedNavMessage fields to FIX-Gateway database keys."""

from __future__ import annotations

from typing import Callable

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
    if msg.selected_course_deg is not None:
        db_write("COURSE", msg.selected_course_deg)
    if msg.bearing_to_wp_deg is not None:
        db_write("COURSE", msg.bearing_to_wp_deg)

    if msg.cross_track_error_nm_signed is not None:
        xtrack = msg.cross_track_error_nm_signed
        db_write("XTRACK", xtrack)
        cdi = max(-1.0, min(1.0, -xtrack / cdi_full_scale_nm))
        db_write("CDI", cdi)

    if msg.gps_fix_type is not None:
        db_write("GPS_FIX_TYPE", msg.gps_fix_type)

    wp = msg.waypoint
    if wp.ident is not None:
        db_write("WPNAME", wp.ident)
    if wp.lat_deg is not None:
        db_write("WPLAT", wp.lat_deg)
    if wp.lon_deg is not None:
        db_write("WPLON", wp.lon_deg)
