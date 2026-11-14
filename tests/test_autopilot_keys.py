# AFC1 (AER-5569): the autopilot and flight director FIX key contract,
# database/autopilot.yaml, from makerplane/briefs/autopilot_control_head_plan.md
# section 5.2 (maos-workspace). The `database` fixture loads the shipped
# database.yaml, so every test here reads the keys as the default
# configuration defines them. doc/autopilot_keys.md is the contract in prose;
# the last tests keep its tables and the yaml in step.
import re
import time
from pathlib import Path

import pytest
import yaml

YAML = Path("src/fixgw/config/database/autopilot.yaml")
DOC = Path("doc/autopilot_keys.md")

# key: (type, min, max, units, initial, tol)
TARGETS = {
    "VSSEL": ("int", -30000, 30000, "ft/min", 0, 0),
    "IASSEL": ("float", 0.0, 999.9, "knots", 0.0, 0),
}
REQUESTS = {
    "APCMD": ("str", None, None, "", "", 0),
    "APCMDSEQ": ("int", 0, 2147483647, "", 0, 0),
}
STATE = {
    "APENG": ("bool", None, None, "", False, 2000),
    "FDENG": ("bool", None, None, "", False, 2000),
    "YDENG": ("bool", None, None, "", False, 2000),
    "APLAT": ("str", None, None, "", "", 2000),
    "APLATARM": ("str", None, None, "", "", 2000),
    "APVERT": ("str", None, None, "", "", 2000),
    "APVERTARM": ("str", None, None, "", "", 2000),
    "APALTCAP": ("bool", None, None, "", False, 2000),
}
FD = {
    "FDPITCH": ("float", -90.0, 90.0, "deg", 0.0, 1000),
    "FDROLL": ("float", -180.0, 180.0, "deg", 0.0, 1000),
}
HEALTH = {
    "APCAPS": ("str", None, None, "", "", 2000),
    "APMSG": ("str", None, None, "", "", 2000),
    "APDISC": ("bool", None, None, "", False, 2000),
}
PILOT = {**TARGETS, **REQUESTS}
SOURCE = {**STATE, **FD, **HEALTH}
CONTRACT = {**PILOT, **SOURCE}

APCMD_VERBS = ["AP", "FD", "YD", "LVL", "HDG", "TRK", "NAV", "APR", "IAS",
               "VNAV", "VS", "ALT", "DISC"]


def yaml_items():
    return yaml.safe_load(YAML.read_text())["items"]


def test_autopilot_yaml_defines_exactly_the_contract():
    keys = [item["key"] for item in yaml_items()]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(CONTRACT)


@pytest.mark.parametrize("key", sorted(CONTRACT))
def test_key_loads_with_its_type_range_units_and_default(database, key):
    typestring, minval, maxval, units, initial, tol = CONTRACT[key]
    item = database.get_raw_item(key)
    assert item.typestring == typestring
    if typestring in ("int", "float"):
        assert (item.min, item.max) == (minval, maxval)
    assert item.units == units
    assert item.tol == tol
    value = database.read(key)[0]
    assert value == initial and type(value) is type(initial)
    assert item.written is False


# Brief section 5.7: stale is removed, not frozen. A display can only remove
# a stale FD bar or annunciation if the key goes old, and a key goes old only
# if it has a tol. Read from the yaml itself as well as the loaded database,
# so a custom.yaml override cannot hide a missing tol in the shipped file.
@pytest.mark.parametrize("key", sorted(SOURCE))
def test_every_state_fd_and_health_key_has_a_tol(database, key):
    shipped = {item["key"]: item for item in yaml_items()}[key]
    assert shipped.get("tol", 0) > 0, key + " has no tol in autopilot.yaml"
    assert database.get_raw_item(key).tol > 0


# Targets and requests are written when the pilot acts. A tol would flag a
# good setting old seconds after every edit.
@pytest.mark.parametrize("key", sorted(PILOT))
def test_targets_and_requests_never_go_old(database, key):
    assert database.get_raw_item(key).tol == 0


@pytest.mark.parametrize("key,tol", [("APENG", 2.0), ("FDPITCH", 1.0)])
def test_source_key_goes_old_one_tol_after_the_last_write(database, monkeypatch, key, tol):
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    value = True if key == "APENG" else 5.0
    database.write(key, value)

    now[0] += tol - 0.1
    assert database.read(key)[2] is False
    now[0] += 0.2
    assert database.read(key)[2] is True

    database.write(key, value)  # the source's next rewrite clears it at once
    assert database.read(key)[2] is False


# HEADBUG and ALTSEL are the HDG and ALT targets. The contract uses them as
# they are, from ahrs.yaml and gcu.yaml, and must not redefine them.
def test_existing_targets_are_used_not_redefined(database):
    keys = {item["key"] for item in yaml_items()}
    assert "HEADBUG" not in keys and "ALTSEL" not in keys
    assert database.get_raw_item("HEADBUG").units == "deg"
    assert database.get_raw_item("ALTSEL").units == "ft"


def test_apcmd_description_names_every_request(database):
    description = database.get_raw_item("APCMD").description
    named = re.findall(r"\b[A-Z]{2,4}\b", description.split(":", 1)[1])
    assert [v for v in named if v != "APCMDSEQ"][: len(APCMD_VERBS)] == APCMD_VERBS


def doc_rows():
    rows = {}
    for line in DOC.read_text().splitlines():
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == 8 and re.fullmatch(r"`[A-Z]+`", cells[0]):
            key, typestring, units, _range, initial, tol, writer, _ = cells
            key = key.strip("`")
            assert key not in rows, key + " has two rows in the doc"
            rows[key] = (typestring, units, initial, int(tol), writer)
    return rows


def test_doc_tables_match_the_yaml():
    rows = doc_rows()
    assert set(rows) == set(CONTRACT)
    for key, (typestring, _min, _max, units, initial, tol) in CONTRACT.items():
        doc_type, doc_units, doc_initial, doc_tol, writer = rows[key]
        assert doc_type == typestring, key
        assert doc_units == units, key
        assert doc_initial == (repr(initial).lower() if isinstance(initial, bool)
                               else '""' if initial == "" else str(initial)), key
        assert doc_tol == tol, key
        assert writer == ("pilot" if key in PILOT else "autopilot source"), key


# G39's pattern (tests/test_server.py): the keys ship in their own database
# file so an update delivers them. Reproduce an install from before AFC1 -- an
# unedited (timestamp-stamped) database.yaml with no autopilot include and no
# autopilot.yaml -- run the real update, and load the result.
def test_update_delivers_autopilot_keys_to_an_install_without_them(tmp_path):
    import fixgw.database as database
    import fixgw.server as server

    root = tmp_path / "makerplane" / "fixgw"
    server.create_config_dir(str(root))
    config = root / "config"
    (config / "database" / "autopilot.yaml").unlink()
    dbyaml = config / "database.yaml"
    dbyaml.write_text(dbyaml.read_text().replace("- include: database/autopilot.yaml\n", ""))
    server.os.utime(dbyaml, (350039106.789, 350039106.789))

    database.init(str(dbyaml))
    assert "APENG" not in database.listkeys()

    server.create_config_dir(str(root))
    database.init(str(dbyaml))
    assert set(CONTRACT) <= set(database.listkeys())
    assert database.get_raw_item("FDROLL").tol == 1000
