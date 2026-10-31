"""Tests for the GarminNmeaAdapter ParserAdapter implementation.

Covers parse_line() for all four sentence types, sign convention,
error handling (void status, corrupt frames), health() counters,
and reset().
"""

import unittest
from unittest.mock import MagicMock

from fixgw.plugins.navigator_adapter.adapters.garmin_nmea import GarminNmeaAdapter


class TestGarminNmeaAdapterMeta(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def test_name(self):
        self.assertEqual(self.adapter.name(), "garmin_nmea")

    def test_supported_protocols_is_list(self):
        self.assertIsInstance(self.adapter.supported_protocols(), list)
        self.assertTrue(len(self.adapter.supported_protocols()) > 0)


class TestParseRmc(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def _parse(self, sentence):
        return self.adapter.parse_line(sentence.encode())

    def test_active_rmc_returns_position_and_kinematics(self):
        sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        msgs = self._parse(sentence)
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        self.assertAlmostEqual(msg.position.lat_deg, 48.1173, places=3)
        self.assertAlmostEqual(msg.position.lon_deg, 11.5167, places=3)
        self.assertAlmostEqual(msg.ground_speed_kt, 22.4, places=1)
        self.assertAlmostEqual(msg.ground_track_deg_true, 84.4, places=1)

    def test_void_rmc_returns_empty(self):
        sentence = "$GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*3D"
        msgs = self._parse(sentence)
        self.assertEqual(len(msgs), 0)

    def test_corrupt_line_returns_empty_and_increments_error(self):
        # A line with no '$' is guaranteed to fail pynmea2 parsing
        msgs = self.adapter.parse_line(b"NOT AN NMEA SENTENCE AT ALL!!!")
        self.assertEqual(msgs, [])
        self.assertGreater(self.adapter.health().parse_errors, 0)


class TestParseGga(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def _parse(self, sentence):
        return self.adapter.parse_line(sentence.encode())

    def test_gga_with_fix_returns_position_and_altitude(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
        msgs = self._parse(sentence)
        self.assertEqual(len(msgs), 1)
        msg = msgs[0]
        # GGA quality 1 with 8 satellites is a 3D fix on the GPS_FIX_TYPE scale.
        self.assertEqual(msg.gps_fix_type, 3)
        self.assertAlmostEqual(msg.position.lat_deg, 48.1173, places=3)
        self.assertAlmostEqual(msg.position.alt_ft_msl, 545.4 * 3.28084, places=0)

    def test_gga_no_fix_returns_fix_type_zero(self):
        # Use a mock to avoid NMEA checksum dependency for the no-fix case
        from unittest.mock import MagicMock
        msg = MagicMock()
        msg.gps_qual = "0"
        msg.num_sats = "00"
        msg.altitude = None
        result = self.adapter._parse_gga(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.gps_fix_type, 0)
        self.assertIsNone(result.position.lat_deg)

    def test_waas_fix_is_3d_dgps(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
        msgs = self._parse(sentence)
        self.assertEqual(msgs[0].gps_fix_type, 4)

    def test_gsa_mode_decides_2d_for_the_next_gga(self):
        # Real stream: GSA reports 2D, then GGA with 8 satellites. GSA wins.
        self.assertEqual(self._parse("$GPGSA,A,2,04,05,09,,,,,,,,,,3.1,2.9,1.0*33"), [])
        msgs = self._parse("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
        self.assertEqual(msgs[0].gps_fix_type, 2)
        # The GSA was consumed; the next GGA alone falls back to the satellite count.
        msgs = self._parse("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
        self.assertEqual(msgs[0].gps_fix_type, 3)

    def test_reset_drops_a_held_gsa(self):
        self._parse("$GPGSA,A,2,04,05,09,,,,,,,,,,3.1,2.9,1.0*33")
        self.adapter.reset("test")
        msgs = self._parse("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
        self.assertEqual(msgs[0].gps_fix_type, 3)


def _nmea(body):
    """A checksummed NMEA sentence from its body (between '$' and '*')."""
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


# RMB and APB are tested with real sentences only: the adapter once read
# attributes pynmea2 does not have, and MagicMock tests with the same invented
# names passed while every real sentence produced nothing (AER-4473).
RMB_BODY = "GPRMB,{status},{xte},{steer},ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V"
APB_BODY = "GPAPB,{status},A,0.50,L,N,{perp},{perp},{dtk},{typ},DEST,095.0,{typ},092.0,{typ}"


class TestParseRmb(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def _rmb(self, status="A", xte="3.00", steer="L"):
        return self.adapter.parse_line(_nmea(RMB_BODY.format(status=status, xte=xte, steer=steer)).encode())

    def test_steer_left_gives_positive_xtrack(self):
        # 'L' = aircraft is RIGHT of track → positive XTE
        (msg,) = self._rmb(steer="L")
        self.assertAlmostEqual(msg.cross_track_error_nm_signed, 3.0, places=4)

    def test_steer_right_gives_negative_xtrack(self):
        # 'R' = aircraft is LEFT of track → negative XTE
        (msg,) = self._rmb(steer="R")
        self.assertAlmostEqual(msg.cross_track_error_nm_signed, -3.0, places=4)

    def test_void_missing_or_bad_returns_nothing(self):
        self.assertEqual(self._rmb(status="V"), [])
        self.assertEqual(self._rmb(xte=""), [])
        self.assertEqual(self._rmb(steer="X"), [])
        self.assertEqual(self.adapter.health().parse_errors, 0)

    def test_zero_xte_preserved(self):
        (msg,) = self._rmb(xte="0.00")
        self.assertAlmostEqual(msg.cross_track_error_nm_signed, 0.0, places=4)


class TestParseApb(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def _apb(self, status="A", perp="V", dtk="087.0", typ="M"):
        body = APB_BODY.format(status=status, perp=perp, dtk=dtk, typ=typ)
        return self.adapter.parse_line(_nmea(body).encode())

    def test_desired_track_is_origin_to_destination_bearing(self):
        # Field 8 (087, the leg DTK), not field 11 (095, bearing from here).
        (msg,) = self._apb()
        self.assertAlmostEqual(msg.desired_track_deg_mag, 87.0, places=3)
        self.assertEqual(msg.to_from, 1)

    def test_perpendicular_passed_is_from(self):
        (msg,) = self._apb(perp="A")
        self.assertEqual(msg.to_from, 2)

    def test_true_desired_track_uses_rmc_variation(self):
        self.adapter.parse_line(b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")
        (msg,) = self._apb(dtk="090.0", typ="T")
        self.assertAlmostEqual(msg.desired_track_deg_mag, 93.1, places=3)

    def test_true_desired_track_without_variation_is_dropped(self):
        (msg,) = self._apb(dtk="090.0", typ="T")
        self.assertIsNone(msg.desired_track_deg_mag)
        self.assertEqual(msg.to_from, 1)

    def test_invalid_apb_is_off(self):
        (msg,) = self._apb(status="V")
        self.assertIsNone(msg.desired_track_deg_mag)
        self.assertEqual(msg.to_from, 0)

    def test_reset_drops_held_variation(self):
        self.adapter.parse_line(b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")
        self.adapter.reset("test")
        (msg,) = self._apb(dtk="090.0", typ="T")
        self.assertIsNone(msg.desired_track_deg_mag)


class TestHealthAndReset(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def test_health_starts_clean(self):
        h = self.adapter.health()
        self.assertEqual(h.parse_errors, 0)
        self.assertFalse(h.stale)
        self.assertFalse(h.failed)
        self.assertIsNone(h.last_sentence_type)

    def test_parse_error_increments_count(self):
        self.adapter.parse_line(b"not-nmea")
        self.assertGreater(self.adapter.health().parse_errors, 0)

    def test_reset_clears_error_count(self):
        self.adapter.parse_line(b"garbage")
        self.assertGreater(self.adapter.health().parse_errors, 0)
        self.adapter.reset("test")
        self.assertEqual(self.adapter.health().parse_errors, 0)

    def test_last_sentence_type_updated(self):
        sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        self.adapter.parse_line(sentence.encode())
        self.assertEqual(self.adapter.health().last_sentence_type, "RMC")


if __name__ == "__main__":
    unittest.main()
