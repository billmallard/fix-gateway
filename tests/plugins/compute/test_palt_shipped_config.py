"""The shipped PALT writer, end to end (P3c, AER-4459).

Runs the real compute plugin on the real shipped compute.yaml against the real
shipped database: PALT is indicated altitude read back to 29.92, it has one
writer, and it goes live without anyone touching BARO.
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


def test_palt_has_one_writer_fed_by_baro_and_alt():
    writers = [f for f in _functions() if f["output"] == "PALT"]
    assert writers == [
        {"function": "altp", "inputs": ["BARO", "ALT"], "output": "PALT"}
    ]


def test_palt_goes_live_on_alt_alone(plugin):
    db = plugin
    db.write("ALT", 4500.0)                 # BARO never written: schema 29.92
    value, _, old, bad, fail, _ = db.read("PALT")
    assert value == pytest.approx(4500.0, abs=2.0)
    assert (old, bad, fail) == (False, False, False)


def test_palt_follows_the_altimeter_setting(plugin):
    db = plugin
    db.write("BARO", 30.42)
    db.write("ALT", 5500.0)
    assert db.read("PALT")[0] == pytest.approx(5042.0, abs=2.0)


def test_failed_alt_fails_palt(plugin):
    db = plugin
    db.write("ALT", 5500.0)
    item = db.get_raw_item("ALT")
    item.fail = True
    value, _, _, _, fail, _ = db.read("PALT")
    assert fail is True and value == 0.0
