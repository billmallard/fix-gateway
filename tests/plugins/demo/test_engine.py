# DEMO3 (AER-5625): the demo's engine follows the flight phase, burns fuel by
# its fuel flow, and runs one cylinder hot in the climb.  The hot cylinder's
# alarm must be annunciate's, from the configured limits, never a demo write:
# the last test flies a loop through the real annunciate plugin on the
# shipped annunciate.yaml and default.ini.

import sys

import pytest

import fixgw.plugin as plugin_base

import fixgw.database as database
from fixgw import cfg
from fixgw.plugins.demo import engine

pytest.importorskip("geographiclib")

from tests.plugins.demo.test_demo import FastClock, as_plugin, loops, make  # noqa: E402

DEFAULT_INI = "src/fixgw/config/init_data/default.ini"
ANNUNCIATE_YAML = "src/fixgw/config/connections/annunciate.yaml"
HOT = "CHT1{}".format(engine.HOT_CYLINDER)


def _ini_limits(key):
    out = {}
    with open(DEFAULT_INI) as f:
        for line in f:
            name, _, value = line.partition("=")
            if name.strip().startswith(key + "."):
                out[name.strip().split(".")[1]] = float(value)
    return out


LIMITS = _ini_limits(HOT)
WARN_ALARM = (LIMITS["highWarn"], LIMITS["highAlarm"])


@pytest.fixture(scope="module")
def modelled(flown):
    rows, eng = engine.add_engine(flown.rows, WARN_ALARM)
    return rows, eng, engine.phases(flown.rows)


def _series(rows, phases, key):
    return [(t, ph, row[key]) for (t, row), ph in zip(rows, phases) if key in row]


# ---------------------------------------------------------------------------
# The model, on the shipped route
# ---------------------------------------------------------------------------

def test_the_route_has_every_phase_but_the_hold_is_last(modelled):
    _, _, phases = modelled
    seen = []
    for ph in phases:
        if not seen or seen[-1] != ph:
            seen.append(ph)
    # en route, the approach descents, the go-around and missed climb, the hold
    assert set(seen) == {engine.CRUISE, engine.DESCENT, engine.TAKEOFF, engine.CLIMB, engine.HOLD}
    assert seen[-1] == engine.HOLD
    assert seen.index(engine.TAKEOFF) + 1 == seen.index(engine.CLIMB)


def test_power_follows_the_phase(modelled):
    rows, _, phases = modelled
    # settled readings: the last 10 s of each phase stretch longer than 30 s
    settled = {}
    start = 0
    for i in range(1, len(phases) + 1):
        if i == len(phases) or phases[i] != phases[start]:
            if rows[i - 1][0] - rows[start][0] > 30.0:
                tail = [r for t, r in rows[start:i] if "TACH1" in r and t > rows[i - 1][0] - 10.0]
                settled.setdefault(phases[start], []).extend(tail)
            start = i
    mean = {ph: {k: sum(r[k] for r in rs) / len(rs) for k in ("TACH1", "MAP1", "FUELF1")}
            for ph, rs in settled.items()}
    order = [engine.CLIMB, engine.CRUISE, engine.HOLD, engine.DESCENT]
    for k in ("TACH1", "MAP1", "FUELF1"):
        vals = [mean[ph][k] for ph in order]
        assert vals == sorted(vals, reverse=True), (k, vals)
    # each settles near its phase's setting (the climb's fuel flow carries
    # the enrichment, and its manifold pressure the altitude)
    for ph in (engine.CRUISE, engine.HOLD, engine.DESCENT):
        assert abs(mean[ph]["TACH1"] - engine.POWER[ph]["TACH1"]) < 15
        assert abs(mean[ph]["FUELF1"] - engine.POWER[ph]["FUELF1"]) < 0.2


def test_fuel_burns_down_by_the_fuel_flow(modelled):
    rows, _, phases = modelled
    q = [(t, r["FUELQ1"] + r["FUELQ2"], r["FUELF1"]) for t, r in rows if "FUELQ1" in r]
    assert q[0][1] == pytest.approx(2 * engine.FUEL_START_GAL)
    assert all(b[1] <= a[1] for a, b in zip(q, q[1:]))
    burned = q[0][1] - q[-1][1]
    expect = sum(a[2] * (b[0] - a[0]) for a, b in zip(q, q[1:])) / 3600.0
    assert burned == pytest.approx(expect, rel=0.03)
    # a loop burns a little over a gallon a tank: never near empty
    assert 1.0 < burned < 4.0


