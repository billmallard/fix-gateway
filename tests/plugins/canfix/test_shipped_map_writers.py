"""The shipped CAN-FIX input map never writes a key compute owns (AER-4474).

An aircraft config loads CANFIX and COMPUTE together. Any canfix input whose
fixid is also a compute output gives that key two writers, and the last frame
in wins. CDI/GSI were the case: 0x1C0/0x1C1 wrote them while the NAVSRC select
did too.
"""

from fixgw import cfg

MAP_YAML = "src/fixgw/config/canfix/map.yaml"
COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"


def _load(path):
    with open(path) as f:
        return cfg.from_yaml(f.read())


def _input_fixids():
    by_canid = {}
    for row in _load(MAP_YAML)["inputs"]:
        for key in str(row["fixid"]).split(","):
            by_canid.setdefault(row["canid"], []).append(key.strip())
    return by_canid


def test_no_canfix_input_writes_a_compute_output():
    outputs = {f["output"] for f in _load(COMPUTE_YAML)["compute"]["functions"]}
    inputs = {k for keys in _input_fixids().values() for k in keys}
    assert inputs & outputs == set()


def test_nav_deviation_lands_on_the_nav1_source_keys():
    by_canid = _input_fixids()
    assert by_canid[0x1C0] == ["NAV1CDI"]           # VOR/LOC Deviation (448)
    assert by_canid[0x1C1] == ["NAV1GSI"]           # Glideslope Deviation (449)
