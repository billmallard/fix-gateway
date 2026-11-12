# DEMO2 (AER-5461): the demo is a source built on fixgw.sim.  These tests fly
# the shipped profile on the real database, with the stream's clock
# fast-forwarded, and check what it writes, what it refuses, and when it stops.

import re
import sys
import threading

import pytest

import fixgw.database as database
import fixgw.plugin as plugin_base
import fixgw.plugins.demo as demo

pytest.importorskip("geographiclib")

from fixgw.sim import generator  # noqa: E402

PROFILE = demo.Plugin._profile_path(demo.DEFAULT_PROFILE)


@pytest.fixture(scope="module")
def flown():
    return generator.generate(PROFILE)


@pytest.fixture
def db(flown, monkeypatch):
    database.init("src/fixgw/config/database.yaml")
    database._stop_update_thread()
    monkeypatch.setattr(generator, "generate", lambda *a, **k: flown)
    yield
    database._stop_update_thread()


def as_plugin(name, key, value):
    """Write ``key`` from inside a plugin module, so the registry names it."""
    exec("database.write(key, value)",
         {"__name__": "fixgw.plugins." + name, "database": database, "key": key, "value": value})


class FastClock:
    """live.stream's clock and sleep: a sleep advances the clock at once.
    ``on_sleep(n)`` runs at the n-th sleep."""

    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.n = 0
        self.on_sleep = on_sleep

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.n += 1
        if self.on_sleep is not None:
            self.on_sleep(self.n)


def make(config=None, fast=None):
    cfg = {"watch_s": 0, "restart_s": 0}
    cfg.update(config or {})
    p = demo.Plugin("demo", cfg, {})
    fast = fast or FastClock()
    p.thread.clock, p.thread.sleep = fast.clock, fast.sleep
    return p


def loops(p, n):
    """Stop the demo once it has cleared the plan ``n`` times."""
    clear = p.thread._clear_plan
    count = []

    def counted():
        clear()
        count.append(1)
        if len(count) >= n:
            p.thread.halt.set()

    p.thread._clear_plan = counted
    return count


def demo_keys():
    return {k for k, ws in database.writers().items() if "demo" in ws}


# ---------------------------------------------------------------------------
# What it writes
# ---------------------------------------------------------------------------

def test_source_keys_are_the_generators_sensor_set():
    assert demo.SOURCE_KEYS == generator.SENSOR_KEYS


def _editor_keys():
    """The route and command keys doc/flightplan_keys.md gives to the editor,
    as regexes.  `FPL1..100 LAT` / `LON` is two keys on one slot prefix."""
    pats = []
    with open("doc/flightplan_keys.md") as f:
        lines = f.read().splitlines()
    writer_col = None
    for line in lines:
        if not line.startswith("|"):
            writer_col = None
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if "Writer" in cells:
            writer_col = cells.index("Writer")
            continue
        if writer_col is None or set(cells[0]) <= set("-"):
            continue
        if not cells[writer_col].startswith("editor"):
            continue
        names = re.findall(r"`([^`]+)`", cells[0])
        prefix = ""
        for name in names:
            m = re.match(r"(FPLS?)1\.\.100 (\w+)$", name)
            if m:
                prefix = m.group(1)
                pats.append(prefix + r"\d+" + m.group(2))
            elif prefix:
                pats.append(prefix + r"\d+" + name)
            else:
                pats.append(re.escape(name))
    return [re.compile(p + "$") for p in pats]


def _compute_outputs():
    import yaml

    with open("src/fixgw/config/connections/compute.yaml") as f:
        functions = yaml.safe_load(f)["compute"]["functions"]
    return {f["output"] for f in functions if "output" in f}


