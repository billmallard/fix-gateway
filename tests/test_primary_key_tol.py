# CERT2 (AER-5032): every FIX key pyEfis shows the pilot on the PFD, HSI and
# engine pages has a nonzero tol in the shipped database, so it reads old
# once its source stops writing. tol 0 (the define_item default) disables the
# expiry: a dead source would leave its last value on the glass, unflagged.
#
# The key list is the shipped pyEfis config on dev (8017016a): the PFD, EMS
# and EMS2 screens of the default list (PFD_AI_ONLY shows a subset of the
# PFD; Data Status, Radio and Flight Plan are out of scope), with includes and
# preferences resolved,
# plus the keys each instrument class subscribes to in Python. It is the
# table in docs/primary_flight_key_tol.md; the last test keeps the two in step.
#
# A pilot setting (BARO, the altitude bug) is written only when the pilot
# changes it, so a tol would flag it old seconds after every edit. Those keys
# are listed in SETTINGS_AND_EVENTS with the reason, and must have tol 0.
#
# COURSE is neither: the compute select copies it from the source NAVSRC
# picks, with that source's flags, so it is as fresh as its source (CERT2a,
# AER-5270). It is in DERIVED_BY_SELECT and checked end to end.
import re
import time
from pathlib import Path

import pytest

from fixgw import cfg

PFD = {
    # Attitude indicator / flight path marker / SVS feed (ai_widget.py)
    "PITCH", "ROLL", "ALAT", "TAS", "VS", "GS", "TRACK", "HEAD", "VPATH",
    "LAT", "LONG", "ALT", "MAGVAR",
    # Tapes and readouts
    "IAS", "TIMEZ", "TIMEL",
    # COM radio block (panel state 2)
    "COMACTFREQ1", "COMACTRXLEVEL1", "COMTXPWR1", "COMVSWR1",
    "COMACTRX1", "COMACTTX1",
}

HSI = {
    "CDI", "GSI", "HEAD", "TRACKM", "GS", "GSV", "CDIV", "TOFROM",
    "BRG1", "BRG2", "VOR1BRG", "VOR2BRG",
}

# EMS and EMS2 pages, and the PFD's engine arcs and fuel bars.
EMS = {
    "TACH1", "OILP1", "H2OT1", "VOLT", "OILT1", "OAT", "CURRNT", "CAT",
    "FUELP1", "FUELF1", "FUELQT", "FUELQ1", "FUELQ2", "FUELQ3",
    "CHT11", "CHT12", "CHT13", "CHT14", "CHTMAX1",
    "EGT11", "EGT12", "EGT13", "EGT14", "EGTMAX1",
    "THR1", "MAP1",
}

DISPLAYED_VALUES = PFD | HSI | EMS

# Shown on those pages, but written on change or once, so tol 0 is right.
SETTINGS_AND_EVENTS = {
    "BARO": "pilot setting, written when the pilot turns the knob",
    "ALTSEL": "pilot setting (altitude bug), written on change",
    "BANKBUG": "pilot setting (target-bank bug), written on change",
    "COMACTNAME1": "frequency name, written when a frequency is tuned",
    "MAVMSG": "autopilot event text, written when the autopilot has news",
    "ACID": "aircraft identifier, a configured constant",
    "LEADER": "set True at start-up; only the quorum plugin (off) rewrites it",
    "GATEWAY_VERSION": "written once at start-up",
    "PYEFIS_VERSION": "written once by pyEfis at start-up",
    "EGTBTN1": "EGT mode button",
    "EGTBTN2": "EGT mode button",
    "EGTBTN3": "EGT mode button",
    "EGTBTN4": "EGT mode button",
    # CERT2a (AER-5270): pilot selections. Only the X-Plane connection streams
    # any of them (HEADBUG, NAV1CRS, NAV2CRS, from the sim's own panel).
    "HEADBUG": "pilot setting (heading bug), written on change",
    "NAVSRC": "pilot selection (CDI source), written on change",
    "NAV1CRS": "pilot setting (NAV1 OBS), written on change",
    "NAV2CRS": "pilot setting (NAV2 OBS), written on change",
    "BRG1SRC": "pilot selection (bearing pointer 1 source), written on change",
    "BRG2SRC": "pilot selection (bearing pointer 2 source), written on change",
    "COMSQUELCH1": "pilot setting (COM squelch), written on change",
}

# Shown, and written by a compute select that copies the selected source's
# value and flags. A tol of its own would flag it old whenever the selected
# source is a setting (NAV1CRS, NAV2CRS) that has not been edited lately.
DERIVED_BY_SELECT = {
    "COURSE": "NAVSRC select of NAV1CRS, NAV2CRS, GPSCRS",
}

