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
        self.assertEqual(msg.gps_fix_type, 1)
        self.assertAlmostEqual(msg.position.lat_deg, 48.1173, places=3)
        self.assertAlmostEqual(msg.position.alt_ft_msl, 545.4 * 3.28084, places=0)

    def test_gga_no_fix_returns_fix_type_zero(self):
        # Use a mock to avoid NMEA checksum dependency for the no-fix case
        from unittest.mock import MagicMock
        msg = MagicMock()
        msg.gps_qual = "0"
        msg.altitude = None
        result = self.adapter._parse_gga(msg)
        self.assertIsNotNone(result)
        self.assertEqual(result.gps_fix_type, 0)
        self.assertIsNone(result.position.lat_deg)

    def test_dgps_fix_type_2(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
        msgs = self._parse(sentence)
        self.assertEqual(msgs[0].gps_fix_type, 2)


class TestParseRmb(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def _mock_rmb(self, status, cross_track_err, dir_steer):
        msg = MagicMock()
        msg.status = status
        msg.cross_track_err = cross_track_err
        msg.dir_steer = dir_steer
        return msg

    def test_steer_left_gives_positive_xtrack(self):
        # 'L' = aircraft is RIGHT of track → positive XTE
        raw_msg = self._mock_rmb("A", 3.0, "L")
        result = self.adapter._parse_rmb(raw_msg)
        self.assertAlmostEqual(result.cross_track_error_nm_signed, 3.0, places=4)

    def test_steer_right_gives_negative_xtrack(self):
        # 'R' = aircraft is LEFT of track → negative XTE
        raw_msg = self._mock_rmb("A", 3.0, "R")
        result = self.adapter._parse_rmb(raw_msg)
        self.assertAlmostEqual(result.cross_track_error_nm_signed, -3.0, places=4)

    def test_void_status_returns_none(self):
        raw_msg = self._mock_rmb("V", 3.0, "L")
        self.assertIsNone(self.adapter._parse_rmb(raw_msg))

    def test_zero_xte_preserved(self):
        raw_msg = self._mock_rmb("A", 0.0, "L")
        result = self.adapter._parse_rmb(raw_msg)
        self.assertAlmostEqual(result.cross_track_error_nm_signed, 0.0, places=4)


class TestParseApb(unittest.TestCase):
    def setUp(self):
        self.adapter = GarminNmeaAdapter()

    def test_apb_returns_bearing_to_wp(self):
        msg = MagicMock()
        msg.bearing_present_dest = 270.0
        result = self.adapter._parse_apb(msg)
        self.assertAlmostEqual(result.bearing_to_wp_deg, 270.0, places=1)

    def test_apb_none_bearing_returns_none(self):
        msg = MagicMock()
        msg.bearing_present_dest = None
        self.assertIsNone(self.adapter._parse_apb(msg))


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