def test_demo_writes_only_source_keys_and_the_editors_route_keys(db, monkeypatch):
    annunciated = []
    real = database.db_item.annunciate.fset

    def watch(self, x):
        annunciated.append(self.key)
        real(self, x)

    monkeypatch.setattr(database.db_item, "annunciate", database.db_item.annunciate.setter(watch))
    p = make()
    loops(p, 2)
    p.run()
    p.thread.join(60)
    assert not p.thread.is_alive()
    assert p.thread.refused is None, p.status

    written = demo_keys()
    editor = _editor_keys()
    route = {k for k in written if any(rx.match(k) for rx in editor)}
    tfc = {k for k in written if k.startswith("TFC")}
    # every key it writes is a sensor's, the editor's, or the traffic block
    # (DEMO7) through the stratux decoder
    assert written - route - tfc == set(demo.SOURCE_KEYS), sorted(written - route - tfc)
    assert {"TFC1TGT", "TFCCOUNT"} <= tfc
    assert all(database.get_raw_item(k).writers == ["demo"] for k in tfc)
    assert {"FPLSEQ", "FPLCMD", "FPLCOUNT", "FPLSEGCNT", "FPL1ID", "FPLS1KIND"} <= route
    # never a reference key, a compute output, or an annunciate flag
    assert not {k for k in written if k.startswith("REF")}
    outputs = _compute_outputs()
    assert {"COURSE", "CDI", "GSI"} <= outputs
    assert written & outputs == set()
    assert annunciated == []
    # the engine's outputs are not the editor's: the contract check above has teeth
    assert not any(rx.match(k) for rx in editor for k in ("FPLCRS", "FPLXTK", "FPLCDI", "WPNAME", "FPLCMDACK"))


def test_the_route_is_committed_again_each_loop(db):
    seqs = []
    database.callback_add("t", "FPLSEQ", lambda k, v, u: seqs.append((v[0], database.read("FPLCOUNT")[0])), None)
    p = make()
    loops(p, 2)
    p.run()
    p.thread.join(60)
    # commit, clear, commit, clear: each one past the last, the clears empty
    assert [s for s, _ in seqs] == [1, 2, 3, 4]
    assert [c > 0 for _, c in seqs] == [True, False, True, False]
    assert p.status["Loops"] == 1


def test_the_restart_holds_position_bad_and_the_next_loop_clears_it(db):
    p = make({"restart_s": 0})
    seen = []

    def after_clear():
        demo.MainThread._clear_plan(p.thread)
        seen.append(database.read("LAT")[3])
        if len(seen) == 2:
            p.thread.halt.set()

    p.thread._clear_plan = after_clear
    bad_mid_loop = []
    database.callback_add("t", "LAT", lambda k, v, u: bad_mid_loop.append(v[3]), None)
    p.run()
    p.thread.join(60)
    assert seen == [True, True]
    # the second loop's first position write cleared it
    first_of_loop2 = bad_mid_loop.index(True) + 1
    assert bad_mid_loop[first_of_loop2] is False


def test_renumber_follows_the_gateways_seq_and_acks():
    rows = [(0.0, {"FPLSEQ": 1, "LAT": 1.0}), (0.2, {"FPLCMD": "1 ACT 3"}), (9.0, {"FPLCMD": "2 RESUME"})]
    out = demo.renumber(rows, 17, 40)
    assert out == [(0.0, {"FPLSEQ": 17, "LAT": 1.0}), (0.2, {"FPLCMD": "41 ACT 3"}),
                   (9.0, {"FPLCMD": "42 RESUME"})]
    assert rows[0][1]["FPLSEQ"] == 1


# ---------------------------------------------------------------------------
# Refusing company
# ---------------------------------------------------------------------------

def test_refuses_at_start_when_another_plugin_writes_a_source_key(db):
    as_plugin("stratux", "ROLL", 3.0)
    p = make()
    with pytest.raises(plugin_base.PluginFail, match="ROLL"):
        p.start()
    assert p.status["State"] == "refused"
    assert "stratux" in p.status["Reason"]
    assert demo_keys() == set()


def test_refuses_after_watching_when_a_source_starts_writing(db, flown, monkeypatch):
    # xplane comes up after demo's start check and writes during the watch
    def gen(*a, **k):
        as_plugin("xplane", "PITCH", 2.0)
        return flown

    monkeypatch.setattr(generator, "generate", gen)
    p = make()
    p.start()
    p.thread.join(10)
    assert p.status["State"] == "refused"
    assert "PITCH (xplane)" in p.status["Reason"]
    assert demo_keys() == set()


