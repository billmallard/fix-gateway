"""Unit tests for the shared great-circle geodesy helpers (fixgw.geo).

FP2 (fix-gateway#23; makerplane/briefs/flight_plan_plan.md section 3.3): the
five reference fixtures both fix-gateway and pyEfis assert to 1e-3 nm /
0.01 deg, plus cross-track/along-track sign and magnitude on the KSBA->KSMX
leg.
"""

import pytest

import fixgw.geo as geo

KSBA = (34.42621, -119.84037)
KSMX = (34.89892, -120.45758)
KLAX = (33.94250, -118.40810)
KJFK = (40.63990, -73.77870)


@pytest.mark.parametrize(
    "p1,p2,expected_dist,expected_brg",
    [
        ((0.0, 0.0), (0.0, 1.0), 60.040, 90.00),
        (KSBA, KSMX, 41.648, 313.13),
        (KLAX, KJFK, 2145.908, 65.87),
        ((45.0, -100.0), (46.0, -100.0), 60.040, 0.00),
        ((20.0, 179.5), (20.0, -179.5), 56.419, 89.83),
    ],
)
def test_bearing_and_distance_reference_fixtures(p1, p2, expected_dist, expected_brg):
    dist = geo.distance_nm(*p1, *p2)
    brg = geo.bearing_deg(*p1, *p2)
    assert dist == pytest.approx(expected_dist, abs=1e-3)
    assert brg == pytest.approx(expected_brg, abs=0.01)


@pytest.mark.parametrize(
    "point,expected_xtd,expected_atd",
    [
        ((34.60, -120.10), -1.146, 16.509),
        ((34.70, -120.10), 3.246, 20.603),
        ((34.55, -120.25), -8.396, 19.892),
    ],
)
def test_cross_track_and_along_track_reference_fixtures(point, expected_xtd, expected_atd):
    xtd = geo.cross_track_nm(*KSBA, *KSMX, *point)
    atd = geo.along_track_nm(*KSBA, *KSMX, *point)
    assert xtd == pytest.approx(expected_xtd, abs=1e-3)
    assert atd == pytest.approx(expected_atd, abs=1e-3)


def test_cross_track_sign_right_is_positive():
    # P(34.70,-120.10) is right of the KSBA->KSMX course -- positive.
    assert geo.cross_track_nm(*KSBA, *KSMX, 34.70, -120.10) > 0
    # P(34.60,-120.10) is left of course -- negative.
    assert geo.cross_track_nm(*KSBA, *KSMX, 34.60, -120.10) < 0


def test_cross_track_zero_at_endpoints():
    assert geo.cross_track_nm(*KSBA, *KSMX, *KSBA) == pytest.approx(0.0, abs=1e-9)


def test_along_track_distance_to_waypoint_positive_before_zero_at_negative_after():
    # Aircraft short of KSMX: still approaching, positive.
    before = geo.along_track_distance_to_waypoint_nm(*KSBA, *KSMX, 34.60, -120.10)
    assert before > 0

    # Aircraft essentially at KSMX: ~0.
    at_wp = geo.along_track_distance_to_waypoint_nm(*KSBA, *KSMX, *KSMX)
    assert at_wp == pytest.approx(0.0, abs=1e-6)

    # Aircraft beyond KSMX on the same course: negative.
    brg = geo.bearing_deg(*KSBA, *KSMX)
    far_lat, far_lon = _destination(*KSMX, brg, 10.0)
    past = geo.along_track_distance_to_waypoint_nm(*KSBA, *KSMX, far_lat, far_lon)
    assert past < 0
    assert past == pytest.approx(-10.0, abs=0.05)


