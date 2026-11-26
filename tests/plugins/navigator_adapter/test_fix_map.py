"""Tests for the navigator_adapter fix_map module.

Verifies that apply_to_db() writes the correct FIX keys for each
NormalizedNavMessage field, handles None fields correctly (no write),
and applies CDI scaling and sign convention.
"""

import io
import unittest
from unittest.mock import MagicMock

import fixgw.database as database
from fixgw.plugins.navigator_adapter.base import NormalizedNavMessage, Position, Waypoint
from fixgw.plugins.navigator_adapter.fix_map import apply_to_db

DB_CONFIG = """
variables:
  e: 1

entries:
- {key: LAT,               type: float, min: -90.0,   max: 90.0,    units: deg,    initial: 0.0, tol: 2000}
- {key: LONG,              type: float, min: -180.0,  max: 180.0,   units: deg,    initial: 0.0, tol: 2000}
- {key: GS,                type: float, min: 0.0,     max: 9999.0,  units: knots,  initial: 0.0, tol: 2000}
- {key: TRACK,             type: float, min: 0.0,     max: 360.0,   units: deg,    initial: 0.0, tol: 2000}
- {key: TALT, type: float, min: -1500.0, max: 60000.0, units: ft,     initial: 0.0, tol: 2000}
- {key: GPS_FIX_TYPE,      type: int,   min: 0,       max: 9,                      initial: 0,   tol: 2000}
- {key: XTRACK,            type: float, min: -100.0,  max: 100.0,   units: nM,     initial: 0.0, tol: 2000}
- {key: CDI,               type: float, min: -1.0,    max: 1.0,                    initial: 0.0, tol: 2000}
- {key: COURSE,            type: float, min: 0.0,     max: 360.0,   units: deg,    initial: 0.0, tol: 2000}
- {key: EXTCRS,            type: float, min: 0.0,     max: 359.9,   units: deg,    initial: 0.0, tol: 5000}
- {key: EXTCDI,            type: float, min: -1.0,    max: 1.0,                    initial: 0.0, tol: 5000}
- {key: EXTTF,             type: int,   min: 0,       max: 2,                      initial: 0,   tol: 5000}
- {key: EXTXTK,            type: float, min: -100.0,  max: 100.0,   units: nM,     initial: 0.0, tol: 5000}
"""


def _val(key):
    return database.read(key)[0]


def _write(key, value):
    database.write(key, value)


class TestFixMapPosition(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))

    def test_position_fields_written(self):
        msg = NormalizedNavMessage(
            position=Position(lat_deg=32.7, lon_deg=-97.3, alt_ft_msl=1500.0)
        )
        apply_to_db(msg, _write)
        self.assertAlmostEqual(_val("LAT"), 32.7, places=4)
        self.assertAlmostEqual(_val("LONG"), -97.3, places=4)
        self.assertAlmostEqual(_val("TALT"), 1500.0, places=2)

    def test_talt_is_bad_below_a_3d_fix_when_the_navigator_says(self):
        def set_value(key, value, bad):
            database.get_raw_item(key).set(value, bad=bad)

        for fix, bad in ((2, True), (3, False), (None, False)):
            msg = NormalizedNavMessage(
                position=Position(lat_deg=32.7, lon_deg=-97.3, alt_ft_msl=1500.0),
                gps_fix_type=fix,
            )
            apply_to_db(msg, _write, set_value=set_value)
            self.assertAlmostEqual(_val("TALT"), 1500.0, places=2)
            self.assertIs(database.get_raw_item("TALT").bad, bad, fix)

    def test_none_position_fields_not_written(self):
        database.write("LAT", 32.0)  # valid value within DB range
        msg = NormalizedNavMessage(position=Position())  # all None
        apply_to_db(msg, _write)
        self.assertAlmostEqual(_val("LAT"), 32.0, places=4)  # unchanged


class TestFixMapKinematics(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))

    def test_ground_speed_and_track_written(self):
        msg = NormalizedNavMessage(ground_speed_kt=120.0, ground_track_deg_true=045.0)
        apply_to_db(msg, _write)
        self.assertAlmostEqual(_val("GS"), 120.0, places=2)
        self.assertAlmostEqual(_val("TRACK"), 45.0, places=2)

    def test_desired_track_and_to_from_go_to_external_inputs(self):
        msg = NormalizedNavMessage(desired_track_deg_mag=270.0, to_from=2)
        apply_to_db(msg, _write)
        self.assertAlmostEqual(_val("EXTCRS"), 270.0, places=2)
        self.assertEqual(_val("EXTTF"), 2)


