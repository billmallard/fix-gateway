#  Copyright (c) 2026 Bill Mallard
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.

"""Unit tests for the garmin_gnx375 FIX-Gateway plugin.

Tests exercise the NMEA sentence parsers (_handle_rmc, _handle_gga,
_handle_rmb, _handle_apb) by bypassing the serial port entirely and
driving MainThread._dispatch() directly with parsed pynmea2 objects.
RMB and APB are only ever tested with real sentences, never mocks.
"""

import io
import unittest
from unittest.mock import MagicMock

import pynmea2

import fixgw.database as database
import fixgw.plugins.garmin_gnx375 as garmin_gnx375
from fixgw.plugins.garmin_gnx375 import MainThread, Plugin

DB_CONFIG = """
variables:
  e: 1
  c: 1
  a: 1
  b: 1
  r: 1
  t: 1

entries:
- key: LAT
  description: Latitude
  type: float
  min: -90.0
  max: 90.0
  units: deg
  initial: 0.0
  tol: 2000

- key: LONG
  description: Longitude
  type: float
  min: -180.0
  max: 180.0
  units: deg
  initial: 0.0
  tol: 2000

- key: GS
  description: Ground Speed
  type: float
  min: 0.0
  max: 9999.0
  units: knots
  initial: 0.0
  tol: 2000

- key: TRACK
  description: Track
  type: float
  min: 0.0
  max: 360.0
  units: deg
  initial: 0.0
  tol: 2000

- key: GPS_ELLIPSOID_ALT
  description: GPS Ellipsoid Altitude
  type: float
  min: -1500.0
  max: 60000.0
  units: ft
  initial: 0.0
  tol: 2000

- key: GPS_FIX_TYPE
  description: GPS Fix Type
  type: int
  min: 0
  max: 9
  initial: 0
  tol: 2000

- key: XTRACK
  description: Cross-Track Error
  type: float
  min: -100.0
  max: 100.0
  units: nM
  initial: 0.0
  tol: 2000

- key: CDI
  description: Course Deviation Indicator
  type: float
  min: -1.0
  max: 1.0
  initial: 0.0
  tol: 2000

- key: COURSE
  description: Course to Destination
  type: float
  min: 0.0
  max: 360.0
  units: deg
  initial: 0.0
  tol: 2000

- key: EXTCRS
  description: External navigator course (magnetic)
  type: float
  min: 0.0
  max: 359.9
  units: deg
  initial: 0.0
  tol: 5000

- key: EXTCDI
  description: External navigator course deviation
  type: float
  min: -1.0
  max: 1.0
  initial: 0.0
  tol: 5000

- key: EXTTF
  description: "External navigator TO/FROM (0 off, 1 TO, 2 FROM)"
  type: int
  min: 0
  max: 2
  initial: 0
  tol: 5000
"""

_CDI_FULL_SCALE = 5.0  # nm, default for these tests


def _make_thread():
    """Return a MainThread with a mock parent that writes to the real FIX DB."""
    parent = MagicMock()
    parent.config = {
        "port": "/dev/null",
        "baud": "9600",
        "cdi_full_scale_nm": str(_CDI_FULL_SCALE),
    }
    parent.log = MagicMock()

    thread = MainThread(parent)

    # Route db_write calls to the real database
    def _db_write(key, value):
        database.write(key, value)

    thread.parent.db_write.side_effect = _db_write
    return thread


def _db_value(key):
    """Return the raw scalar value for a database key."""
    result = database.read(key)
    return result[0]


class TestRmcParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_active_rmc_writes_lat_lon_gs_track(self):
        sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        msg = pynmea2.parse(sentence)
        self.thread._handle_rmc(msg)
        self.assertAlmostEqual(_db_value("LAT"), 48.1173, places=3)
        self.assertAlmostEqual(_db_value("LONG"), 11.5167, places=3)
        self.assertAlmostEqual(_db_value("GS"), 22.4, places=1)
        self.assertAlmostEqual(_db_value("TRACK"), 84.4, places=1)

    def test_void_rmc_does_not_write(self):
        msg = MagicMock()
        msg.status = "V"
        initial_lat = _db_value("LAT")
        self.thread._handle_rmc(msg)
        self.assertEqual(_db_value("LAT"), initial_lat)

    def test_rmc_skips_missing_speed_and_track(self):
        msg = MagicMock()
        msg.status = "A"
        msg.latitude = 12.5
        msg.longitude = -45.25
        msg.spd_over_grnd = None
        msg.true_course = None

        self.thread._handle_rmc(msg)

        self.assertAlmostEqual(_db_value("LAT"), 12.5)
        self.assertAlmostEqual(_db_value("LONG"), -45.25)
        self.assertEqual(_db_value("GS"), 0.0)
        self.assertEqual(_db_value("TRACK"), 0.0)


class TestGgaParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_gga_fix_writes_position_and_altitude(self):
        # Altitude 545.4 m → 1789.1 ft
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
        msg = pynmea2.parse(sentence)
        self.thread._handle_gga(msg)
        self.assertAlmostEqual(_db_value("LAT"), 48.1173, places=3)
        self.assertAlmostEqual(_db_value("LONG"), 11.5167, places=3)
        self.assertAlmostEqual(_db_value("GPS_ELLIPSOID_ALT"), 545.4 * 3.28084, places=0)
        # GGA quality 1 with 8 satellites is a 3D fix on the GPS_FIX_TYPE scale.
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 3)

    def test_gga_no_fix_skips_position(self):
        msg = MagicMock()
        msg.gps_qual = "0"
        msg.num_sats = "00"
        msg.altitude = None
        initial_lat = _db_value("LAT")
        self.thread._handle_gga(msg)
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 0)
        self.assertEqual(_db_value("LAT"), initial_lat)

    def test_waas_fix_is_3d_dgps(self):
        sentence = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
        msg = pynmea2.parse(sentence)
        self.thread._handle_gga(msg)
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 4)

    def test_gga_fix_without_altitude_skips_altitude(self):
        msg = MagicMock()
        msg.gps_qual = "1"
        msg.num_sats = "08"
        msg.latitude = 10.0
        msg.longitude = 20.0
        msg.altitude = None

        self.thread._handle_gga(msg)

        self.assertEqual(_db_value("GPS_FIX_TYPE"), 3)
        self.assertEqual(_db_value("GPS_ELLIPSOID_ALT"), 0.0)


class TestFixTypeFromSentenceStream(unittest.TestCase):
    """GPS_FIX_TYPE from real, checksummed sentences through _dispatch (AER-4224)."""

    GGA_GPS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
    GGA_WAAS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
    GGA_GPS_3 = "$GPGGA,123519,4807.038,N,01131.000,E,1,03,2.9,545.4,M,46.9,M,,*4E"
    GSA_2D = "$GPGSA,A,2,04,05,09,,,,,,,,,,3.1,2.9,1.0*33"
    GNGSA_3D = "$GNGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*27"

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def _feed(self, *sentences):
        for s in sentences:
            self.thread._dispatch(pynmea2.parse(s, check=True), _CDI_FULL_SCALE)
        return _db_value("GPS_FIX_TYPE")

    def test_gga_only_stream_passes_the_3d_gate(self):
        # The flight-plan engine requires GPS_FIX_TYPE >= 3.
        self.assertEqual(self._feed(self.GGA_GPS_8), 3)
        self.assertEqual(self._feed(self.GGA_WAAS_8), 4)

    def test_three_satellites_is_2d(self):
        self.assertEqual(self._feed(self.GGA_GPS_3), 2)

    def test_gsa_mode_overrides_satellite_count_for_one_epoch(self):
        self.assertEqual(self._feed(self.GSA_2D, self.GGA_GPS_8), 2)
        # No GSA before this GGA: back to the satellite count.
        self.assertEqual(self._feed(self.GGA_GPS_8), 3)

    def test_multi_constellation_gsa_talker_is_used(self):
        self.assertEqual(self._feed(self.GNGSA_3D, self.GGA_GPS_3), 3)


def _nmea(body):
    """A checksummed NMEA sentence from its body (between '$' and '*')."""
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


