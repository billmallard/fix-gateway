#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Shared great-circle geodesy helpers.
#
#  Extracted from fixgw.plugins.compute (FP2, fix-gateway#23; makerplane/
#  briefs/flight_plan_plan.md section 3.3) so the compute plugin's xte/bearing
#  functions and the fixgw.plugins.flightplan engine share one implementation.
#  The two low-level helpers (_initial_bearing_rad, _great_circle_distance_rad)
#  are byte-for-byte what compute.py used to define inline -- no behaviour
#  change. Cross-track and along-track distance are new, added for FP2.
#
#  Reference fixtures (asserted in tests/test_geo.py to 1e-3 nm / 0.01 deg):
#    (0,0) -> (0,1): 60.040 nm, 090.00
#    KSBA (34.42621,-119.84037) -> KSMX (34.89892,-120.45758): 41.648 nm, 313.13
#    KLAX (33.94250,-118.40810) -> KJFK (40.63990,-73.77870): 2145.908 nm, 065.87
#    (45,-100) -> (46,-100): 60.040 nm, 000.00
#    (20,179.5) -> (20,-179.5): 56.419 nm, 089.83
#  Cross-track on KSBA->KSMX: P(34.60,-120.10) -1.146 nm (left), ATD 16.509;
#  P(34.70,-120.10) +3.246 nm (right), ATD 20.603; P(34.55,-120.25) -8.396 nm,
#  ATD 19.892.

import math

EARTH_RADIUS_NM = 3440.065


def _radians(degrees):
    return math.radians(degrees)


def _initial_bearing_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    lat1 = _radians(lat1_deg)
    lon1 = _radians(lon1_deg)
    lat2 = _radians(lat2_deg)
    lon2 = _radians(lon2_deg)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )
    return math.atan2(y, x)


def _great_circle_distance_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    lat1 = _radians(lat1_deg)
    lon1 = _radians(lon1_deg)
    lat2 = _radians(lat2_deg)
    lon2 = _radians(lon2_deg)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalize_angle_rad(angle):
    # Normalize to [-pi, pi] for stable trig sign behavior.
    return (angle + math.pi) % (2 * math.pi) - math.pi


def bearing_deg(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """Great-circle initial bearing 1 -> 2, degrees true, wrapped [0, 360)."""
    return math.degrees(_initial_bearing_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg)) % 360.0


def distance_nm(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """Great-circle distance 1 -> 2 in nautical miles, R = 3440.065 nm."""
    return _great_circle_distance_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg) * EARTH_RADIUS_NM


def wrap360(deg):
    return deg % 360.0


def cross_track_nm(lat1_deg, lon1_deg, lat2_deg, lon2_deg, lat3_deg, lon3_deg):
    """Signed cross-track distance of point 3 from the great-circle course
    1 -> 2, in nautical miles. Positive = right of course (the XTRACK/FPLXTK
    sign convention).

    Standard spherical cross-track-distance formula (Aviation Formulary /
    Movable Type): dxt = asin(sin(d13) * sin(theta13 - theta12)) * R, where
    d13 is the angular distance 1->3 and theta13/theta12 are the initial
    bearings 1->3 and 1->2.
    """
    if lat1_deg == lat3_deg and lon1_deg == lon3_deg:
        return 0.0
    d13 = _great_circle_distance_rad(lat1_deg, lon1_deg, lat3_deg, lon3_deg)
    theta13 = _initial_bearing_rad(lat1_deg, lon1_deg, lat3_deg, lon3_deg)
    theta12 = _initial_bearing_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg)
    xtd_rad = math.asin(_clamp_unit(math.sin(d13) * math.sin(theta13 - theta12)))
    return xtd_rad * EARTH_RADIUS_NM