class TestFixMapXteAndCdi(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))

    def test_positive_xtrack_writes_negative_cdi(self):
        # Aircraft RIGHT of track → positive XTE → CDI needle deflects left (negative)
        msg = NormalizedNavMessage(cross_track_error_nm_signed=2.5)
        apply_to_db(msg, _write, cdi_full_scale_nm=5.0)
        self.assertAlmostEqual(_val("EXTXTK"), 2.5, places=4)
        self.assertAlmostEqual(_val("EXTCDI"), -0.5, places=4)

    def test_negative_xtrack_writes_positive_cdi(self):
        msg = NormalizedNavMessage(cross_track_error_nm_signed=-2.5)
        apply_to_db(msg, _write, cdi_full_scale_nm=5.0)
        self.assertAlmostEqual(_val("EXTCDI"), 0.5, places=4)

    def test_cdi_clips_to_plus_one(self):
        msg = NormalizedNavMessage(cross_track_error_nm_signed=-50.0)
        apply_to_db(msg, _write, cdi_full_scale_nm=5.0)
        self.assertAlmostEqual(_val("EXTCDI"), 1.0, places=4)

    def test_cdi_clips_to_minus_one(self):
        msg = NormalizedNavMessage(cross_track_error_nm_signed=50.0)
        apply_to_db(msg, _write, cdi_full_scale_nm=5.0)
        self.assertAlmostEqual(_val("EXTCDI"), -1.0, places=4)

    def test_approach_scale_0_3nm(self):
        msg = NormalizedNavMessage(cross_track_error_nm_signed=0.15)
        apply_to_db(msg, _write, cdi_full_scale_nm=0.3)
        self.assertAlmostEqual(_val("EXTCDI"), -0.5, places=4)

    def test_zero_xte_gives_zero_cdi(self):
        msg = NormalizedNavMessage(cross_track_error_nm_signed=0.0)
        apply_to_db(msg, _write, cdi_full_scale_nm=5.0)
        self.assertAlmostEqual(_val("EXTCDI"), 0.0, places=4)

    def test_none_xte_does_not_write(self):
        database.write("EXTCDI", 0.7)
        msg = NormalizedNavMessage()  # cross_track_error_nm_signed is None
        apply_to_db(msg, _write)
        self.assertAlmostEqual(_val("EXTCDI"), 0.7, places=4)  # unchanged


class TestFixMapComputeOwnedKeys(unittest.TestCase):
    """AER-4473: compute owns COURSE/CDI/XTRACK; the navigator feeds only EXT*."""

    def test_full_message_never_writes_compute_owned_keys(self):
        written = []
        msg = NormalizedNavMessage(
            position=Position(lat_deg=32.7, lon_deg=-97.3),
            ground_speed_kt=120.0,
            ground_track_deg_true=45.0,
            desired_track_deg_mag=87.0,
            to_from=1,
            cross_track_error_nm_signed=1.0,
            gps_fix_type=3,
        )
        apply_to_db(msg, lambda key, value: written.append(key))
        self.assertTrue({"EXTCRS", "EXTCDI", "EXTTF", "EXTXTK"} <= set(written))
        self.assertFalse({"COURSE", "CDI", "XTRACK"} & set(written))


class TestFixMapIntegrity(unittest.TestCase):
    """SRC6 (AER-6674): a degraded or invalid navigator writes its position,
    kinematics and EXT* guidance bad, and a valid one clears the flag. Before
    SRC6 the field was declared and never read, so a degraded navigator read
    as healthy."""

    COVERED = ("LAT", "LONG", "TALT", "GS", "TRACK", "EXTCRS", "EXTTF", "EXTXTK", "EXTCDI")

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))

    @staticmethod
    def _set_value(key, value, bad):
        database.get_raw_item(key).set(value, bad=bad)

    def _apply(self, integrity):
        msg = NormalizedNavMessage(
            position=Position(lat_deg=32.7, lon_deg=-97.3, alt_ft_msl=1500.0),
            ground_speed_kt=120.0,
            ground_track_deg_true=45.0,
            desired_track_deg_mag=87.0,
            to_from=1,
            cross_track_error_nm_signed=1.0,
            gps_fix_type=3,
            integrity=integrity,
        )
        apply_to_db(msg, _write, set_value=self._set_value)
        return {k for k in self.COVERED if database.get_raw_item(k).bad}

    def test_degraded_sets_bad_on_every_covered_key(self):
        self.assertEqual(self._apply("degraded"), set(self.COVERED))
        # The value still goes out with the flag: a reader decides what bad means.
        self.assertAlmostEqual(_val("LAT"), 32.7, places=4)

    def test_invalid_and_an_unknown_word_set_bad_too(self):
        for integrity in ("invalid", "Valid", ""):
            database.init(io.StringIO(DB_CONFIG))
            self.assertEqual(self._apply(integrity), set(self.COVERED), integrity)

    def test_valid_clears_the_flag_a_degraded_message_set(self):
        self._apply("degraded")
        self.assertEqual(self._apply("valid"), set())

    def test_fix_type_carries_no_flag_from_it(self):
        # GPS_FIX_TYPE is itself a quality key; its value says what the fix is.
        msg = NormalizedNavMessage(gps_fix_type=3, integrity="degraded")
        apply_to_db(msg, _write, set_value=self._set_value)
        self.assertFalse(database.get_raw_item("GPS_FIX_TYPE").bad)

    def test_the_plugin_passes_the_flag_through(self):
        # A stub adapter that reports a degraded fix, through the plugin's own
        # line handler, so the plugin's set_value is the one exercised.
        from fixgw.plugins.navigator_adapter import MainThread

        class Degraded:
            def parse_line(self, raw):
                return [NormalizedNavMessage(position=Position(lat_deg=32.7, lon_deg=-97.3),
                                             integrity="degraded")]

        parent = MagicMock()
        parent.db_write.side_effect = _write
        parent.db_get_item.side_effect = database.get_raw_item
        MainThread(parent, Degraded(), 5.0).handle_line(b"x")
        self.assertTrue(database.get_raw_item("LAT").bad)
        self.assertTrue(database.get_raw_item("LONG").bad)


class TestFixMapGpsFix(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))

    def test_fix_type_written(self):
        msg = NormalizedNavMessage(gps_fix_type=2)
        apply_to_db(msg, _write)
        self.assertEqual(_val("GPS_FIX_TYPE"), 2)

    def test_none_fix_type_not_written(self):
        database.write("GPS_FIX_TYPE", 1)
        msg = NormalizedNavMessage()
        apply_to_db(msg, _write)
        self.assertEqual(_val("GPS_FIX_TYPE"), 1)  # unchanged


if __name__ == "__main__":
    unittest.main()