def test_refuses_when_something_unattributed_moves_lat_long(db, flown, monkeypatch):
    # a netfix client is attributed to netfix; a write from outside any
    # plugin is not, and the live check is what sees it
    def gen(*a, **k):
        # from its own thread: on the demo's stack the write would be demo's
        t = threading.Thread(target=database.write, args=("LAT", 34.9))
        t.start()
        t.join()
        return flown

    monkeypatch.setattr(generator, "generate", gen)
    p = make()
    p.start()
    p.thread.join(10)
    assert p.status["State"] == "refused"
    assert "moving LAT" in p.status["Reason"]
    assert demo_keys() == set()


def test_stops_on_a_second_writer_while_running(db):
    fast = FastClock(on_sleep=lambda n: as_plugin("gpsd", "LONG", -120.0) if n == 50 else None)
    p = make(fast=fast)
    p.start()
    p.thread.join(30)
    assert not p.thread.is_alive()
    assert p.status["State"] == "stopped"
    assert "LONG (gpsd)" in p.status["Reason"]
    assert not p.is_running()
    # it stopped at once: the row after the intruding write was not written
    assert fast.n == 50


def test_a_route_key_written_by_the_engine_does_not_stop_it(db):
    # flightplan republishes the route on restore: a shared contract
    fast = FastClock(on_sleep=lambda n: as_plugin("flightplan", "FPLNAME", "X") if n == 50 else None)
    p = make(fast=fast)
    loops(p, 1)
    p.start()
    p.thread.join(60)
    assert p.thread.refused is None
    assert p.status["State"] == "restarting"


def test_refuses_a_speed_other_than_1(db):
    p = make({"speed": 4})
    with pytest.raises(plugin_base.PluginFail, match="real time"):
        p.start()


def test_refuses_without_the_sim_tier(db, monkeypatch):
    monkeypatch.setitem(sys.modules, "fixgw.sim.generator", None)
    p = make()
    with pytest.raises(plugin_base.PluginFail, match=r"fixgw\[sim\]"):
        p.start()


def test_stop_ends_the_thread(db):
    fast = FastClock()
    p = make(fast=fast)
    fast.on_sleep = lambda n: p.thread.halt.set() if n == 10 else None
    p.start()
    p.thread.join(10)
    p.shutdown()
    assert not p.thread.is_alive()
    assert p.thread.refused is None


# ---------------------------------------------------------------------------
# Traffic (DEMO7) in the rewritten loop
# ---------------------------------------------------------------------------

def test_traffic_orbits_the_hold_fix_at_hold_altitude(db):
    p = make()
    loops(p, 2)
    p.run()
    p.thread.join(60)
    assert p.thread.traffic.center == demo.hold_center(demo.route_rows(generator.generate(PROFILE),
                                                                       set(database.listkeys()))[0])
    lat, lon, alt = p.thread.traffic.center
    assert (round(lat, 3), round(lon, 3), alt) == (35.073, -120.663, 4000.0)   # WINCH, +4000
    assert database.read("TFCCOUNT")[0] == 3


def test_refuses_beside_stratux_writing_traffic(db):
    as_plugin("stratux", "TFC1TGT", 5)
    p = make()
    with pytest.raises(plugin_base.PluginFail, match="TFC1TGT"):
        p.start()


def test_with_traffic_off_the_tfc_block_is_not_its_own(db):
    as_plugin("stratux", "TFC1TGT", 5)
    p = make({"traffic": False})
    loops(p, 1)
    p.start()
    p.thread.join(60)
    assert p.thread.refused is None
    assert p.thread.traffic is None
    assert not {k for k in demo_keys() if k.startswith("TFC")}


def test_without_a_tfc_block_it_says_so_once_and_flies_without_traffic(db, monkeypatch, caplog):
    real = database.listkeys
    monkeypatch.setattr(database, "listkeys", lambda: [k for k in real() if not k.startswith("TFC")])
    p = make()
    loops(p, 1)
    p.start()
    p.thread.join(60)
    assert p.thread.refused is None
    assert p.thread.traffic is None
    errors = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "TFC block" in errors[0]
