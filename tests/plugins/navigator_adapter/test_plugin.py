"""Tests for the navigator_adapter Plugin class.

Covers adapter registry lookup, unknown adapter rejection, and get_status().
Serial I/O is not exercised (no port opened).
"""

import unittest
from unittest.mock import MagicMock

from fixgw.plugins.navigator_adapter import Plugin, _ADAPTERS
from fixgw.plugins.navigator_adapter.adapters.garmin_nmea import GarminNmeaAdapter


def _make_plugin(adapter_name="garmin_nmea", extra=None):
    config = {"port": "/dev/null", "baud": "9600", "adapter": adapter_name}
    if extra:
        config.update(extra)
    p = Plugin.__new__(Plugin)
    p.log = MagicMock()
    p.config = config

    # Call __init__ via PluginBase without the thread machinery
    from fixgw.plugins.navigator_adapter import Plugin as P
    P.__init__(p, "test_navigator", config, None)
    return p


class TestPluginRegistry(unittest.TestCase):

    def test_garmin_nmea_in_registry(self):
        self.assertIn("garmin_nmea", _ADAPTERS)
        self.assertIs(_ADAPTERS["garmin_nmea"], GarminNmeaAdapter)

    def test_unknown_adapter_raises(self):
        with self.assertRaises((ValueError, Exception)):
            _make_plugin(adapter_name="nonexistent_vendor")

    def test_known_adapter_instantiates(self):
        p = _make_plugin("garmin_nmea")
        self.assertIsInstance(p._adapter, GarminNmeaAdapter)

    def test_get_status_keys(self):
        p = _make_plugin("garmin_nmea")
        status = p.get_status()
        self.assertIn("Adapter", status)
        self.assertIn("Parse Errors", status)
        self.assertIn("Last Sentence", status)

    def test_get_status_adapter_name(self):
        p = _make_plugin("garmin_nmea")
        self.assertEqual(p.get_status()["Adapter"], "garmin_nmea")

    def test_custom_cdi_scale_stored(self):
        p = _make_plugin("garmin_nmea", {"cdi_full_scale_nm": "0.3"})
        self.assertAlmostEqual(p._thread.cdi_full_scale_nm, 0.3, places=4)


if __name__ == "__main__":
    unittest.main()
