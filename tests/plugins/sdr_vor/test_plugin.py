"""SDRVOR1 plugin: what it writes to its NAV slot, tuning and release (AER-6141)."""

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time

import numpy as np
import pytest

import fixgw.plugins.sdr_vor as sdr_vor
from fixgw.plugins.sdr_vor import dsp
from tests.plugins.sdr_vor import siggen

VOR_MHZ = 113.90
LOC_MHZ = 110.30


def capture(tmp_path, name, iq):
    path = tmp_path / name
    path.write_bytes(siggen.to_cu8(iq))
    return str(path)


MADE = []


@pytest.fixture(autouse=True)
def release_all():
    yield
    while MADE:
        MADE.pop().release()


def make(database, config=None, **kw):
    cfg = {"nav_slot": 2, "replay_realtime": False}
    cfg.update(config or {})
    cfg.update(kw)
    p = sdr_vor.Plugin("sdr_vor", cfg, None)
    MADE.append(p)
    return p


def read(database, key):
    return database.read(key)


def drive(p, decodes):
    """Run the plugin's loop body until it has decoded ``decodes`` more blocks."""
    target = p.status["Decodes"] + decodes
    for _ in range(200):
        p.step()
        if p.status["Decodes"] >= target:
            return
    raise AssertionError("no decodes")


def tune(database, mhz, slot=2):
    database.write(f"NAV{slot}FREQ", mhz)


# -- the slot's keys ------------------------------------------------------------