def along_track_nm(lat1_deg, lon1_deg, lat2_deg, lon2_deg, lat3_deg, lon3_deg):
    """Signed along-track distance of point 3's projection onto the
    great-circle course 1 -> 2, in nautical miles, measured from point 1.
    Positive = beyond point 1 in the direction of point 2 (i.e. progress made
    good); the TO waypoint is passed abeam when this exceeds distance_nm(1, 2)
    for a leg, or more directly, when the along-track distance TO the TO
    waypoint (computed with 1=TO, 2=FROM-extended-backwards, or equivalently
    via along_track_distance_to_waypoint below) goes negative.
    """
    if lat1_deg == lat3_deg and lon1_deg == lon3_deg:
        return 0.0
    d13 = _great_circle_distance_rad(lat1_deg, lon1_deg, lat3_deg, lon3_deg)
    xtd_rad = math.asin(
        _clamp_unit(
            math.sin(d13)
            * math.sin(
                _initial_bearing_rad(lat1_deg, lon1_deg, lat3_deg, lon3_deg)
                - _initial_bearing_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg)
            )
        )
    )
    cos_d13 = math.cos(d13)
    cos_xtd = math.cos(xtd_rad)
    if cos_xtd == 0:
        atd_rad = 0.0
    else:
        atd_rad = math.acos(_clamp_unit(cos_d13 / cos_xtd))
    # acos always returns a non-negative angle; recover the sign by checking
    # whether point 3 is ahead of or behind point 1 along the course.
    bearing_to_3 = _initial_bearing_rad(lat1_deg, lon1_deg, lat3_deg, lon3_deg)
    course = _initial_bearing_rad(lat1_deg, lon1_deg, lat2_deg, lon2_deg)
    if abs(_normalize_angle_rad(bearing_to_3 - course)) > math.pi / 2:
        atd_rad = -atd_rad
    return atd_rad * EARTH_RADIUS_NM


def destination_point(lat_deg, lon_deg, bearing_deg_, distance_nm):
    """The point reached from (lat_deg, lon_deg) travelling on initial great-
    circle bearing bearing_deg_ (degrees true) for distance_nm nautical
    miles. distance_nm may be negative (the point behind the start on the
    same course)."""
    r = EARTH_RADIUS_NM
    d = distance_nm / r
    brg = math.radians(bearing_deg_)
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    lat2 = math.asin(
        _clamp_unit(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    )
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540.0) % 360.0 - 180.0


def desired_track_true(from_lat, from_lon, to_lat, to_lon, ac_lat, ac_lon):
    """DTK: the true bearing from the aircraft's along-track projection on
    the leg from_->to_ to the TO waypoint (FP2, fix-gateway#23) -- not the
    constant leg bearing, so the needle stays exactly centered on the great
    circle even off-course."""
    if from_lat == to_lat and from_lon == to_lon:
        return 0.0
    initial_brg = bearing_deg(from_lat, from_lon, to_lat, to_lon)
    atd = along_track_nm(from_lat, from_lon, to_lat, to_lon, ac_lat, ac_lon)
    proj_lat, proj_lon = destination_point(from_lat, from_lon, initial_brg, atd)
    return bearing_deg(proj_lat, proj_lon, to_lat, to_lon)


def along_track_distance_to_waypoint_nm(from_lat, from_lon, to_lat, to_lon, ac_lat, ac_lon):
    """Along-track distance remaining from the aircraft to the TO waypoint,
    on the leg FROM -> TO. Positive while the aircraft has not yet reached the
    TO waypoint's abeam point; negative once past it. This is
    distance(FROM,TO) - along_track(FROM,TO,aircraft) restated directly as
    the along-track distance computed with the course reversed (TO -> FROM)
    from the aircraft's position, i.e. how far the aircraft's projection is
    from the TO waypoint, signed so "ahead of the waypoint" is positive.
    """
    return along_track_nm(to_lat, to_lon, from_lat, from_lon, ac_lat, ac_lon)


def _clamp_unit(x):
    return max(-1.0, min(1.0, x))
