"""The configurator's aircraft-parameters schema (export_param_schema)."""

from fixgw.tools import export_param_schema as eps

DEFAULT_INI = "src/fixgw/config/init_data/default.ini"


def _ias():
    speeds = next(g for g in eps.build_schema()["groups"] if g["id"] == "speeds")
    return next(k for k in speeds["keys"] if k["key"] == "IAS")


def test_on_ground_ias_setting_reaches_the_configurator():
    ias = _ias()
    assert "Vgnd" in ias["aux"]
    meta = ias["aux_meta"]["Vgnd"]
    assert meta["min"] < meta["default"] < meta["max"]
    assert "below" in meta["help"]


def test_on_ground_ias_default_matches_the_shipped_ini():
    with open(DEFAULT_INI) as f:
        line = next(l for l in f if l.startswith("IAS.Vgnd"))
    assert float(line.split("=")[1]) == _ias()["aux_meta"]["Vgnd"]["default"]


def test_keys_without_settings_carry_empty_aux_meta():
    for g in eps.build_schema()["groups"]:
        for k in g["keys"]:
            if k["key"] != "IAS":
                assert k["aux_meta"] == {}, k["key"]
