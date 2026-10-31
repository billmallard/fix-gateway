#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  fixgw.nmea_fix.gps_fix_type: NMEA GGA quality + GSA mode -> the
#  GPS_FIX_TYPE contract scale (0-1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK).
#  Every case is driven from a real, checksummed NMEA sentence (AER-4224).

import pynmea2
import pytest

from fixgw.nmea_fix import gps_fix_type

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
