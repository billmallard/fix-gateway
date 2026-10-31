"""
Unit tests for the annunciate plugin.

Tests AnnunciateItem callback logic by writing directly to the database and
observing the item's annunciate flag — no serial ports or network sockets
are involved.

Coverage:
  - High and low threshold detection
  - Deadband hysteresis (prevents flag oscillation at the setpoint boundary)
  - Conditional bypass: flag clears when a bypass condition is met
    (also verifies the annunicate→annunciate typo fix on the bypass path)
  - Conditional bypass on a list of conditions: bypass only while all hold
  - A bypass input that is old, bad or failed never bypasses (AER-4370)
  - Start bypass: no annunciation until value first rises above low setpoint
  - start_bypass: no (YAML False) does not arm the start latch
  - stop(): callbacks removed, flag no longer updated after shutdown
  - get_status(): item count reported correctly
"""
import io
import time
import unittest
from types import SimpleNamespace

import fixgw.database as database
from fixgw.plugins import annunciate

DB_CONFIG = """
variables:
  e: 1

entries:
- key: OILP1
  description: Oil Pressure Engine 1
  type: float
  min: 0.0
  max: 120.0
  units: psi
  initial: 60.0
  tol: 2000
  aux: [lowAlarm, highAlarm]

- key: TACH1
  description: Tachometer Engine 1
  type: float
  min: 0.0
  max: 3600.0
  units: rpm
  initial: 0.0
  tol: 2000

- key: IAS
  description: Indicated Airspeed
  type: float
  min: 0.0
  max: 1000.0
  units: knots
  initial: 0.0
  tol: 2000
"""

_DEFAULTS = {
    "low_aux_point": "lowAlarm",
    "high_aux_point": "highAlarm",
    "deadband": 1.0,
    "start_bypass": None,
    "cond_bypass": "None",
}


def _make_plugin(items, defaults=None):
    config = {"defaults": defaults or _DEFAULTS, "items": items}
    p = annunciate.Plugin("test_annunciate", config, None)
    p.run()
    return p