# Real sentences, parsed by pynmea2 with the checksum checked. The plugin once
# read RMB/APB attributes pynmea2 does not have, and tests that fed MagicMocks
# with the same invented names passed while every real sentence wrote nothing.
RMB_RIGHT_OF_TRACK = _nmea("GPRMB,A,3.00,L,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
RMB_LEFT_OF_TRACK = _nmea("GPRMB,A,3.00,R,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
RMB_FAR_RIGHT = _nmea("GPRMB,A,20.00,L,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
RMB_VOID = _nmea("GPRMB,V,3.00,L,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
RMB_NO_XTE = _nmea("GPRMB,A,,L,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
RMB_BAD_STEER = _nmea("GPRMB,A,3.00,X,ORIG,DEST,4000.000,N,08800.000,W,5.0,090.0,120.0,V")
# Leg DTK 087 M; present-position bearing 095 M, so the two are told apart.
APB_TO_MAG = _nmea("GPAPB,A,A,0.50,L,N,V,V,087.0,M,DEST,095.0,M,092.0,M")
APB_FROM_MAG = _nmea("GPAPB,A,A,0.50,L,N,A,A,087.0,M,DEST,267.0,M,092.0,M")
APB_TO_TRUE = _nmea("GPAPB,A,A,0.50,L,N,V,V,090.0,T,DEST,098.0,T,095.0,T")
APB_INVALID = _nmea("GPAPB,V,A,0.50,L,N,V,V,087.0,M,DEST,095.0,M,092.0,M")
RMC_VAR_3W = _nmea("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W")


def _feed(thread, *sentences):
    for s in sentences:
        thread._dispatch(pynmea2.parse(s, check=True), _CDI_FULL_SCALE)


class TestRmbParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_steer_left_is_right_of_track_and_deflects_needle_left(self):
        # 'L' (steer left) -> aircraft RIGHT of track -> +3 nm -> CDI -3/5
        _feed(self.thread, RMB_RIGHT_OF_TRACK)
        self.assertAlmostEqual(_db_value("EXTCDI"), -3.0 / _CDI_FULL_SCALE, places=4)

    def test_steer_right_is_left_of_track_and_deflects_needle_right(self):
        _feed(self.thread, RMB_LEFT_OF_TRACK)
        self.assertAlmostEqual(_db_value("EXTCDI"), 3.0 / _CDI_FULL_SCALE, places=4)

    def test_large_xte_clips_cdi_to_full_scale(self):
        _feed(self.thread, RMB_FAR_RIGHT)
        self.assertAlmostEqual(_db_value("EXTCDI"), -1.0, places=4)

    def test_void_missing_or_bad_rmb_does_not_write(self):
        for sentence in (RMB_VOID, RMB_NO_XTE, RMB_BAD_STEER):
            database.init(io.StringIO(DB_CONFIG))
            self.thread = _make_thread()
            _feed(self.thread, sentence)
            self.thread.parent.db_write.assert_not_called()

    def test_configured_scale_is_used(self):
        self.thread._dispatch(pynmea2.parse(RMB_RIGHT_OF_TRACK, check=True), 6.0)
        self.assertAlmostEqual(_db_value("EXTCDI"), -0.5, places=4)


class TestApbParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_course_is_leg_desired_track_not_bearing_to_destination(self):
        _feed(self.thread, APB_TO_MAG)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)
        self.assertEqual(_db_value("EXTTF"), 1)

    def test_perpendicular_passed_is_from(self):
        _feed(self.thread, APB_FROM_MAG)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)
        self.assertEqual(_db_value("EXTTF"), 2)

    def test_true_desired_track_is_made_magnetic_with_rmc_variation(self):
        # 090 T with 3.1 W variation is 093.1 M.
        _feed(self.thread, RMC_VAR_3W, APB_TO_TRUE)
        self.assertAlmostEqual(_db_value("EXTCRS"), 93.1, places=3)

    def test_true_desired_track_without_variation_is_not_written(self):
        _feed(self.thread, APB_TO_TRUE)
        self.assertEqual(_db_value("EXTCRS"), 0.0)
        self.assertEqual(_db_value("EXTTF"), 1)

    def test_invalid_apb_sets_off_and_leaves_course(self):
        _feed(self.thread, APB_TO_MAG, APB_INVALID)
        self.assertEqual(_db_value("EXTTF"), 0)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)


class TestExternalNavigatorKeysOnly(unittest.TestCase):
    """AER-4473: compute owns COURSE/CDI/XTRACK; the GNX feeds only EXT*."""

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_full_sentence_stream_never_writes_compute_owned_keys(self):
        _feed(
            self.thread,
            RMC_VAR_3W,
            "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47",
            RMB_RIGHT_OF_TRACK,
            APB_TO_MAG,
            APB_FROM_MAG,
            APB_TO_TRUE,
        )
        written = {c.args[0] for c in self.thread.parent.db_write.call_args_list}
        self.assertTrue({"EXTCDI", "EXTCRS", "EXTTF"} <= written)
        self.assertFalse({"COURSE", "CDI", "XTRACK"} & written)


class TestDispatch(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def test_dispatch_routes_known_sentence_types_and_ignores_unknown(self):
        handlers = {
            "_handle_rmc": MagicMock(),
            "_handle_gga": MagicMock(),
            "_handle_rmb": MagicMock(),
            "_handle_apb": MagicMock(),
            "_handle_gsa": MagicMock(),
        }
        for name, handler in handlers.items():
            setattr(self.thread, name, handler)

        for sentence_type in ["RMC", "GGA", "RMB", "APB", "GSA", "TXT"]:
            msg = MagicMock()
            msg.sentence_type = sentence_type
            self.thread._dispatch(msg, _CDI_FULL_SCALE)

        handlers["_handle_rmc"].assert_called_once()
        handlers["_handle_gga"].assert_called_once()
        handlers["_handle_rmb"].assert_called_once()
        handlers["_handle_apb"].assert_called_once()
        handlers["_handle_gsa"].assert_called_once()


class TestSerialRunLoop(unittest.TestCase):
    def setUp(self):
        self.parent = MagicMock()
        self.parent.config = {"port": "/dev/null"}
        self.parent.log = MagicMock()
        self.thread = MainThread(self.parent)

    def _patch_serial(self, factory):
        original = garmin_gnx375.serial.Serial
        garmin_gnx375.serial.Serial = factory
        return original

    def test_run_logs_open_failure(self):
        def serial_factory(*args, **kwargs):
            raise garmin_gnx375.serial.SerialException("no port")

        original = self._patch_serial(serial_factory)
        try:
            self.thread.run()
        finally:
            garmin_gnx375.serial.Serial = original

        self.parent.log.error.assert_called_once()
        self.assertIn("cannot open /dev/null", self.parent.log.error.call_args.args[0])

    def test_run_reads_parses_dispatches_and_closes_serial(self):
        class FakeSerial:
            def __init__(self):
                self.is_open = True
                self.closed = False
                self.calls = 0

            def readline(self):
                self.calls += 1
                if self.calls == 1:
                    return b""
                self.thread.getout = True
                return b"$GPRMC,ignored*00\r\n"

            def close(self):
                self.closed = True
                self.is_open = False

        fake_serial = FakeSerial()
        fake_serial.thread = self.thread
        parsed = MagicMock()
        parsed.sentence_type = "RMC"
        self.thread._dispatch = MagicMock()
        parse_mock = MagicMock(return_value=parsed)
        original_serial = self._patch_serial(
            lambda port, baud, timeout: fake_serial
        )
        original_parse = garmin_gnx375.pynmea2.parse
        garmin_gnx375.pynmea2.parse = parse_mock
        try:
            self.thread.run()
        finally:
            garmin_gnx375.serial.Serial = original_serial
            garmin_gnx375.pynmea2.parse = original_parse

        parse_mock.assert_called_once_with("$GPRMC,ignored*00")
        self.thread._dispatch.assert_called_once_with(parsed, 5.0)
        self.assertTrue(fake_serial.closed)

    def test_run_uses_configured_baud_and_cdi_scale(self):
        self.parent.config = {
            "port": "/dev/ttyS1",
            "baud": "4800",
            "cdi_full_scale_nm": "0.3",
        }
        self.thread = MainThread(self.parent)

        class FakeSerial:
            is_open = False

            def readline(self):
                self.thread.getout = True
                return b"$GPRMC,ignored*00\n"

        fake_serial = FakeSerial()
        fake_serial.thread = self.thread
        parsed = MagicMock()
        self.thread._dispatch = MagicMock()
        original_serial = self._patch_serial(
            lambda port, baud, timeout: fake_serial
        )
        original_parse = garmin_gnx375.pynmea2.parse
        garmin_gnx375.pynmea2.parse = MagicMock(return_value=parsed)
        try:
            self.thread.run()
        finally:
            garmin_gnx375.serial.Serial = original_serial
            garmin_gnx375.pynmea2.parse = original_parse

        self.thread._dispatch.assert_called_once_with(parsed, 0.3)

    def test_run_skips_decode_and_parse_errors_and_logs_dispatch_errors(self):
        class BadDecode:
            def decode(self, *args, **kwargs):
                raise UnicodeError("bad bytes")

        class FakeSerial:
            is_open = False

            def __init__(self, thread):
                self.thread = thread
                self.items = [
                    BadDecode(),
                    b"$BAD\n",
                    b"$GOOD\n",
                ]

            def readline(self):
                item = self.items.pop(0)
                if not self.items:
                    self.thread.getout = True
                return item

        def parse(line):
            if line == "$BAD":
                raise pynmea2.ParseError("bad nmea", line)
            return MagicMock()

        fake_serial = FakeSerial(self.thread)
        self.thread._dispatch = MagicMock(side_effect=RuntimeError("boom"))
        original_serial = self._patch_serial(lambda *args, **kwargs: fake_serial)
        original_parse = garmin_gnx375.pynmea2.parse
        garmin_gnx375.pynmea2.parse = parse
        try:
            self.thread.run()
        finally:
            garmin_gnx375.serial.Serial = original_serial
            garmin_gnx375.pynmea2.parse = original_parse

        self.thread._dispatch.assert_called_once()
        self.parent.log.debug.assert_called_once()
        self.assertIn("dispatch error", self.parent.log.debug.call_args.args[0])

    def test_run_logs_read_error_and_closes_serial(self):
        class FakeSerial:
            is_open = True

            def __init__(self):
                self.closed = False

            def readline(self):
                raise garmin_gnx375.serial.SerialException("read failed")

            def close(self):
                self.closed = True
                self.is_open = False

        fake_serial = FakeSerial()
        original = self._patch_serial(lambda *args, **kwargs: fake_serial)
        try:
            self.thread.run()
        finally:
            garmin_gnx375.serial.Serial = original

        self.parent.log.error.assert_called_once()
        self.assertIn("serial read error", self.parent.log.error.call_args.args[0])
        self.assertTrue(fake_serial.closed)

    def test_stop_sets_getout(self):
        self.assertFalse(self.thread.getout)

        self.thread.stop()

        self.assertTrue(self.thread.getout)


class TestPluginLifecycle(unittest.TestCase):
    def test_plugin_run_stop_and_status(self):
        pl = Plugin("garmin-test", {"port": "/dev/null"}, {})
        thread = MagicMock()
        thread.is_alive.return_value = False
        pl.thread = thread

        pl.run()
        thread.start.assert_called_once_with()

        pl.stop()
        thread.stop.assert_called_once_with()
        thread.join.assert_not_called()
        self.assertIs(pl.get_status(), pl.status)

    def test_plugin_stop_joins_live_thread(self):
        pl = Plugin("garmin-test", {"port": "/dev/null"}, {})
        thread = MagicMock()
        thread.is_alive.side_effect = [True, False]
        pl.thread = thread

        pl.stop()

        thread.join.assert_called_once_with(2.0)

    def test_plugin_stop_raises_when_thread_survives_join(self):
        pl = Plugin("garmin-test", {"port": "/dev/null"}, {})
        thread = MagicMock()
        thread.is_alive.return_value = True
        pl.thread = thread

        with self.assertRaises(garmin_gnx375.plugin.PluginFail):
            pl.stop()

        thread.join.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
