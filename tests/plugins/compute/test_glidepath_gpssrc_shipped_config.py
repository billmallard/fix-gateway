"""The shipped GPS glidepath follows GPSSRC, end to end (AER-5145, fix-gateway#137).

The lateral triple (GPSCRS/GPSCDI/GPSTF) is selected on GPSSRC, but the vertical
pair took X-Plane's own GPS glidepath unconditionally. With GPSSRC 0 (pyEFIS's
internal plan, which has no vertical guidance) the HSI still drew X-Plane's
glidepath diamond as though it belonged to the selected source. Sim-bench session
8f9eb80b0daa (2026-11-07, fp8-ksmx-rnav30-missed) logged GSV 1.0 / GSI 0.0 all the
way through the missed approach and the hold.

Runs the real compute plugin on the real shipped compute.yaml against the real
shipped database. The external glidepath is written to whichever keys the shipped
xplane.yaml maps X-Plane's GPS vdef and glideslope flag to, so this reproduces the
bench feed rather than naming the keys the fix chose.
"""

import time

import pytest
import yaml

from fixgw import cfg

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"
XPLANE_YAML = "src/fixgw/config/connections/xplane.yaml"


def _functions():
    with open(COMPUTE_YAML) as f:
        return cfg.from_yaml(f.read())["compute"]["functions"]


def _xplane_key(dataref):
    with open(XPLANE_YAML) as f:
        datarefs = yaml.safe_load(f)["xplane"]["datarefs"]
    keys = [k for k, v in datarefs.items()
            if (v[0] if isinstance(v, list) else v) == dataref]
    assert len(keys) == 1, (dataref, keys)
    return keys[0]


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


def _external_glidepath_active(db, deviation):
    db.write(_xplane_key("sim/cockpit/radios/gps_vdef_dot"), deviation)
    db.write(_xplane_key("sim/cockpit2/radios/indicators/gps_flag_glideslope"), 0.0)


def test_internal_plan_hides_the_external_glidepath(plugin):
    # The bench repro: HSI on GPS, internal plan selected, X-Plane's GPS
    # publishing a valid glidepath.
    db = plugin
    db.write("NAVSRC", 2)
    db.write("GPSSRC", 0)
    _external_glidepath_active(db, 0.3)
    assert db.read("GPSGSV")[0] == 0.0
    assert db.read("GSV")[0] == 0.0          # no GS diamond
    assert db.read("GPSGSI")[0] == 0.0
    assert db.read("GSI")[0] == 0.0


def test_external_navigator_shows_its_glidepath(plugin):
    db = plugin
    db.write("NAVSRC", 2)
    db.write("GPSSRC", 1)
    _external_glidepath_active(db, 0.3)
    assert db.read("GPSGSV")[0] == 1.0
    assert db.read("GSV")[0] == 1.0
    assert db.read("GSI")[0] == pytest.approx(0.3)
    db.write("GPSSRC", 0)                    # pilot selects the internal plan
    assert db.read("GSV")[0] == 0.0
    assert db.read("GSI")[0] == 0.0
    db.write("GPSSRC", 1)                    # and back
    assert db.read("GSV")[0] == 1.0
    assert db.read("GSI")[0] == pytest.approx(0.3)


def test_external_glidepath_flag_still_hides_the_diamond(plugin):
    db = plugin
    db.write("NAVSRC", 2)
    db.write("GPSSRC", 1)
    _external_glidepath_active(db, 0.3)
    db.write(_xplane_key("sim/cockpit2/radios/indicators/gps_flag_glideslope"), 1.0)
    assert db.read("GSV")[0] == 0.0


def test_glidepath_pair_is_selected_like_the_lateral_triple():
    by_output = {f["output"]: f for f in _functions()}
    for out in ("GPSCRS", "GPSCDI", "GPSTF", "GPSGSI", "GPSGSV"):
        assert by_output[out]["function"] == "select"
        assert by_output[out]["inputs"][0] == "GPSSRC"
    # One writer each.
    for out in ("GPSGSI", "GPSGSV"):
        assert [f["output"] for f in _functions()].count(out) == 1
