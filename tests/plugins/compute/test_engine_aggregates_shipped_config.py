"""The engine aggregates follow the cylinder count (G94, fix-gateway#215; AER-6244).

The shipped compute.yaml computed EGTAVG1, EGTSPAN1, EGTMAX1 and CHTMAX1 from
cylinders 1-4, while variables.yaml declares c: 6. pyEfis's shipped screens
draw CHT11-14 and CHTMAX1 with no gauge for cylinders 5 and 6, so CHTMAX1 is
the only path a cylinder 5-6 exceedance has to the glass, and it ignored them.

Now the four take EGT1c/CHT1c, engine 1's cylinders up to c. Like sum (FUELQT,
AER-5391), average, max and span leave out a cylinder no source has written,
or a four-cylinder install on c: 6 would never publish them at all. min still
waits for every input: NAV1GSV is a min that must wait for both of its own.
"""

import io
import time

import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.compute as compute
from fixgw import cfg

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"
DEFAULT_INI = "src/fixgw/config/init_data/default.ini"
AGGREGATES = {
    "EGTAVG1": ("average", "EGT1c"),
    "EGTSPAN1": ("span", "EGT1c"),
    "EGTMAX1": ("max", "EGT1c"),
    "CHTMAX1": ("max", "CHT1c"),
}


def _compute_config():
    with open(COMPUTE_YAML) as f:
        return cfg.from_yaml(f.read(), metadata=True)


def _functions(outputs):
    cc, _ = _compute_config()
    return [f for f in cc["compute"]["functions"] if f["output"] in outputs]


# --- The shipped database, default.ini and the whole shipped compute.yaml ---


@pytest.fixture
def shipped(database):
    database.load_init_file(DEFAULT_INI)
    cc, meta = _compute_config()
    pl = compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    yield database
    pl.shutdown()


def test_the_aggregates_take_engine_1s_cylinders():
    found = {f["output"]: (f["function"], f["inputs"]) for f in _functions(AGGREGATES)}
    assert found == {k: (fn, [i]) for k, (fn, i) in AGGREGATES.items()}


def test_cht15_above_its_alarm_raises_chtmax1_past_the_alarm(shipped):
    db = shipped
    high_alarm = db.get_raw_item("CHTMAX1").get_aux_value("highAlarm")
    assert high_alarm == pytest.approx(232.2222222)
    for n in range(1, 7):
        db.write(f"CHT1{n}", 180.0)
    db.write("CHT15", 240.0)
    value, _, old, bad, fail, _ = db.read("CHTMAX1")
    assert value == pytest.approx(240.0)
    assert value > high_alarm
    assert (old, bad, fail) == (False, False, False)


def test_egt16_is_the_hottest_and_widest(shipped):
    db = shipped
    for n, egt in zip(range(1, 7), [600.0, 610.0, 620.0, 630.0, 640.0, 690.0]):
        db.write(f"EGT1{n}", egt)
    assert db.read("EGTMAX1")[0] == pytest.approx(690.0)
    assert db.read("EGTSPAN1")[0] == pytest.approx(90.0)
    assert db.read("EGTAVG1")[0] == pytest.approx(631.6666667)


def test_a_four_cylinder_install_still_publishes_from_its_four(shipped):
    db = shipped
    for n, (egt, cht) in enumerate(
        [(600.0, 170.0), (620.0, 180.0), (640.0, 190.0), (660.0, 200.0)], start=1
    ):
        db.write(f"EGT1{n}", egt)
        db.write(f"CHT1{n}", cht)  # CHT15, CHT16, EGT15, EGT16 never written
    expected = {
        "CHTMAX1": 200.0,
        "EGTMAX1": 660.0,
        "EGTAVG1": 630.0,
        "EGTSPAN1": 60.0,
    }
    for key, want in expected.items():
        value, _, old, bad, fail, _ = db.read(key)
        assert value == pytest.approx(want), key
        assert (old, bad, fail) == (False, False, False), key
        assert "compute" in fixdatabase.writers()[key]


# --- The aggregates on a synthetic database, with a clock the test ticks ---


class Clock:
    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now


def _db(engines, cylinders):
    return f"""
variables:
  e: {engines}
  c: {cylinders}
entries:
- {{key: EGTec, description: EGT %e %c, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 2000}}
- {{key: CHTec, description: CHT %e %c, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 2000}}
- {{key: EGTAVGe, description: EGT avg %e, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 0}}
- {{key: EGTSPANe, description: EGT span %e, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 0}}
- {{key: EGTMAXe, description: EGT max %e, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 2000}}
- {{key: CHTMAXe, description: CHT max %e, type: float, min: 0.0, max: 1000.0, initial: 0.0, tol: 2000}}
"""


