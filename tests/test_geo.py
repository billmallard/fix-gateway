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
