#  Copyright (c) 2026 Bill Mallard
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.

"""Unit tests for the garmin_gnx375 FIX-Gateway plugin.

The plugin is a config alias for navigator_adapter with the garmin_nmea
adapter (SRC6, AER-6674), so these tests drive the adapter path: real,
checksummed sentences as bytes into the plugin's read-loop handler, then the
adapter's parser and fix_map, then the real FIX database. No sentence is a
mock. They are the tests the stand-alone GNX parser had, kept so the fold is
shown to change nothing the GNX writes.
"""

import io
import unittest
from unittest.mock import MagicMock

import serial

import fixgw.database as database
import fixgw.plugin as plugin
import fixgw.plugins.navigator_adapter as navigator_adapter
from fixgw.plugins.garmin_gnx375 import Plugin
from fixgw.plugins.navigator_adapter.adapters.garmin_nmea import GarminNmeaAdapter

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

- key: TALT
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

- key: EXTXTK
  description: External navigator cross-track error
  type: float
  min: -100.0
  max: 100.0
  units: nM
  initial: 0.0
  tol: 5000
"""

_CDI_FULL_SCALE = 5.0  # nm, default for these tests

# Every key the database above declares, for "what did the GNX write".
_ALL_KEYS = ("LAT", "LONG", "GS", "TRACK", "TALT", "GPS_FIX_TYPE", "XTRACK", "CDI",
             "COURSE", "EXTCRS", "EXTCDI", "EXTTF", "EXTXTK")


def _make_plugin(**config):
    """The GNX plugin as a connections file loads it; its writes reach the real DB."""
    cfg = {"port": "/dev/null", "cdi_full_scale_nm": str(_CDI_FULL_SCALE)}
    cfg.update(config)
    return Plugin("garmin-test", cfg, {})


def _nmea(body):
    """A checksummed NMEA sentence from its body (between '$' and '*')."""
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


def _feed(pl, *sentences):
    for s in sentences:
        pl._thread.handle_line((s + "\r\n").encode("ascii"))


def _db_value(key):
    """Return the raw scalar value for a database key."""
    return database.read(key)[0]


def _written():
    return {k for k in _ALL_KEYS if database.get_raw_item(k).written}


RMC_ACTIVE = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
RMC_VOID = _nmea("GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W")
RMC_NO_SPEED_TRACK = _nmea("GPRMC,123519,A,1230.000,N,04515.000,W,,,230394,003.1,W")
GGA_GPS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"
GGA_WAAS_8 = "$GPGGA,123519,4807.038,N,01131.000,E,2,08,0.9,545.4,M,46.9,M,,*44"
GGA_GPS_3 = "$GPGGA,123519,4807.038,N,01131.000,E,1,03,2.9,545.4,M,46.9,M,,*4E"
GGA_NO_FIX = _nmea("GPGGA,123519,,,,,0,00,,,M,,M,,")
GGA_NO_ALT = _nmea("GPGGA,123519,1000.000,N,02000.000,E,1,08,0.9,,M,46.9,M,,")
GSA_2D = "$GPGSA,A,2,04,05,09,,,,,,,,,,3.1,2.9,1.0*33"
GNGSA_3D = "$GNGSA,A,3,04,05,,09,12,,,24,,,,,2.5,1.3,2.1*27"


class TestRmcParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def test_active_rmc_writes_lat_lon_gs_track(self):
        _feed(self.pl, RMC_ACTIVE)
        self.assertAlmostEqual(_db_value("LAT"), 48.1173, places=3)
        self.assertAlmostEqual(_db_value("LONG"), 11.5167, places=3)
        self.assertAlmostEqual(_db_value("GS"), 22.4, places=1)
        self.assertAlmostEqual(_db_value("TRACK"), 84.4, places=1)

    def test_void_rmc_does_not_write(self):
        _feed(self.pl, RMC_VOID)
        self.assertEqual(_written(), set())

    def test_rmc_skips_missing_speed_and_track(self):
        _feed(self.pl, RMC_NO_SPEED_TRACK)
        self.assertAlmostEqual(_db_value("LAT"), 12.5)
        self.assertAlmostEqual(_db_value("LONG"), -45.25)
        self.assertEqual(_written(), {"LAT", "LONG"})


class TestGgaParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def test_gga_fix_writes_position_and_altitude(self):
        # Altitude 545.4 m -> 1789.4 ft
        _feed(self.pl, GGA_GPS_8)
        self.assertAlmostEqual(_db_value("LAT"), 48.1173, places=3)
        self.assertAlmostEqual(_db_value("LONG"), 11.5167, places=3)
        self.assertAlmostEqual(_db_value("TALT"), 545.4 * 3.28084, places=0)
        # GGA quality 1 with 8 satellites is a 3D fix on the GPS_FIX_TYPE scale.
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 3)

    def test_gga_no_fix_skips_position(self):
        _feed(self.pl, GGA_NO_FIX)
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 0)
        self.assertEqual(_written(), {"GPS_FIX_TYPE"})

    def test_waas_fix_is_3d_dgps(self):
        _feed(self.pl, GGA_WAAS_8)
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 4)

    def test_gga_fix_without_altitude_skips_altitude(self):
        _feed(self.pl, GGA_NO_ALT)
        self.assertEqual(_db_value("GPS_FIX_TYPE"), 3)
        self.assertNotIn("TALT", _written())


class TestFixTypeFromSentenceStream(unittest.TestCase):
    """GPS_FIX_TYPE from real, checksummed sentences (AER-4224)."""

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def _fix(self, *sentences):
        _feed(self.pl, *sentences)
        return _db_value("GPS_FIX_TYPE")

    def test_gga_only_stream_passes_the_3d_gate(self):
        # The flight-plan engine requires GPS_FIX_TYPE >= 3.
        self.assertEqual(self._fix(GGA_GPS_8), 3)
        self.assertEqual(self._fix(GGA_WAAS_8), 4)

    def test_three_satellites_is_2d(self):
        self.assertEqual(self._fix(GGA_GPS_3), 2)

    def test_talt_is_bad_below_a_3d_fix(self):
        # GGA field 9 is metres above MSL, so TALT in feet; held or invented
        # below a 3-D fix, so bad there (TALTSRC1, Elon's review on AER-6458).
        self._fix(GGA_GPS_3)
        talt = database.get_raw_item("TALT")
        self.assertAlmostEqual(talt.value[0], 545.4 * 3.28084, places=0)
        self.assertTrue(talt.bad)
        self._fix(GGA_GPS_8)
        self.assertFalse(database.get_raw_item("TALT").bad)

    def test_gsa_mode_overrides_satellite_count_for_one_epoch(self):
        self.assertEqual(self._fix(GSA_2D, GGA_GPS_8), 2)
        # No GSA before this GGA: back to the satellite count.
        self.assertEqual(self._fix(GGA_GPS_8), 3)

    def test_multi_constellation_gsa_talker_is_used(self):
        self.assertEqual(self._fix(GNGSA_3D, GGA_GPS_3), 3)


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


class TestRmbParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def test_steer_left_is_right_of_track_and_deflects_needle_left(self):
        # 'L' (steer left) -> aircraft RIGHT of track -> +3 nm -> CDI -3/5
        _feed(self.pl, RMB_RIGHT_OF_TRACK)
        self.assertAlmostEqual(_db_value("EXTXTK"), 3.0, places=4)
        self.assertAlmostEqual(_db_value("EXTCDI"), -3.0 / _CDI_FULL_SCALE, places=4)

    def test_steer_right_is_left_of_track_and_deflects_needle_right(self):
        _feed(self.pl, RMB_LEFT_OF_TRACK)
        self.assertAlmostEqual(_db_value("EXTXTK"), -3.0, places=4)
        self.assertAlmostEqual(_db_value("EXTCDI"), 3.0 / _CDI_FULL_SCALE, places=4)

    def test_large_xte_clips_cdi_to_full_scale(self):
        _feed(self.pl, RMB_FAR_RIGHT)
        self.assertAlmostEqual(_db_value("EXTXTK"), 20.0, places=4)  # not clamped
        self.assertAlmostEqual(_db_value("EXTCDI"), -1.0, places=4)

    def test_void_missing_or_bad_rmb_does_not_write(self):
        for sentence in (RMB_VOID, RMB_NO_XTE, RMB_BAD_STEER):
            database.init(io.StringIO(DB_CONFIG))
            _feed(_make_plugin(), sentence)
            self.assertEqual(_written(), set(), sentence)

    def test_configured_scale_is_used(self):
        _feed(_make_plugin(cdi_full_scale_nm="6.0"), RMB_RIGHT_OF_TRACK)
        self.assertAlmostEqual(_db_value("EXTCDI"), -0.5, places=4)


class TestApbParsing(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def test_course_is_leg_desired_track_not_bearing_to_destination(self):
        _feed(self.pl, APB_TO_MAG)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)
        self.assertEqual(_db_value("EXTTF"), 1)

    def test_perpendicular_passed_is_from(self):
        _feed(self.pl, APB_FROM_MAG)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)
        self.assertEqual(_db_value("EXTTF"), 2)

    def test_true_desired_track_is_made_magnetic_with_rmc_variation(self):
        # 090 T with 3.1 W variation is 093.1 M.
        _feed(self.pl, RMC_VAR_3W, APB_TO_TRUE)
        self.assertAlmostEqual(_db_value("EXTCRS"), 93.1, places=3)

    def test_true_desired_track_without_variation_is_not_written(self):
        _feed(self.pl, APB_TO_TRUE)
        self.assertEqual(_db_value("EXTCRS"), 0.0)
        self.assertEqual(_db_value("EXTTF"), 1)

    def test_invalid_apb_sets_off_and_leaves_course(self):
        _feed(self.pl, APB_TO_MAG, APB_INVALID)
        self.assertEqual(_db_value("EXTTF"), 0)
        self.assertAlmostEqual(_db_value("EXTCRS"), 87.0, places=3)


class TestExternalNavigatorKeysOnly(unittest.TestCase):
    """AER-4473: compute owns COURSE/CDI/XTRACK; the GNX feeds only EXT*."""

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.pl = _make_plugin()

    def test_full_sentence_stream_never_writes_compute_owned_keys(self):
        _feed(self.pl, RMC_VAR_3W, GGA_GPS_8, RMB_RIGHT_OF_TRACK,
              APB_TO_MAG, APB_FROM_MAG, APB_TO_TRUE)
        written = _written()
        self.assertTrue({"EXTXTK", "EXTCDI", "EXTCRS", "EXTTF"} <= written)
        self.assertFalse({"COURSE", "CDI", "XTRACK"} & written)

    def test_unknown_and_garbled_lines_write_nothing(self):
        _feed(self.pl, _nmea("GPTXT,01,01,02,ANTENNA OK"), "$BAD", "not nmea")
        self.assertEqual(_written(), set())


class TestTheAlias(unittest.TestCase):
    """garmin_gnx375 is navigator_adapter with garmin_nmea, for existing installs
    (SRC6; Bill kept the alias on AER-6409)."""

    def test_it_is_the_navigator_adapter_plugin_with_the_garmin_parser(self):
        pl = _make_plugin()
        self.assertIsInstance(pl, navigator_adapter.Plugin)
        self.assertIsInstance(pl._adapter, GarminNmeaAdapter)
        self.assertEqual(pl.get_status()["Adapter"], "garmin_nmea")

    def test_another_adapter_in_its_config_is_not_used(self):
        pl = _make_plugin(adapter="nonexistent_vendor")
        self.assertIsInstance(pl._adapter, GarminNmeaAdapter)

    def test_the_callers_config_is_not_changed(self):
        cfg = {"port": "/dev/null"}
        Plugin("garmin-test", cfg, {})
        self.assertEqual(cfg, {"port": "/dev/null"})


class TestTwoNavigatorsEnabled(unittest.TestCase):
    def test_a_second_running_navigator_is_warned_about(self):
        # Both run navigator_adapter's code, so the database's second-writer
        # warning cannot tell them apart; the plugin says so instead.
        gnx = _make_plugin()
        nav = navigator_adapter.Plugin("navigator", {"port": "/dev/null", "adapter": "garmin_nmea"}, {})
        for pl in (gnx, nav):
            pl._thread = MagicMock()
            pl._thread.is_alive.return_value = False
            pl.log = MagicMock()
        try:
            gnx.run()
            gnx.log.warning.assert_not_called()
            nav.run()
            nav.log.warning.assert_called_once()
            self.assertIn("garmin-test", nav.log.warning.call_args.args[0])
        finally:
            gnx.stop()
            nav.stop()
        self.assertEqual(navigator_adapter.Plugin._running, set())


class _OneLineSerial:
    """Serial that returns its lines, then stops the thread on the last one."""

    def __init__(self, thread, lines):
        self.thread = thread
        self.lines = list(lines)
        self.is_open = True
        self.closed = False
        self.opened_with = None

    def readline(self):
        line = self.lines.pop(0)
        if not self.lines:
            self.thread.getout = True
        if isinstance(line, Exception):
            raise line
        return line

    def close(self):
        self.closed = True
        self.is_open = False


class TestSerialRunLoop(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self._original = serial.Serial

    def tearDown(self):
        serial.Serial = self._original

    def _run(self, pl, lines):
        fake = _OneLineSerial(pl._thread, lines)

        def factory(port, baud, timeout):
            fake.opened_with = (port, baud)
            return fake

        serial.Serial = factory
        pl._thread.run()
        return fake

    def test_run_logs_open_failure(self):
        def factory(*args, **kwargs):
            raise serial.SerialException("no port")

        serial.Serial = factory
        pl = _make_plugin()
        pl.log = pl._thread.log = MagicMock()
        pl._thread.run()
        pl._thread.log.error.assert_called_once()
        self.assertIn("cannot open /dev/null", pl._thread.log.error.call_args.args[0])

    def test_default_baud_is_aviation_output_1s_9600(self):
        fake = self._run(_make_plugin(), [b""])
        self.assertEqual(fake.opened_with, ("/dev/null", 9600))

    def test_configured_baud_is_used(self):
        fake = self._run(_make_plugin(port="/dev/ttyS1", baud="4800"), [b""])
        self.assertEqual(fake.opened_with, ("/dev/ttyS1", 4800))

    def test_run_reads_parses_writes_and_closes_serial(self):
        fake = self._run(_make_plugin(), [b"", (RMB_RIGHT_OF_TRACK + "\r\n").encode()])
        self.assertAlmostEqual(_db_value("EXTXTK"), 3.0, places=4)
        self.assertTrue(fake.closed)

    def test_run_survives_undecodable_and_garbled_lines(self):
        fake = self._run(_make_plugin(), [b"\xff\xfe\r\n", b"$BAD\r\n",
                                          (RMB_LEFT_OF_TRACK + "\r\n").encode()])
        self.assertAlmostEqual(_db_value("EXTXTK"), -3.0, places=4)
        self.assertTrue(fake.closed)

    def test_run_logs_read_error_and_closes_serial(self):
        pl = _make_plugin()
        pl._thread.log = MagicMock()
        fake = self._run(pl, [serial.SerialException("read failed")])
        pl._thread.log.error.assert_called_once()
        self.assertIn("serial read error", pl._thread.log.error.call_args.args[0])
        self.assertTrue(fake.closed)

    def test_stop_sets_getout(self):
        pl = _make_plugin()
        self.assertFalse(pl._thread.getout)
        pl._thread.stop()
        self.assertTrue(pl._thread.getout)


class TestPluginLifecycle(unittest.TestCase):
    def _plugin_with(self, thread):
        pl = _make_plugin()
        pl._thread = thread
        return pl

    def test_plugin_run_stop(self):
        thread = MagicMock()
        thread.is_alive.return_value = False
        pl = self._plugin_with(thread)

        pl.run()
        thread.start.assert_called_once_with()

        pl.stop()
        thread.stop.assert_called_once_with()
        thread.join.assert_not_called()

    def test_plugin_stop_joins_live_thread(self):
        thread = MagicMock()
        thread.is_alive.side_effect = [True, False]
        pl = self._plugin_with(thread)

        pl.stop()

        thread.join.assert_called_once_with(2.0)

    def test_plugin_stop_raises_when_thread_survives_join(self):
        thread = MagicMock()
        thread.is_alive.return_value = True
        pl = self._plugin_with(thread)

        with self.assertRaises(plugin.PluginFail):
            pl.stop()

        thread.join.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