TABLE = Path(__file__).resolve().parents[1] / "docs/primary_flight_key_tol.md"


def test_lists_name_real_keys(database):
    # A typo in either list would otherwise drop a key from the check.
    keys = set(database.listkeys())
    assert DISPLAYED_VALUES - keys == set()
    assert set(SETTINGS_AND_EVENTS) - keys == set()
    assert set(DERIVED_BY_SELECT) - keys == set()
    assert DISPLAYED_VALUES & set(SETTINGS_AND_EVENTS) == set()
    assert DISPLAYED_VALUES & set(DERIVED_BY_SELECT) == set()
    assert set(SETTINGS_AND_EVENTS) & set(DERIVED_BY_SELECT) == set()


@pytest.mark.parametrize("key", sorted(DISPLAYED_VALUES))
def test_displayed_value_has_a_tol(database, key):
    assert database.get_raw_item(key).tol != 0, (
        f"{key} is on the PFD/HSI/EMS path with tol 0: it never goes old"
    )


@pytest.mark.parametrize("key", sorted(DISPLAYED_VALUES))
def test_displayed_value_reads_old_after_its_tol(database, key):
    # The property the tol is for: fresh after a write, old once the source
    # has been silent for longer than the tol.
    item = database.get_raw_item(key)
    item.value = item.value[0]
    assert item.value[2] is False
    item.timestamp = time.time() - item.tol / 1000.0 - 0.5
    assert item.value[2] is True


def test_table_matches_the_lists():
    rows = re.findall(r"^\| `([A-Z0-9_]+)` \|", TABLE.read_text(), re.M)
    assert len(rows) == len(set(rows))
    assert set(rows) == (
        DISPLAYED_VALUES | set(SETTINGS_AND_EVENTS) | set(DERIVED_BY_SELECT)
    )


@pytest.mark.parametrize("key", sorted(SETTINGS_AND_EVENTS))
def test_setting_or_event_has_no_tol(database, key):
    # The other direction: a setting written on change must not read old
    # seconds after the pilot sets it.
    assert database.get_raw_item(key).tol == 0


COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"


@pytest.fixture
def compute(database):
    # The real compute plugin on the shipped compute.yaml.
    import fixgw.plugins.compute

    with open(COMPUTE_YAML) as f:
        cc, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = fixgw.plugins.compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    yield database
    pl.shutdown()


def _let_time_pass(db, seconds):
    # Age every key as if nothing had been written for `seconds`, then run
    # the expiry pass the server runs once a second.
    for key in db.listkeys():
        db.get_raw_item(key).timestamp -= seconds
    db.update()


def test_course_select_is_shipped(database):
    with open(COMPUTE_YAML) as f:
        functions = cfg.from_yaml(f.read())["compute"]["functions"]
    writers = [f for f in functions if f["output"] == "COURSE"]
    assert [(w["function"], w["inputs"]) for w in writers] == [
        ("select", ["NAVSRC", "NAV1CRS", "NAV2CRS", "GPSCRS"]),
    ]


@pytest.mark.parametrize("navsrc, obs", [(0, "NAV1CRS"), (1, "NAV2CRS")])
def test_obs_course_stays_fresh_after_an_edit(compute, navsrc, obs):
    # A VOR selected, no sim: the pilot sets the OBS once and flies. COURSE
    # is the pilot's own setting and must not read old seconds later.
    db = compute
    db.write("NAVSRC", navsrc)
    db.write(obs, 123.0)
    assert db.read("COURSE")[:3] == (123.0, False, False)
    _let_time_pass(db, 10)
    assert db.read("COURSE")[:3] == (123.0, False, False)


def test_gps_course_goes_old_when_its_source_stops(compute):
    # The safety direction still holds through the select: GPS selected and
    # GPSCRS's writer gone, COURSE reads old.
    db = compute
    db.write("NAVSRC", 2)
    db.write("GPSCRS", 45.0)
    assert db.read("COURSE")[:3] == (45.0, False, False)
    _let_time_pass(db, db.get_raw_item("GPSCRS").tol / 1000.0 + 0.5)
    assert db.read("COURSE")[2] is True


def test_bankbug_shape(database):
    # ATT1 (AER-5149): the target-bank bug is a magnitude, 0 = off, capped at
    # pyEfis's 65 deg unusual-attitude de-clutter, where the bug is hidden.
    item = database.get_raw_item("BANKBUG")
    assert item.dtype is float
    assert (item.min, item.max) == (0.0, 65.0)
    assert item.value[0] == 0.0