def test_good_vor_writes_the_slot(database, tmp_path):
    path = capture(tmp_path, "vor.cu8", siggen.vor(123.0, 30.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, VOR_MHZ)
    database.write("NAV2CRS", 303.0)  # inbound to the station on radial 123
    drive(p, 4)
    assert p.status["Valid"]
    assert read(database, "NAV2DH")[0] == 1.0
    brg = read(database, "VOR2BRG")
    assert abs(dsp.wrap180(brg[0] - 303.0)) < 2.0
    assert not brg[4]  # not failed
    assert read(database, "NAV2TF")[0] == 1.0  # TO
    assert read(database, "NAV2SUPP")[0] == 1.0  # supplemental (SDRVOR3)
    cdi = read(database, "NAV2CDI")
    assert abs(cdi[0]) < 0.2 and not cdi[4]
    assert read(database, "NAV2TYPE")[0] == 1.0  # VOR
    assert read(database, "NAV2GSF")[0] == 1.0  # never a glideslope


@pytest.mark.parametrize(
    "radial,tf", [(180.0, 1.0), (0.0, 2.0), (170.0, 1.0), (10.0, 2.0)]
)
def test_to_from_either_side_of_the_station(database, tmp_path, radial, tf):
    # OBS 360: south of the station (radial 180) is TO, north of it FROM.
    path = capture(tmp_path, "vor.cu8", siggen.vor(radial, 30.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, VOR_MHZ)
    database.write("NAV2CRS", 0.0)
    drive(p, 4)
    assert p.status["Valid"]
    assert read(database, "NAV2TF")[0] == tf
    # 10 deg off the course line on either side is a full-scale needle.
    expect = {180.0: 0.0, 0.0: 0.0, 170.0: -1.0, 10.0: -1.0}[radial]
    assert read(database, "NAV2CDI")[0] == pytest.approx(expect, abs=0.2)


def test_good_localizer_writes_the_slot(database, tmp_path):
    path = capture(tmp_path, "loc.cu8", siggen.localizer(0.08, 30.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, LOC_MHZ)
    drive(p, 2)
    assert p.status["Valid"]
    assert read(database, "NAV2DH")[0] == 1.0
    assert read(database, "NAV2CDI")[0] == pytest.approx(0.08 / 0.155, abs=0.05)
    assert read(database, "NAV2TYPE")[0] == 2.0  # LOC
    assert read(database, "NAV2TF")[0] == 0.0
    assert read(database, "VOR2BRG")[4]  # no bearing on a localizer
    assert read(database, "NAV2GSF")[0] == 1.0
    assert read(database, "NAV2SUPP")[0] == 1.0


def assert_flagged(database, kind):
    assert read(database, "NAV2DH")[0] == 0.0
    assert read(database, "NAV2CDI")[4]
    assert read(database, "VOR2BRG")[4]
    assert read(database, "NAV2TF")[0] == 0.0
    assert read(database, "NAV2TYPE")[0] == kind
    # Supplemental in every state, tuned or not: the mark is about the
    # receiver, not the signal (SDRVOR3).
    supp = read(database, "NAV2SUPP")
    assert supp[0] == 1.0 and not any(supp[2:5])


@pytest.mark.parametrize("mhz,kind", [(VOR_MHZ, 1.0), (LOC_MHZ, 2.0)])
def test_pure_noise_is_flagged(database, tmp_path, mhz, kind):
    p = make(database, replay=capture(tmp_path, "n.cu8", siggen.noise(seconds=3.0)))
    tune(database, mhz)
    drive(p, 4)
    assert not p.status["Valid"]
    assert "cn" in p.status["Failed checks"]
    assert_flagged(database, kind)


def test_below_the_threshold_is_flagged(database, tmp_path):
    path = capture(tmp_path, "weak.cu8", siggen.vor(45.0, 10.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, VOR_MHZ)
    drive(p, 4)
    assert not p.status["Valid"]
    assert_flagged(database, 1.0)


def test_signal_lost_mid_stream_flags_at_once(database, tmp_path):
    iq = np.concatenate([siggen.vor(90.0, 30.0, seconds=3.0), siggen.noise(seconds=1.0, seed=3)])
    p = make(database, replay=capture(tmp_path, "lost.cu8", iq), replay_loop=False)
    tune(database, VOR_MHZ)
    drive(p, 4)
    assert p.status["Valid"]
    drive(p, 2)  # the second block is all noise
    assert not p.status["Valid"]
    assert_flagged(database, 1.0)


def test_the_first_blocks_wait_for_a_stable_bearing(database, tmp_path):
    path = capture(tmp_path, "vor.cu8", siggen.vor(200.0, 30.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, VOR_MHZ)
    drive(p, 1)
    assert not p.status["Valid"] and p.status["Failed checks"] == ["stable"]
    assert read(database, "NAV2DH")[0] == 0.0
    drive(p, 2)
    assert p.status["Valid"]


def test_failed_course_keeps_the_bearing_but_drops_the_needle(database, tmp_path):
    path = capture(tmp_path, "vor.cu8", siggen.vor(123.0, 30.0, seconds=3.0))
    p = make(database, replay=path)
    tune(database, VOR_MHZ)
    database.get_raw_item("NAV2CRS").set(303.0, fail=True)
    drive(p, 4)
    assert read(database, "NAV2DH")[0] == 0.0
    assert read(database, "NAV2CDI")[4]
    assert not read(database, "VOR2BRG")[4]


def test_slot_1(database, tmp_path):
    path = capture(tmp_path, "vor.cu8", siggen.vor(10.0, 30.0, seconds=3.0))
    p = make(database, replay=path, nav_slot=1)
    tune(database, VOR_MHZ, slot=1)
    database.write("NAV1CRS", 190.0)
    drive(p, 4)
    assert read(database, "NAV1DH")[0] == 1.0
    assert abs(dsp.wrap180(read(database, "VOR1BRG")[0] - 190.0)) < 2.0
    assert read(database, "NAV1TYPE")[0] == 1.0
    assert read(database, "NAV1SUPP")[0] == 1.0
    assert read(database, "NAV2DH")[0] == 0.0  # untouched: its initial value
    assert not database.get_raw_item("NAV2DH").written
    assert read(database, "NAV2SUPP")[0] == 0.0  # the other slot is not claimed
    assert not database.get_raw_item("NAV2SUPP").written


@pytest.mark.parametrize(
    "cfg", [{"nav_slot": 3}, {"nav_slot": 0}, {"sample_rate": 500000}, {"sample_rate": 4000000}]
)
def test_bad_config_refused(database, cfg):
    with pytest.raises(ValueError):
        make(database, cfg)


def test_supplemental_notice_on_status_and_log(database, tmp_path, caplog):
    p = make(database, replay=capture(tmp_path, "n.cu8", siggen.noise(seconds=1.0)))
    assert "supplemental, not a certified nav receiver" in p.get_status()["Notice"]
    assert "day VFR" in p.get_status()["Notice"]
    with caplog.at_level(logging.INFO):
        p.run()
        p.stop()
    assert any("not a certified nav receiver" in r.getMessage() for r in caplog.records)


# -- tuning and the dongle ------------------------------------------------------

FAKE_RTL_SDR = """#!{python}
import sys, time
with open({log!r}, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
data = open({iq!r}, "rb").read()
out = sys.stdout.buffer
while True:
    out.write(data)
    out.flush()
    time.sleep(0.05)
"""


@pytest.fixture
def fake_rtl_sdr(tmp_path):
    iq = capture(tmp_path, "radio.cu8", siggen.vor(77.0, 30.0, seconds=1.0))
    log = tmp_path / "argv.log"
    script = tmp_path / "rtl_sdr"
    script.write_text(FAKE_RTL_SDR.format(python=sys.executable, log=str(log), iq=iq))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def test_no_frequency_means_no_radio(database, fake_rtl_sdr):
    script, log = fake_rtl_sdr
    p = make(database, rtl_sdr=script)
    for _ in range(3):
        p.step()
    assert p.source is None
    assert not log.exists()
    assert_flagged(database, 0.0)


@pytest.mark.parametrize("mhz", [0.0, 108.03, 107.0])
def test_not_a_nav_channel_means_no_radio(database, fake_rtl_sdr, mhz):
    script, log = fake_rtl_sdr
    p = make(database, rtl_sdr=script)
    tune(database, mhz)
    p.step()
    assert p.source is None and not log.exists()


def test_tunes_rtl_sdr_below_the_station_and_releases_it(database, fake_rtl_sdr):
    script, log = fake_rtl_sdr
    p = make(database, rtl_sdr=script, rtl_device=1, gain=28, ppm=3)
    tune(database, VOR_MHZ)
    drive(p, 3)
    assert p.status["Valid"]
    proc = p.source.process
    argv = log.read_text().split()
    assert argv[argv.index("-f") + 1] == str(int(VOR_MHZ * 1e6 - 60000))
    assert argv[argv.index("-s") + 1] == "240000"
    assert argv[argv.index("-d") + 1] == "1"
    assert argv[argv.index("-g") + 1] == "28"
    assert argv[argv.index("-p") + 1] == "3"
    assert argv[-1] == "-"
    # Retune: a new process on the new frequency, the old one gone.
    tune(database, 112.0)
    p.step()
    assert proc.poll() is not None
    assert p.source.process is not proc
    second = p.source.process
    assert log.read_text().splitlines()[-1].split()[3] == str(112000000 - 60000)
    # No frequency: the dongle is released and the slot flagged.
    tune(database, 0.0)
    p.step()
    assert second.poll() is not None
    assert p.source is None and p.status["rtl_sdr pid"] is None
    assert_flagged(database, 0.0)


def test_missing_rtl_sdr_is_flagged_not_fatal(database, tmp_path):
    p = make(database, rtl_sdr=str(tmp_path / "no_such_rtl_sdr"))
    tune(database, VOR_MHZ)
    p.step()
    assert p.source is None
    assert_flagged(database, 1.0)


def test_thread_runs_and_stops_cleanly(database, fake_rtl_sdr):
    script, _ = fake_rtl_sdr
    p = make(database, rtl_sdr=script)
    tune(database, VOR_MHZ)
    p.run()
    deadline = time.time() + 30
    while time.time() < deadline and not p.status["Valid"]:
        time.sleep(0.1)
    assert p.status["Valid"]
    proc = p.source.process
    p.stop()
    assert proc.poll() is not None
    assert read(database, "NAV2DH")[0] == 0.0
    assert read(database, "NAV2TYPE")[0] == 0.0


# -- through the real gateway, with compute ------------------------------------

DRIVER = """
import json, sys, threading, time
import fixgw.server as server, fixgw.plugin as plugin, fixgw.database as database
sys.argv = ["fixGw.py", "--config-file", sys.argv[1]]
args = server.main_setup()
def drive():
    time.sleep(3)
    database.write("NAV2CRS", 303.0)
    database.write("NAVSRC", 1)
    database.write("NAV2FREQ", 113.9)
    time.sleep(6)
    out = {k: list(database.read(k)) for k in
           ["NAV2DH", "NAV2CDIV", "CDIV", "VOR2BRG", "NAV2TF", "TOFROM",
            "NAV2TYPE", "NAVTYPE", "NAV2GSV", "GSV", "NAV1SUPP", "NAV2SUPP",
            "NAVSUPP"]}
    print("STATE " + json.dumps(out), flush=True)
    # NAVSUPP follows NAVSRC (SDRVOR3): NAV1, GPS, back to NAV2.
    sweep = []
    for src in (0, 2, 1):
        database.write("NAVSRC", src)
        time.sleep(0.3)
        sweep.append([src, list(database.read("NAVSUPP"))])
    print("SWEEP " + json.dumps(sweep), flush=True)
    print("WRITERS " + json.dumps(database.writers()), flush=True)
    plugin.jobQueue.put("QUIT")
threading.Thread(target=drive, daemon=True).start()
server.run(args)
"""


def run_gateway(tmp_path, iq, sdr=True):
    config = tmp_path / "config"
    shutil.copytree("src/fixgw/config", config)
    replay = capture(tmp_path, "replay.cu8", iq)
    (config / "preferences.yaml.custom").write_text(
        f"enabled:\n  SDR_VOR: {'true' if sdr else 'false'}\n  DEMO: false\n"
    )
    conn = config / "connections" / "sdr_vor.yaml"
    conn.write_text(conn.read_text() + f"  replay: {replay}\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.abspath("src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    r = subprocess.run(
        [sys.executable, "-c", DRIVER, str(config / "default.yaml")],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    lines = {
        line.split(" ", 1)[0]: json.loads(line.split(" ", 1)[1])
        for line in r.stdout.splitlines()
        if line.startswith(("STATE ", "WRITERS ", "SWEEP "))
    }
    assert "SWEEP" in lines, r.stdout + r.stderr
    return lines["STATE"], lines["WRITERS"], lines["SWEEP"]


def test_gateway_valid_vor_reaches_the_hsi_keys(tmp_path):
    state, writers, _ = run_gateway(tmp_path, siggen.vor(123.0, 30.0, seconds=4.0))
    assert state["NAV2DH"][0] == 1.0
    assert state["NAV2CDIV"][0] == 1.0 and state["CDIV"][0] == 1.0
    assert abs(dsp.wrap180(state["VOR2BRG"][0] - 303.0)) < 2.0
    assert state["TOFROM"][0] == 1.0 and state["NAVTYPE"][0] == 1.0
    assert state["GSV"][0] == 0.0  # no glideslope
    for key in ["NAV2DH", "NAV2CDI", "NAV2TF", "NAV2TYPE", "NAV2GSF", "VOR2BRG"]:
        assert writers[key] == ["sdr_vor"], (key, writers.get(key))
    assert writers["NAV2CDIV"] == ["compute"]
    assert {k: v for k, v in writers.items() if len(v) > 1} == {}


def test_gateway_noise_leaves_cdiv_false(tmp_path):
    state, _, _ = run_gateway(tmp_path, siggen.noise(seconds=4.0))
    assert state["NAV2DH"][0] == 0.0
    assert state["NAV2CDIV"][0] == 0.0 and state["CDIV"][0] == 0.0
    assert state["VOR2BRG"][4]  # failed
    assert state["TOFROM"][0] == 0.0


def fresh(item, value):
    return item[0] == value and not any(item[2:5])


def test_gateway_navsupp_follows_navsrc_with_one_writer(tmp_path):
    # SDRVOR3 (AER-6245): sdr_vor on NAV2 marks NAV2 supplemental; NAVSUPP
    # carries it only while NAV2 is selected. NAV1, which nothing claims,
    # reads 0 and not failed, after NAV2's 1 was showing.
    state, writers, sweep = run_gateway(tmp_path, siggen.vor(123.0, 30.0, seconds=4.0))
    assert fresh(state["NAV2SUPP"], 1.0)
    assert fresh(state["NAVSUPP"], 1.0)  # NAVSRC = 1 (NAV2) in the driver
    assert fresh(state["NAV1SUPP"], 0.0)
    assert [src for src, _ in sweep] == [0, 2, 1]
    assert [fresh(v, want) for (_, v), want in zip(sweep, (0.0, 0.0, 1.0))] == [True] * 3
    assert writers["NAV2SUPP"] == ["sdr_vor"]
    assert writers["NAVSUPP"] == ["compute"]
    assert "NAV1SUPP" not in writers


def test_gateway_without_sdr_vor_navsupp_stays_0(tmp_path):
    # Every other source: with sdr_vor off, nothing writes NAV1SUPP/NAV2SUPP,
    # and NAVSUPP reads 0, not failed, on every NAVSRC.
    state, writers, sweep = run_gateway(tmp_path, siggen.noise(seconds=1.0), sdr=False)
    for key in ("NAV1SUPP", "NAV2SUPP", "NAVSUPP"):
        assert fresh(state[key], 0.0), (key, state[key])
    assert all(fresh(v, 0.0) for _, v in sweep), sweep
    assert "NAV1SUPP" not in writers and "NAV2SUPP" not in writers


def test_only_sdr_vor_names_a_supplemental_slot_key():
    # Every other source leaves NAV1SUPP/NAV2SUPP alone: no plugin or
    # connection config but sdr_vor's names them. The database definition and
    # compute's select are the only other mentions.
    import pathlib
    import re

    pattern = re.compile(r"NAV\S{0,6}SUPP|\"supp\"")
    allowed = {
        "src/fixgw/plugins/sdr_vor/__init__.py",
        "src/fixgw/config/connections/sdr_vor.yaml",
        "src/fixgw/plugins/compute.py",
        "src/fixgw/config/connections/compute.yaml",
        "src/fixgw/config/database/ahrs.yaml",
    }
    found = set()
    for path in pathlib.Path("src/fixgw").rglob("*"):
        if path.suffix in (".py", ".yaml", ".yml", ".ini", ".json") and path.is_file():
            if pattern.search(path.read_text(errors="replace")):
                found.add(path.as_posix())
    assert found <= allowed, sorted(found - allowed)
    assert "src/fixgw/plugins/sdr_vor/__init__.py" in found
