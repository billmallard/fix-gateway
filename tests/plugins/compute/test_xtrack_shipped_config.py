"""The shipped XTRACK writer, end to end (AER-4473).

Runs the real compute plugin on the real shipped compute.yaml against the real
shipped database: XTRACK has one writer, a GPSSRC select of the internal
plan's FPLXTK and the external navigator's EXTXTK, so it always comes from the
same source as the needle (GPSCDI).
"""

import time

import pytest

from fixgw import cfg

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"


def _functions():
    with open(COMPUTE_YAML) as f:
        return cfg.from_yaml(f.read())["compute"]["functions"]


@pytest.fixture
def plugin(database):
    import fixgw.plugins.compute

    with open(COMPUTE_YAML) as f:
        cc, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = fixgw.plugins.compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    yield database
    pl.shutdown()


def test_xtrack_has_one_writer_selected_like_the_needle():
    writers = [f for f in _functions() if f["output"] == "XTRACK"]
    assert writers == [
        {
            "function": "select",
            "inputs": ["GPSSRC", "FPLXTK", "EXTXTK"],
            "output": "XTRACK",
            "require_leader": False,
        }
    ]
    cdi = [f for f in _functions() if f["output"] == "GPSCDI"]
    assert cdi[0]["inputs"][0] == "GPSSRC"


def test_xtrack_follows_gpssrc(plugin):
    db = plugin
    db.write("FPLXTK", -1.5)
    db.write("EXTXTK", 2.5)
    db.write("GPSSRC", 0)                   # internal plan
    assert db.read("XTRACK")[0] == pytest.approx(-1.5)
    db.write("GPSSRC", 1)                   # external navigator
    assert db.read("XTRACK")[0] == pytest.approx(2.5)
    db.write("EXTXTK", 0.75)
    assert db.read("XTRACK")[0] == pytest.approx(0.75)
    db.write("FPLXTK", -9.0)                # the unselected source is ignored
    assert db.read("XTRACK")[0] == pytest.approx(0.75)


def test_xtrack_ignores_the_internal_waypoint_under_gpssrc_1(plugin):
    # xte used to measure against WPLAT/WPLON, the internal plan's waypoint.
    db = plugin
    db.write("GPSSRC", 1)
    db.write("EXTXTK", 2.0)
    db.write("LAT", 40.0)
    db.write("LONG", -88.0)
    db.write("WPLAT", 40.5)
    db.write("WPLON", -87.0)
    db.write("GPSCRS", 0.0)
    assert db.read("XTRACK")[0] == pytest.approx(2.0)