class TestAnnunciatePlugin(unittest.TestCase):

    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        database.write("OILP1.lowAlarm", 10.0)
        database.write("OILP1.highAlarm", 100.0)

    # ------------------------------------------------------------------
    # Basic threshold detection
    # ------------------------------------------------------------------

    def test_no_annunciation_when_in_range(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 60.0)
        self.assertFalse(p.items[0].item.annunciate)

    def test_high_annunciation_above_threshold(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 105.0)
        self.assertTrue(p.items[0].item.annunciate)

    def test_low_annunciation_below_threshold(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 5.0)
        self.assertTrue(p.items[0].item.annunciate)

    def test_high_flag_clears_when_back_in_range(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 105.0)
        self.assertTrue(p.items[0].item.annunciate)
        database.write("OILP1", 60.0)
        self.assertFalse(p.items[0].item.annunciate)

    def test_missing_aux_points_disable_that_side(self):
        p = _make_plugin(
            [
                {
                    "key": "OILP1",
                    "low_aux_point": "missingLow",
                    "high_aux_point": "missingHigh",
                }
            ]
        )

        self.assertIsNone(p.items[0].low_set_point)
        self.assertIsNone(p.items[0].high_set_point)
        database.write("OILP1", 105.0)
        self.assertFalse(p.items[0].item.annunciate)

    # ------------------------------------------------------------------
    # Deadband hysteresis
    # ------------------------------------------------------------------

    def test_percent_deadband_uses_item_range(self):
        p = _make_plugin([{"key": "OILP1", "deadband": "10%"}])

        self.assertEqual(p.items[0].deadband, 12.0)

    def test_deadband_prevents_rapid_clear_on_high(self):
        # highAlarm=100.0, deadband=1.0 → clears only when value <= 99.0
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 105.0)               # trigger
        self.assertTrue(p.items[0].item.annunciate)
        database.write("OILP1", 99.5)                # below threshold but within deadband
        self.assertTrue(p.items[0].item.annunciate)  # still latched
        database.write("OILP1", 98.9)                # past deadband → clear
        self.assertFalse(p.items[0].item.annunciate)

    def test_deadband_prevents_rapid_clear_on_low(self):
        # lowAlarm=10.0, deadband=1.0 → clears only when value >= 11.0
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 5.0)                 # trigger
        self.assertTrue(p.items[0].item.annunciate)
        database.write("OILP1", 10.5)                # above threshold but within deadband
        self.assertTrue(p.items[0].item.annunciate)  # still latched
        database.write("OILP1", 11.1)                # past deadband → clear
        self.assertFalse(p.items[0].item.annunciate)

    # ------------------------------------------------------------------
    # Conditional bypass
    # ------------------------------------------------------------------

    def test_conditional_bypass_suppresses_annunciation(self):
        """Bypass condition clears the annunciate flag.

        Also directly validates the annunicate→annunciate typo fix: before
        the fix the bypass path set self.item.annunicate (wrong attribute)
        instead of self.item.annunciate, so the flag was never cleared.
        """
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)

        # Bypass inactive: TACH1 >= 500 → annunciation fires normally
        database.write("TACH1", 1000.0)
        database.write("OILP1", 105.0)
        self.assertTrue(p.items[0].item.annunciate)

        # Activate bypass: TACH1 < 500 → flag must clear
        database.write("TACH1", 0.0)
        database.write("OILP1", 105.0)  # re-trigger evaluate
        self.assertFalse(p.items[0].item.annunciate)

    def test_annunciation_resumes_when_bypass_deactivates(self):
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)

        database.write("TACH1", 0.0)    # bypass active
        database.write("OILP1", 105.0)
        self.assertFalse(p.items[0].item.annunciate)

        database.write("TACH1", 1000.0)  # bypass inactive
        database.write("OILP1", 105.0)
        self.assertTrue(p.items[0].item.annunciate)

    # ------------------------------------------------------------------
    # Bypass input quality (AER-4370): a failed tach must not silence
    # the engine alarms on a running engine
    # ------------------------------------------------------------------

    def _low_oil_with_tach(self, p, **flags):
        database.write("TACH1", 0.0)
        tach = database.get_raw_item("TACH1")
        for name, value in flags.items():
            setattr(tach, name, value)
        database.write("OILP1", 5.0)
        return p.items[0].item.annunciate

    def test_failed_bypass_input_does_not_bypass(self):
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)
        self.assertTrue(self._low_oil_with_tach(p, fail=True))

    def test_bad_bypass_input_does_not_bypass(self):
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)
        self.assertTrue(self._low_oil_with_tach(p, bad=True))

    def test_old_bypass_input_does_not_bypass(self):
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)
        database.write("TACH1", 0.0)
        database.get_raw_item("TACH1").timestamp -= 3.0  # past its 2000 ms tol
        database.write("OILP1", 5.0)
        self.assertTrue(p.items[0].item.annunciate)

    def test_trusted_bypass_input_still_bypasses(self):
        defaults = dict(_DEFAULTS, cond_bypass="TACH1 < 500")
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)
        self.assertFalse(self._low_oil_with_tach(p))

    def test_condition_list_bypasses_only_when_all_hold(self):
        defaults = dict(_DEFAULTS, cond_bypass=["TACH1 < 500", "IAS < 40"])
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)

        # On the ground, engine stopped: both hold, bypassed
        database.write("IAS", 0.0)
        self.assertFalse(self._low_oil_with_tach(p))

        # Airborne, tach reads 0: IAS does not hold, alarm armed
        database.write("IAS", 120.0)
        self.assertTrue(self._low_oil_with_tach(p))

        # Engine running on the ground: TACH1 does not hold, alarm armed
        database.write("IAS", 0.0)
        database.write("TACH1", 1000.0)
        database.write("OILP1", 5.0)
        self.assertTrue(p.items[0].item.annunciate)

    def test_condition_list_with_failed_member_does_not_bypass(self):
        defaults = dict(_DEFAULTS, cond_bypass=["TACH1 < 500", "IAS < 40"])
        p = _make_plugin([{"key": "OILP1"}], defaults=defaults)
        database.write("IAS", 0.0)
        database.get_raw_item("IAS").fail = True
        self.assertTrue(self._low_oil_with_tach(p))

    def test_condition_list_validation_errors(self):
        with self.assertRaisesRegex(ValueError, "Wrong number of tokens"):
            _make_plugin([{"key": "OILP1", "cond_bypass": ["TACH1 < 500", "IAS <"]}])

    def test_conditional_bypass_validation_errors(self):
        with self.assertRaisesRegex(ValueError, "Wrong number of tokens"):
            _make_plugin([{"key": "OILP1", "cond_bypass": "TACH1 <"}])

        with self.assertRaisesRegex(ValueError, "Unknown operator"):
            _make_plugin([{"key": "OILP1", "cond_bypass": "TACH1 <> 500"}])

    def test_conditional_bypass_unknown_key_when_lookup_returns_none(self):
        item = SimpleNamespace(
            aux=[],
            min=0.0,
            max=100.0,
            dtype=float,
            value=(0.0, False, False, False, False, False),
        )
        fake_plugin = SimpleNamespace(
            db_get_item=lambda key: item if key == "OILP1" else None,
            db_callback_add=lambda *_args: None,
        )

        with self.assertRaisesRegex(ValueError, "Unknown Key MISSING"):
            annunciate.AnnunciateItem(
                fake_plugin,
                dict(_DEFAULTS, cond_bypass="MISSING < 1"),
                {"key": "OILP1"},
            )

    # ------------------------------------------------------------------
    # Start bypass
    # ------------------------------------------------------------------

    def test_start_bypass_suppresses_until_value_rises(self):
        # start_bypass=True: no annunciation until value first climbs above
        # the low setpoint; after that, normal low-limit behavior applies.
        p = _make_plugin([{"key": "OILP1", "start_bypass": True}])

        database.write("OILP1", 5.0)           # below low (10.0), latch active
        self.assertFalse(p.items[0].item.annunciate)

        database.write("OILP1", 60.0)          # above low → latch released
        database.write("OILP1", 5.0)           # now below low again
        self.assertTrue(p.items[0].item.annunciate)

    def test_start_bypass_no_does_not_latch(self):
        # YAML parses "start_bypass: no" as False.  That must disable the
        # latch: a value already below its low limit at startup (low fuel
        # at a gateway restart) has to annunciate at once.
        p = _make_plugin([{"key": "OILP1", "start_bypass": False}])
        self.assertFalse(p.items[0].start_bypass_latch)
        database.write("OILP1", 5.0)
        self.assertTrue(p.items[0].item.annunciate)

    def test_none_low_alarm_falls_back_to_minimum_for_start_bypass_and_low_latch(self):
        database.get_raw_item("OILP1").aux["lowAlarm"] = None
        p = _make_plugin([{"key": "OILP1", "start_bypass": True}])
        p.stop()

        p.items[0].evaluate("OILP1", (5.0, False, False, False, False, False), None)
        self.assertFalse(p.items[0].start_bypass_latch)
        self.assertFalse(p.items[0].item.annunciate)

        p.items[0].evaluate("OILP1", (-1.0, False, False, False, False, False), None)
        self.assertTrue(p.items[0].item.annunciate)

    def test_none_high_alarm_falls_back_to_maximum(self):
        database.get_raw_item("OILP1").aux["highAlarm"] = None
        p = _make_plugin([{"key": "OILP1"}])
        p.stop()

        p.items[0].evaluate("OILP1", (121.0, False, False, False, False, False), None)

        self.assertTrue(p.items[0].item.annunciate)

    def test_aux_update_evaluates_current_base_value(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 105.0)
        p.items[0].item.annunciate = False

        p.items[0].evaluate("OILP1.highAlarm", None, None)

        self.assertTrue(p.items[0].item.annunciate)

    def test_str_reports_configuration(self):
        p = _make_plugin([{"key": "OILP1", "start_bypass": True}])

        self.assertEqual(
            str(p.items[0]),
            "\n".join(
                [
                    "OILP1",
                    "  Low Set Point: OILP1.lowAlarm",
                    "  High Set Point: OILP1.highAlarm",
                    "  Deadband: 1.0",
                    "  Start Bypass Enabled: Yes",
                    "  Conditional Bypass: None",
                ]
            ),
        )

    def test_null_item_lookup_raises_value_error(self):
        fake_plugin = SimpleNamespace(
            db_get_item=lambda _key: None,
            db_callback_add=lambda *_args: None,
        )

        with self.assertRaisesRegex(ValueError, "Key MISSING not found"):
            annunciate.AnnunciateItem(fake_plugin, _DEFAULTS, {"key": "MISSING"})

    # ------------------------------------------------------------------
    # stop() — callback removal
    # ------------------------------------------------------------------

    def test_stop_removes_callbacks(self):
        p = _make_plugin([{"key": "OILP1"}])
        database.write("OILP1", 60.0)
        self.assertFalse(p.items[0].item.annunciate)

        p.stop()

        # After stop, writing above threshold must NOT update the flag
        database.write("OILP1", 105.0)
        self.assertFalse(p.items[0].item.annunciate)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def test_get_status_reports_item_count(self):
        p = _make_plugin([{"key": "OILP1"}])
        self.assertEqual(p.get_status()["Item Count"], 1)

    def test_get_status_multiple_items(self):
        p = _make_plugin([{"key": "OILP1"}, {"key": "OILP1"}])
        self.assertEqual(p.get_status()["Item Count"], 2)


