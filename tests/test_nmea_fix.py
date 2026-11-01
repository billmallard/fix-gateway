#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  fixgw.nmea_fix.gps_fix_type: NMEA GGA quality + GSA mode -> the
#  GPS_FIX_TYPE contract scale (0-1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK).
#  Every case is driven from a real, checksummed NMEA sentence (AER-4224).

import pynmea2
import pytest

from fixgw.nmea_fix import apb_guidance, cdi_from_cross_track, gps_fix_type, rmc_magvar_east

GGA_GPS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
GGA_WAAS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
GGA_GPS_3 = "$GPGGA,123519,4807.038,N,01131.000,E,1,03,2.9,545.4,M,46.9,M,,*4E"
GGA_NONE = "$GPGGA,123519,,,,,0,00,,,M,,M,,*6B"
GGA_DR_5 = "$GPGGA,123519,4807.038,N,01131.000,E,6,05,1.5,545.4,M,46.9,M,,*40"
GGA_RTK_12 = "$GPGGA,123519,4807.038,N,01131.000,E,4,12,0.6,545.4,M,46.9,M,1.0,0031*6B"
GSA_3D = "$GPGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*39"
GSA_2D = "$GPGSA,A,2,04,05,09,,,,,,,,,,3.1,2.9,1.0*33"
GSA_NONE = "$GPGSA,A,1,,,,,,,,,,,,,,,*1E"


def _fix_type(gga, gsa=None):
    g = pynmea2.parse(gga, check=True)
    mode = pynmea2.parse(gsa, check=True).mode_fix_type if gsa else None
    return gps_fix_type(g.gps_qual, g.num_sats, mode)


@pytest.mark.parametrize(
    "gga, gsa, expected",
    [
        # GGA only (no GSA in the stream): satellite count decides 2D/3D.
        (GGA_GPS_8, None, 3),
        (GGA_WAAS_8, None, 4),
        (GGA_GPS_3, None, 2),
        (GGA_RTK_12, None, 5),
        (GGA_NONE, None, 0),
        # Dead reckoning is not a GNSS fix, whatever the satellite count.
        (GGA_DR_5, None, 0),
        # GSA present: its mode is authoritative for 2D/3D.
        (GGA_GPS_8, GSA_3D, 3),
        (GGA_WAAS_8, GSA_3D, 4),
        (GGA_GPS_8, GSA_2D, 2),
        (GGA_WAAS_8, GSA_2D, 2),
        (GGA_GPS_8, GSA_NONE, 1),
        # GGA quality 0 is no fix even if a GSA still says 3D.
        (GGA_NONE, GSA_3D, 0),
    ],
)
def test_real_sentences_map_to_contract(gga, gsa, expected):
    assert _fix_type(gga, gsa) == expected


def test_valid_gps_and_waas_fixes_pass_a_3d_gate():
    # The flight-plan engine's integrity gate is GPS_FIX_TYPE >= 3; the raw GGA
    # codes (1, 2) used to fail it on every valid fix.
    assert _fix_type(GGA_GPS_8) >= 3
    assert _fix_type(GGA_WAAS_8) >= 3


@pytest.mark.parametrize("qual, sats", [(None, None), ("", ""), ("x", "08")])
def test_missing_or_garbled_quality_is_no_fix(qual, sats):
    assert gps_fix_type(qual, sats) == 0


def test_missing_satellite_count_is_2d():
    assert gps_fix_type(1, None) == 2
    assert gps_fix_type(1, "") == 2


# External-navigator guidance helpers (AER-4473), from real sentences.


def _nmea(body):
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return pynmea2.parse(f"${body}*{checksum:02X}", check=True)


def _rmc(var, var_dir):
    return _nmea(f"GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,{var},{var_dir}")


def _apb(dtk, typ, status="A", perp="V"):
    return _nmea(f"GPAPB,{status},A,0.50,L,N,{perp},{perp},{dtk},{typ},DEST,095.0,{typ},092.0,{typ}")


def test_rmc_variation_is_east_positive_and_absent_is_none():
    assert rmc_magvar_east(_rmc("003.1", "W")) == pytest.approx(-3.1)
    assert rmc_magvar_east(_rmc("011.9", "E")) == pytest.approx(11.9)
    assert rmc_magvar_east(_rmc("", "")) is None


def test_true_desired_track_wraps_through_north():
    # 001 T with 3 E variation is 358 M; 359 T with 3 W is 002 M.
    assert apb_guidance(_apb("001.0", "T"), 3.0)[0] == pytest.approx(358.0)
    assert apb_guidance(_apb("359.0", "T"), -3.0)[0] == pytest.approx(2.0)


def test_magnetic_desired_track_ignores_variation():
    assert apb_guidance(_apb("087.0", "M"), 20.0) == (pytest.approx(87.0), 1)


def test_unknown_bearing_type_is_not_a_course():
    assert apb_guidance(_apb("087.0", ""), 3.0) == (None, 1)


def test_cdi_is_fplcdi_convention_and_clamped():
    assert cdi_from_cross_track(0.15, 0.3) == pytest.approx(-0.5)
    assert cdi_from_cross_track(-9.0, 5.0) == 1.0
