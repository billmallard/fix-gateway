"""The shipped lateral-validity wiring, end to end (fix-gateway#20).

Runs the real compute plugin on the real shipped compute.yaml against the real
shipped database, so the keys, the select and the X-Plane NAV#DH input are
proved together rather than one function at a time.
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


def test_cdiv_keys_are_declared_and_cdiv_has_one_writer(database):
    for key in ("NAV1CDIV", "NAV2CDIV", "CDIV"):
        item = database.get_raw_item(key)
        assert (item.min, item.max) == (0.0, 1.0)
    with pytest.raises(KeyError):
        database.get_raw_item("GPSCDIV")            # no honest GPS source: not minted
    writers = [f for f in _functions() if f["output"] == "CDIV"]
    assert writers == [{
        "function": "select",
        "inputs": ["NAVSRC", "NAV1CDIV", "NAV2CDIV", 1.0],
        "output": "CDIV",
        "require_leader": False,
    }]


def test_existing_selects_are_unchanged():
    by_output = {f["output"]: f["inputs"] for f in _functions()}
    assert by_output["GSV"] == ["NAVSRC", "NAV1GSV", "NAV2GSV", "GPSGSV"]
    assert by_output["TOFROM"] == ["NAVSRC", "NAV1TF", "NAV2TF", "GPSTF"]
    assert by_output["COURSE"] == ["NAVSRC", "NAV1CRS", "NAV2CRS", "GPSCRS"]
    assert by_output["CDI"] == ["NAVSRC", "NAV1CDI", "NAV2CDI", "GPSCDI"]


def test_not_received_localizer_reads_cdiv_zero(plugin):
    db = plugin
    db.write("NAV2DH", 1.0)
    db.write("NAV1DH", 0.0)                          # LOC 1 tuned, not received
    db.write("NAVSRC", 0)
    assert db.read("CDIV")[0] == 0.0
    db.write("NAV1DH", 1.0)                          # captured
    assert db.read("CDIV")[0] == 1.0
    db.write("NAVSRC", 1)
    assert db.read("CDIV")[0] == 1.0
    db.write("NAV2DH", 0.0)
    assert db.read("CDIV")[0] == 0.0
    db.write("NAVSRC", 2)                            # GPS: not gated
    assert db.read("CDIV")[0] == 1.0