# ----------------------------------------------------------------------
# Deviation items (G39, fix-gateway#42/#7; pyEfis EFIS-ALTSEL-005/-009/-010)
# ----------------------------------------------------------------------

DEV_DB_CONFIG = """
entries:
- key: ALT
  description: Indicated Altitude
  type: float
  min: -1000.0
  max: 60000.0
  units: ft
  initial: 0.0
  tol: 2000

- key: ALTSEL
  description: Selected Altitude
  type: int
  min: -1000
  max: 60000
  units: ft
  initial: 3000
"""

_ALTDEV = {"key": "ALTSEL", "deviation_from": "ALT", "threshold": 200, "deadband": 20}


class TestDeviationItem(unittest.TestCase):

    def setUp(self):
        database.init(io.StringIO(DEV_DB_CONFIG))
        database.write("ALTSEL", 5000)

    def flag(self):
        return database.read("ALTSEL")[1]

    def test_within_threshold_does_not_annunciate(self):
        _make_plugin([_ALTDEV])
        for alt in (5000, 5199, 4801, 5200, 4800):     # 200 exactly is inside
            database.write("ALT", alt)
            self.assertFalse(self.flag(), alt)

    def test_deviation_above_or_below_annunciates(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5201)
        self.assertTrue(self.flag())
        database.write("ALT", 5000)
        database.write("ALT", 4799)
        self.assertTrue(self.flag())

    def test_flag_holds_through_deadband_then_clears(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5250)
        self.assertTrue(self.flag())
        database.write("ALT", 5190)                    # inside 200, not yet 180
        self.assertTrue(self.flag())
        database.write("ALT", 5181)
        self.assertTrue(self.flag())
        database.write("ALT", 5180)
        self.assertFalse(self.flag())
        database.write("ALT", 5190)                    # re-entry needs > 200 again
        self.assertFalse(self.flag())

    def test_moving_the_selected_altitude_evaluates(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5000)
        database.write("ALTSEL", 8000)                 # pilot sets a new target
        self.assertTrue(self.flag())
        database.write("ALTSEL", 5100)
        self.assertFalse(self.flag())

    def test_a_client_write_cannot_clear_a_live_deviation(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5500)
        self.assertTrue(self.flag())
        # A FIX client writing ALTSEL with its flags (annunciate False).
        database.write("ALTSEL", (5000, False, False, False))
        self.assertTrue(self.flag())

    def test_invalid_altitude_clears_the_alert(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5500)
        self.assertTrue(self.flag())
        for flag in ("fail", "bad"):
            item = database.get_raw_item("ALT")
            setattr(item, flag, True)
            self.assertFalse(self.flag(), flag)
            setattr(item, flag, False)
            self.assertTrue(self.flag(), flag)

    def test_old_altitude_clears_the_alert(self):
        _make_plugin([_ALTDEV])
        database.write("ALT", 5500)
        self.assertTrue(self.flag())
        database.get_raw_item("ALT").tol = 100         # age ALT out for real
        time.sleep(0.2)
        database.update()
        self.assertFalse(self.flag())
        database.write("ALT", 5500)                    # a fresh reading re-alerts
        self.assertTrue(self.flag())

    def test_threshold_and_deadband_come_from_config(self):
        _make_plugin([dict(_ALTDEV, threshold=500, deadband=0)])
        database.write("ALT", 5450)
        self.assertFalse(self.flag())
        database.write("ALT", 5501)
        self.assertTrue(self.flag())
        database.write("ALT", 5500)                    # no deadband: clears at 500
        self.assertFalse(self.flag())

    def test_deadband_defaults_to_zero_not_the_defaults_percentage(self):
        item = {"key": "ALTSEL", "deviation_from": "ALT", "threshold": 200}
        p = _make_plugin([item], defaults=dict(_DEFAULTS, deadband="1%"))
        self.assertEqual(p.items[0].deadband, 0.0)

    def test_bad_deviation_config_is_rejected(self):
        for bad in (
            {"key": "ALTSEL", "deviation_from": "ALT"},
            dict(_ALTDEV, threshold=0),
            dict(_ALTDEV, deadband=200),
            dict(_ALTDEV, deadband=-1),
            dict(_ALTDEV, deviation_from="NOPE"),
        ):
            with self.assertRaises(ValueError, msg=bad):
                _make_plugin([bad])

    def test_stop_removes_both_callbacks(self):
        p = _make_plugin([_ALTDEV])
        p.stop()
        database.write("ALT", 9000)
        database.write("ALTSEL", 1000)
        self.assertFalse(self.flag())

    def test_str_reports_configuration(self):
        p = _make_plugin([_ALTDEV])
        s = str(p.items[0])
        self.assertIn("Deviation From: ALT", s)
        self.assertIn("Threshold: 200.0", s)
        self.assertIn("Deadband: 20.0", s)