@pytest.fixture
def engine(monkeypatch):
    plugins = []
    clock = Clock()

    def make(engines, cylinders):
        fixdatabase.init(io.StringIO(_db(engines, cylinders)))
        fixdatabase._stop_update_thread()  # the test ticks the clock itself
        monkeypatch.setattr(fixdatabase, "time", clock)
        monkeypatch.setattr(compute.quorum, "leader", True)
        _, meta = _compute_config()
        pl = compute.Plugin("compute", {"functions": _functions(AGGREGATES)}, meta)
        pl.start()
        plugins.append(pl)
        return clock

    yield make
    for pl in plugins:
        pl.shutdown()


def _read_all():
    return {k: fixdatabase.read(k) for k in AGGREGATES}


@pytest.mark.parametrize("c", [4, 6, 8])
def test_the_aggregates_cover_every_cylinder_up_to_c(engine, c):
    engine(1, c)
    for n in range(1, c + 1):
        fixdatabase.write(f"EGT1{n}", 500.0 + n)
        fixdatabase.write(f"CHT1{n}", 100.0 + n)
    r = _read_all()
    assert r["EGTMAX1"][0] == pytest.approx(500.0 + c)
    assert r["CHTMAX1"][0] == pytest.approx(100.0 + c)
    assert r["EGTSPAN1"][0] == pytest.approx(c - 1)
    assert r["EGTAVG1"][0] == pytest.approx(500.0 + (c + 1) / 2)


def test_the_aggregates_publish_from_the_first_cylinder_written(engine):
    engine(1, 6)
    fixdatabase.write("EGT13", 600.0)
    fixdatabase.write("CHT13", 190.0)
    r = _read_all()
    assert r["EGTAVG1"][0] == pytest.approx(600.0)
    assert r["EGTMAX1"][0] == pytest.approx(600.0)
    assert r["EGTSPAN1"][0] == pytest.approx(0.0)
    assert r["CHTMAX1"][0] == pytest.approx(190.0)
    for key, (_, _, old, bad, fail, _) in r.items():
        assert (old, bad, fail) == (False, False, False), key


def test_a_cylinder_never_written_does_not_flag_the_aggregates(engine):
    clock = engine(1, 6)
    for n in range(1, 5):
        fixdatabase.write(f"EGT1{n}", 600.0)
        fixdatabase.write(f"CHT1{n}", 190.0)
    clock.now += 2.5                   # cylinders 5 and 6 were never written
    fixdatabase.update()               # their expiry fires the callbacks
    for n in range(1, 5):
        fixdatabase.write(f"EGT1{n}", 600.0 + n)
        fixdatabase.write(f"CHT1{n}", 190.0 + n)
    assert fixdatabase.read("CHT15")[2] is True
    r = _read_all()
    assert r["CHTMAX1"][0] == pytest.approx(194.0)
    assert r["EGTAVG1"][0] == pytest.approx(602.5)
    for key, (_, _, old, bad, fail, _) in r.items():
        assert (old, bad, fail) == (False, False, False), key


def test_a_cylinder_that_stops_reporting_makes_the_aggregates_old(engine):
    clock = engine(1, 6)
    for n in range(1, 7):
        fixdatabase.write(f"CHT1{n}", 190.0)
    clock.now += 2.5                   # CHT15 outlives its 2000 ms tol
    for n in (1, 2, 3, 4, 6):
        fixdatabase.write(f"CHT1{n}", 190.0)
    assert fixdatabase.read("CHT15")[2] is True
    assert fixdatabase.read("CHTMAX1")[2] is True
    fixdatabase.write("CHT15", 200.0)  # and comes back
    value, _, old, bad, fail, _ = fixdatabase.read("CHTMAX1")
    assert value == pytest.approx(200.0)
    assert (old, bad, fail) == (False, False, False)


def test_a_failed_cylinder_5_fails_chtmax1(engine):
    engine(1, 6)
    for n in range(1, 5):
        fixdatabase.write(f"CHT1{n}", 190.0)
    fixdatabase.write("CHT15", (190.0, False, False, True))  # (value, annunc, bad, fail)
    value, _, _, _, fail, _ = fixdatabase.read("CHTMAX1")
    assert fail is True and value == 0.0


def test_engine_2s_cylinders_stay_out_of_engine_1s_aggregates(engine):
    engine(2, 4)
    for n in range(1, 5):
        fixdatabase.write(f"EGT1{n}", 600.0)
        fixdatabase.write(f"CHT1{n}", 190.0)
        fixdatabase.write(f"EGT2{n}", 900.0)
        fixdatabase.write(f"CHT2{n}", 300.0)
    r = _read_all()
    assert r["EGTMAX1"][0] == pytest.approx(600.0)
    assert r["EGTAVG1"][0] == pytest.approx(600.0)
    assert r["EGTSPAN1"][0] == pytest.approx(0.0)
    assert r["CHTMAX1"][0] == pytest.approx(190.0)


# --- min is unchanged: NAV1GSV waits for both of its inputs ----------------


def test_nav1gsv_still_waits_for_nav1gsnf(shipped):
    db = shipped
    before = db.read("NAV1GSV")
    db.write("NAV1DH", 1.0)            # NAV1GSNF is not written
    assert db.read("NAV1GSV") == before
    assert "compute" not in fixdatabase.writers().get("NAV1GSV", [])