def test_wrap360():
    assert geo.wrap360(370.0) == pytest.approx(10.0)
    assert geo.wrap360(-10.0) == pytest.approx(350.0)
    assert geo.wrap360(0.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# arc_sweep_deg (PA13 -- RF/AF constant-radius arc guidance)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_brg,to_brg,clockwise,expected",
    [
        (0.0, 90.0, True, 90.0),      # quarter circle, clockwise
        (90.0, 0.0, True, 270.0),     # the long way around, clockwise
        (0.0, 90.0, False, 270.0),    # same pair, counterclockwise is the long way
        (90.0, 0.0, False, 90.0),
        (350.0, 10.0, True, 20.0),    # sweep across the 0/360 wrap
        (10.0, 350.0, False, 20.0),
        (45.0, 45.0, True, 0.0),      # coincident radials -- no sweep
    ],
)
def test_arc_sweep_deg(from_brg, to_brg, clockwise, expected):
    assert geo.arc_sweep_deg(from_brg, to_brg, clockwise) == pytest.approx(expected)


def _destination(lat_deg, lon_deg, bearing_deg_, dist_nm):
    import math

    r = geo.EARTH_RADIUS_NM
    d = dist_nm / r
    brg = math.radians(bearing_deg_)
    lat1 = math.radians(lat_deg)
    lon1 = math.radians(lon_deg)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


# destination_point_wgs84 (AER-4218). Expected values from GeographicLib 2.1:
#   Geodesic.WGS84.Direct(lat, lon, az, nm * 1852) -> lat2, lon2
@pytest.mark.parametrize(
    "start,az,nm,expected",
    [
        ((39.0, -104.9), 124.0, 5.0, (38.953322910, -104.811437508)),
        ((18.4, -66.0), 300.0, 11.0, (18.491953014, -66.167058929)),
        ((64.8, -147.7), 45.0, 7.0, (64.882096226, -147.506536669)),
        ((0.0, 0.0), 90.0, 60.0, (0.0, 0.998207944)),
        ((34.42621, -119.84037), 0.0, 0.0, (34.42621, -119.84037)),
    ],
)
def test_destination_point_wgs84_reference_fixtures(start, az, nm, expected):
    lat, lon = geo.destination_point_wgs84(*start, az, nm)
    assert lat == pytest.approx(expected[0], abs=1e-9)
    assert lon == pytest.approx(expected[1], abs=1e-9)


def test_destination_point_wgs84_agrees_with_geographiclib_everywhere():
    # Random starts, courses and lengths from 0 to 5000 nm: under a
    # millimetre from GeographicLib (measured: 0.06 mm).
    import random

    gl = pytest.importorskip("geographiclib.geodesic").Geodesic.WGS84
    rng = random.Random(4218)
    for _ in range(2000):
        lat, lon = rng.uniform(-89.9, 89.9), rng.uniform(-180.0, 180.0)
        az, nm = rng.uniform(0.0, 360.0), rng.choice([0.001, 0.5, 5.0, 11.0, 50.0, 500.0, 5000.0])
        p = geo.destination_point_wgs84(lat, lon, az, nm)
        r = gl.Direct(lat, lon, az, nm * 1852.0)
        assert gl.Inverse(p[0], p[1], r["lat2"], r["lon2"])["s12"] < 0.001


def test_the_sphere_misplaces_a_hold_turn_in_point_wgs84_does_not():
    # The reason it exists: 11 nm south of a fix at 18N the sphere is
    # 0.05 nm off the WGS-84 point, which on a 0.58 nm hold turn is a
    # 5 deg swing of the tangent.
    gl = pytest.importorskip("geographiclib.geodesic").Geodesic.WGS84
    start, az, nm = (18.4, -66.0), 180.0, 11.0
    r = gl.Direct(*start, az, nm * 1852.0)
    sphere = geo.destination_point(*start, az, nm)
    wgs = geo.destination_point_wgs84(*start, az, nm)
    assert gl.Inverse(*sphere, r["lat2"], r["lon2"])["s12"] / 1852.0 > 0.04
    assert gl.Inverse(*wgs, r["lat2"], r["lon2"])["s12"] < 0.001
