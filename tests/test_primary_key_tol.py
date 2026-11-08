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
# are listed in SETTINGS_AND_EVENTS with the reason, and are not checked.
import re
import time
from pathlib import Path

import pytest

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
    "COURSE", "CDI", "GSI", "HEAD", "TRACKM", "GS", "GSV", "CDIV", "TOFROM",
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
}

TABLE = Path(__file__).resolve().parents[1] / "docs/primary_flight_key_tol.md"


def test_lists_name_real_keys(database):
    # A typo in either list would otherwise drop a key from the check.
    keys = set(database.listkeys())
    assert DISPLAYED_VALUES - keys == set()
    assert set(SETTINGS_AND_EVENTS) - keys == set()
    assert DISPLAYED_VALUES & set(SETTINGS_AND_EVENTS) == set()


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
    assert set(rows) == DISPLAYED_VALUES | set(SETTINGS_AND_EVENTS)
