"""remap carries its index's quality to its output (CMP1, AER-6834).

remap wrote a bare value, which stamped the output fresh and left its flags
as they were.  Three of the four shipped remaps are glideslope validity
(EXTGSF -> EXTGSV, NAV1GSF -> NAV1GSNF, NAV2GSF -> NAV2GSNF), so with
NAV1GSF failed NAV1GSNF read 1.0 with every flag clear, refreshed by the very
callback that carried the failure.  NAV1GSV is a min over NAV1DH and
NAV1GSNF, so the HSI drew a valid glideslope from a failed flag source.

Now remap publishes through _publish like every other compute function: the
index's old, bad, fail and secfail flags go with the table value in one
update, and a failed output reads 0.0.
"""

import io
import time

import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.compute as compute
from fixgw import cfg

# --- remap on its own, on a small database with a hand-ticked clock -------

DB = """
entries:
- {key: I1, type: float, min: 0.0, max: 10.0, initial: 1.0, tol: 2000}
- {key: O1, type: float, min: 0.0, max: 10.0, initial: 0.0, tol: 2000}
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class Parent:
    def db_get_item(self, key):
        return fixdatabase.get_raw_item(key)

    def db_read(self, key):
        return fixdatabase.read(key)


@pytest.fixture
def remap(monkeypatch):
    fixdatabase.init(io.StringIO(DB))
    fixdatabase._stop_update_thread()  # the test ticks the clock itself
    clock = Clock()
    monkeypatch.setattr(fixdatabase, "time", clock)
    monkeypatch.setattr(compute.quorum, "leader", True)
    f = compute.remapFunction(["I1"], "O1", [1, 0], True)
    fixdatabase.callback_add("compute", "I1", f, Parent())
    seen = []
    fixdatabase.callback_add("recorder", "O1", lambda k, v, u: seen.append(v), None)
    fixdatabase.write("I1", (0.0, False, False, False))  # flag out of view
    return clock, seen


def flags(key):
    _, _, old, bad, fail, secfail = fixdatabase.read(key)
    return dict(old=old, bad=bad, fail=fail, secfail=secfail)


CLEAR = dict(old=False, bad=False, fail=False, secfail=False)


def test_a_fresh_good_index_gives_the_table_value_with_every_flag_clear(remap):
    assert fixdatabase.read("O1")[0] == 1.0
    assert flags("O1") == CLEAR
    fixdatabase.write("I1", 1.0)
    assert fixdatabase.read("O1")[0] == 0.0
    assert flags("O1") == CLEAR


def test_a_failed_index_fails_the_output_in_one_update(remap):
    _, seen = remap
    seen.clear()
    fixdatabase.write("I1", (0.0, False, False, True))  # (value, annunc, bad, fail)
    value, _, _, _, fail, _ = fixdatabase.read("O1")
    assert fail and value == 0.0
    assert seen and all(t == fixdatabase.read("O1") for t in seen), seen


def test_an_index_that_outlives_its_tol_turns_the_output_old(remap):
    clock, _ = remap
    clock.now += 2.5
    fixdatabase.update()
    assert fixdatabase.read("I1")[2]
    assert flags("O1") == dict(CLEAR, old=True)
    assert fixdatabase.read("O1")[0] == 1.0


def test_an_index_its_writer_marks_old_turns_the_output_old(remap):
    fixdatabase.get_raw_item("I1").old = True
    assert flags("O1") == dict(CLEAR, old=True)


def test_bad_and_secfail_carry_through(remap):
    fixdatabase.write("I1", (0.0, False, True, False))
    assert flags("O1") == dict(CLEAR, bad=True)
    fixdatabase.write("I1", (0.0, False, False, False, True))
    assert flags("O1") == dict(CLEAR, secfail=True)


def test_a_recovered_index_clears_the_output(remap):
    clock, _ = remap
    fixdatabase.write("I1", (0.0, False, False, True))
    clock.now += 2.5
    fixdatabase.update()
    assert flags("O1")["fail"] and flags("O1")["old"]
    fixdatabase.write("I1", (0.0, False, False, False))
    assert fixdatabase.read("O1")[0] == 1.0
    assert flags("O1") == CLEAR


# --- The shipped NAV1 glideslope chain, end to end -------------------------

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"


def test_the_shipped_glideslope_flags_are_remaps():
    with open(COMPUTE_YAML) as f:
        functions = cfg.from_yaml(f.read())["compute"]["functions"]
    found = {f["output"]: (f["inputs"], f["table"]) for f in functions
             if f["function"] == "remap"}
    for src, out in (("EXTGSF", "EXTGSV"), ("NAV1GSF", "NAV1GSNF"),
                     ("NAV2GSF", "NAV2GSNF")):
        assert found[out] == ([src], [1, 0])


@pytest.fixture
def shipped(database):
    with open(COMPUTE_YAML) as f:
        cc, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    # A receiver on the localizer and glideslope, both flags out of view.
    database.write("NAV1DH", 1.0)
    database.write("NAV1GSF", (0.0, False, False, False))
    yield database
    pl.shutdown()


def test_a_good_nav1_glideslope_flag_reads_valid(shipped):
    db = shipped
    for key in ("NAV1GSNF", "NAV1GSV"):
        value, _, old, bad, fail, secfail = db.read(key)
        assert value == 1.0, key
        assert not any((old, bad, fail, secfail)), (key, db.read(key))


def test_a_failed_nav1_glideslope_flag_fails_the_glideslope(shipped):
    db = shipped
    db.write("NAV1GSF", (0.0, False, False, True))
    for key in ("NAV1GSNF", "NAV1GSV"):
        value, _, _, _, fail, _ = db.read(key)
        assert fail and value == 0.0, (key, db.read(key))


def test_an_old_nav1_glideslope_flag_turns_the_glideslope_old(shipped):
    db = shipped
    db.get_raw_item("NAV1GSF").old = True
    for key in ("NAV1GSNF", "NAV1GSV"):
        assert db.read(key)[2], (key, db.read(key))