_CAPTURE = dict(_ALTDEV, arm_on_capture=True)


class TestDeviationArmOnCapture(unittest.TestCase):
    """arm_on_capture (Bill, AER-4328): no alert on the way to a new target,
    only on leaving one that ALT has reached."""

    def setUp(self):
        database.init(io.StringIO(DEV_DB_CONFIG))
        database.write("ALT", 3000)
        database.write("ALTSEL", 3000)

    def flag(self):
        return database.read("ALTSEL")[1]

    def test_climb_to_a_new_target_is_not_an_alert_leaving_it_is(self):
        _make_plugin([_CAPTURE])
        database.write("ALTSEL", 5000)                 # level at 3000, set 5000
        for alt in (3000, 3500, 4500, 4799):
            database.write("ALT", alt)
            self.assertFalse(self.flag(), alt)
        database.write("ALT", 4800)                    # captured: armed
        database.write("ALT", 5000)
        self.assertFalse(self.flag())
        database.write("ALT", 5201)                    # drifted off it
        self.assertTrue(self.flag())
        database.write("ALT", 5180)                    # back inside the deadband
        self.assertFalse(self.flag())

    def test_a_gateway_start_off_target_waits_for_a_capture(self):
        _make_plugin([_CAPTURE])                       # ALT and ALTSEL predate it
        database.write("ALT", 3300)
        self.assertFalse(self.flag())
        database.write("ALT", 3100)
        database.write("ALT", 3300)
        self.assertTrue(self.flag())

    def test_before_any_capture_nothing_alerts(self):
        _make_plugin([_CAPTURE])
        database.write("ALTSEL", 9000)
        database.write("ALT", 1000)                    # descending away: never armed
        self.assertFalse(self.flag())

    def test_a_new_target_disarms_and_clears_a_live_alert(self):
        _make_plugin([_CAPTURE])
        database.write("ALT", 3000)                    # captured at 3000
        database.write("ALT", 3300)
        self.assertTrue(self.flag())
        database.write("ALTSEL", 8000)                 # pilot selects a new altitude
        self.assertFalse(self.flag())
        database.write("ALT", 6000)
        self.assertFalse(self.flag())

    def test_a_small_bump_within_the_threshold_stays_armed(self):
        _make_plugin([_CAPTURE])
        database.write("ALT", 3000)
        database.write("ALTSEL", 3100)                 # still within 200: captured
        database.write("ALT", 3350)
        self.assertTrue(self.flag())

    def test_rewriting_the_same_target_does_not_disarm(self):
        _make_plugin([_CAPTURE])
        database.write("ALT", 3000)                    # captured
        database.write("ALT", 3300)
        self.assertTrue(self.flag())
        database.write("ALTSEL", 3000)                 # a client republishes it
        self.assertTrue(self.flag())
        database.write("ALTSEL", (3000, False, False, False))
        self.assertTrue(self.flag())

    def test_an_altitude_dropout_keeps_the_capture(self):
        _make_plugin([_CAPTURE])
        database.write("ALT", 3000)                    # captured
        item = database.get_raw_item("ALT")
        item.fail = True
        database.write("ALT", 3300)
        self.assertFalse(self.flag())                  # invalid: no alert
        item.fail = False
        self.assertTrue(self.flag())                   # back, still captured

    def test_str_reports_arm_on_capture(self):
        p = _make_plugin([_CAPTURE])
        self.assertIn("Arm On Capture: Yes", str(p.items[0]))
        p2 = _make_plugin([_ALTDEV])
        self.assertIn("Arm On Capture: No", str(p2.items[0]))