def test_one_cylinder_crosses_warn_then_alarm_in_the_climb_and_recovers(modelled):
    rows, eng, phases = modelled
    warn, alarm = WARN_ALARM
    cht = _series(rows, phases, HOT)
    t_warn = next(t for t, _, v in cht if v > warn)
    t_alarm = next(t for t, _, v in cht if v > alarm)
    t_back = next(t for t, _, v in cht if t > t_alarm and v < warn)
    assert t_warn < t_alarm < t_back
    in_climb = {t: ph for t, ph, _ in cht}
    assert in_climb[t_warn] in (engine.TAKEOFF, engine.CLIMB)
    assert in_climb[t_back] in (engine.TAKEOFF, engine.CLIMB)
    # far enough past the alarm that the gauge is plainly red, not absurd
    peak = max(v for _, _, v in cht)
    assert alarm + 1.0 < peak < alarm + engine.EVENT_OVER_ALARM
    # once, and never again in the loop
    assert all(v < warn for t, _, v in cht if t > t_back)
    assert [w for _, w in eng.marks] == ["hot", "enrich", "recovered"]
    # the pilot's enrichment shows: fuel flow up, EGTs down
    t_enrich = eng.marks[1][0]
    before = [r for t, r in rows if "FUELF1" in r and t_enrich - 5 < t < t_enrich]
    after = [r for t, r in rows if "FUELF1" in r and t_enrich + 15 < t < t_enrich + 20]
    assert after[-1]["FUELF1"] > before[-1]["FUELF1"] + 1.0
    assert after[-1]["EGT11"] < before[-1]["EGT11"] - 20.0
    # no other cylinder, and no other engine reading, goes near a limit
    for key in ("CHT11", "CHT12", "CHT14"):
        assert max(v for _, _, v in _series(rows, phases, key)) < warn


def test_no_limits_no_event(flown):
    rows, eng = engine.add_engine(flown.rows, None)
    assert eng.marks == []
    assert max(r[HOT] for _, r in rows if HOT in r) < WARN_ALARM[0]


@pytest.mark.parametrize("warn, alarm", [(None, 232.0), (204.0, None), (232.0, 204.0), ("x", 1)])
def test_event_limits_needs_a_warn_below_an_alarm(warn, alarm):
    assert engine.event_limits(warn, alarm) is None


def test_the_event_follows_the_configured_limits(flown):
    rows, _ = engine.add_engine(flown.rows, (180.0, 200.0))
    peak = max(r[HOT] for _, r in rows if HOT in r)
    assert 201.0 < peak < 210.0


# ---------------------------------------------------------------------------
# In the plugin, through the real annunciate
# ---------------------------------------------------------------------------

def _load_ini(path):
    # the server's initialization-file loader (server.main_setup)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and line[0] != "#":
                x = line.split("=")
                if len(x) >= 2:
                    database.write(x[0].strip(), x[1].strip())


def test_the_hot_cylinder_is_annunciated_by_the_annunciate_plugin(db, monkeypatch):
    from fixgw.plugins import annunciate

    _load_ini(DEFAULT_INI)
    with open(ANNUNCIATE_YAML) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    ann = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    ann.run()

    flags = []
    real = database.db_item.annunciate.fset

    def watch(self, x):
        if x != self.annunciate:
            flags.append((self.key, x, sys._getframe(1).f_globals["__name__"]))
        real(self, x)

    monkeypatch.setattr(database.db_item, "annunciate", database.db_item.annunciate.setter(watch))
    p = make()
    loops(p, 1)
    p.run()
    p.thread.join(60)
    ann.stop()
    assert p.thread.refused is None, p.status

    # every flag change came from the annunciate plugin, none from the demo
    assert {mod for _, _, mod in flags} == {"fixgw.plugins.annunciate"}
    # the demo wrote the engine
    written = {k for k, ws in database.writers().items() if "demo" in ws}
    assert set(engine.ENGINE_KEYS) <= written
    # the hot cylinder raised and cleared, once
    assert [(k, x) for k, x, _ in flags if k == HOT] == [(HOT, True), (HOT, False)]
    # no other engine reading is annunciated all loop.  (ALTSEL may be: the
    # missed climbs through its default 3000 ft on the way to 4000, and the
    # altitude alerter is real code too.)
    others = [(k, x) for k, x, _ in flags if k in engine.ENGINE_KEYS and k != HOT and x]
    assert others == [], others
    assert "hot" in p.status["CHT event"] and "recovered" in p.status["CHT event"]


def test_without_limits_it_says_so_once_and_flies_without_the_event(db, caplog):
    # the database's own CHT13 has no limits until init_data sets them
    p = make()
    loops(p, 1)
    p.run()
    p.thread.join(60)
    assert p.thread.refused is None
    assert p.status["CHT event"] == "none"
    warnings = [r.message for r in caplog.records if "no CHT event" in r.message]
    assert len(warnings) == 1


def test_a_second_writer_on_an_engine_key_stops_it(db):
    fast = FastClock(on_sleep=lambda n: as_plugin("canfix", "CHT12", 150.0) if n == 50 else None)
    p = make(fast=fast)
    p.start()
    p.thread.join(30)
    assert p.status["State"] == "stopped"
    assert "CHT12 (canfix)" in p.status["Reason"]


def test_refuses_at_start_beside_a_source_writing_the_engine(db):

    as_plugin("grand_rapids_eis", "TACH1", 2300)
    p = make()
    with pytest.raises(plugin_base.PluginFail, match="TACH1"):
        p.start()


def test_the_restart_hold_keeps_the_engine_gauges_fresh(db):
    p = make({"restart_s": 4}, fast=FastClock())
    held = []
    hold = p.thread._hold

    def watched(seconds):
        n = len(writes)
        out = hold(seconds)
        held.append(writes[n:])
        p.thread.halt.set()
        return out

    writes = []
    database.callback_add("t", "CHT11", lambda k, v, u: writes.append(v[0]), None)
    p.thread._hold = watched
    p.run()
    p.thread.join(60)
    # once a second through a 4 s hold, the last reading repeated
    assert len(held[0]) == 4
    assert len(set(held[0])) == 1