def test_shipped_annunciate_config_alerts_altsel_at_200_ft():
    """The shipped connections/annunciate.yaml against the shipped database:
    every item builds, and ALTSEL alerts 200 ft off a captured altitude, not
    on the climb to it (EFIS-ALTSEL-005/-009; arm on capture, AER-4328)."""
    import yaml

    database.init("src/fixgw/config/database.yaml")
    with open("src/fixgw/config/connections/annunciate.yaml") as f:
        config = yaml.safe_load(f)["annunciate"]
    p = annunciate.Plugin("annunciate", config, None)
    p.run()
    dev = [i for i in p.items if isinstance(i, annunciate.DeviationItem)]
    assert [(i.key, i.ref_key, i.threshold, i.arm_on_capture) for i in dev] == [
        ("ALTSEL", "ALT", 200.0, True)
    ]

    database.write("ALT", 3000)
    database.write("ALTSEL", 5000)
    database.write("ALT", 4000)                        # the climb: no alert
    assert database.read("ALTSEL")[1] is False
    database.write("ALT", 5100)                        # captured
    assert database.read("ALTSEL")[1] is False
    database.write("ALT", 5300)                        # left it
    assert database.read("ALTSEL")[1] is True
    p.stop()


if __name__ == "__main__":
    unittest.main()
