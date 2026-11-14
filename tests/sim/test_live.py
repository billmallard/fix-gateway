"""Live mode: a profile flown into a running gateway over netfix (SYN2)."""

import json
import threading
import time
from pathlib import Path

import pytest

import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.netfix
from fixgw import cfg
from fixgw.netfix import Client
from fixgw.sim import generator, live

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "src" / "fixgw" / "sim" / "profiles" / "fp8-ksmx-rnav30-missed.yaml"
PORT = 34932


@pytest.fixture(scope="module")
def run():
    return generator.generate(str(PROFILE))


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        assert s > 0
        self.now += s
        self.slept += s


def test_stream_keeps_the_profile_schedule_at_a_multiple(run):
    """Row t goes out t/speed after the first, rows in order, keys in the
    generator's write order (LAT/LONG last of the sensors), and each mark
    after the row of its tick."""
    clock = FakeClock()
    out = []
    marks = []
    st = live.stream(run, lambda k, v: out.append((clock.now, k, v)), speed=4.0,
                     on_mark=lambda e, w: marks.append((e, w, len(out))), clock=clock, sleep=clock.sleep)
    end_t = run.rows[-1][0]
    assert st.rows == len(run.rows) and not st.stopped
    assert st.writes == sum(len(r) for _, r in run.rows) == len(out)
    assert clock.slept == pytest.approx(end_t / 4.0) and st.wall_s == pytest.approx(end_t / 4.0)
    assert st.late_max_s == 0.0
    assert [k for when, k, _ in out if when == 100.0][-1] == "FPLSEQ"     # the route block, committed last, at t=0
    assert [e for e, _, _ in marks] == sorted(run.timeline, key=lambda e: e["t"])
    seq = next((e, w, n) for e, w, n in marks if e["what"] == "sequence")
    assert seq[1] == pytest.approx(seq[0]["t"] / 4.0)
    written_by_then = out[:seq[2]]
    assert written_by_then[-1][0] == pytest.approx(100.0 + seq[0]["t"] / 4.0)


def test_a_slow_write_is_caught_up_not_accumulated(run):
    clock = FakeClock()
    short = generator.Run(run.profile, run.frequency_ms, run.rows[:50])

    def write(k, v):
        if k == "FPLSEQ":
            clock.now += 1.0          # the route block costs a second
    st = live.stream(short, write, clock=clock, sleep=clock.sleep)
    assert st.late_max_s == pytest.approx(1.0 - 0.2, abs=1e-9)    # row 2 was due at 0.2 s
    assert st.wall_s == pytest.approx(short.rows[-1][0])          # back on schedule by the end


def test_stop_ends_the_run_between_rows(run):
    clock = FakeClock()
    n = []
    st = live.stream(run, lambda k, v: None, clock=clock, sleep=clock.sleep,
                     stop=lambda: n.append(1) or len(n) > 10)
    assert st.stopped and st.rows == 10 and st.run_s == pytest.approx(run.rows[9][0])


def test_speed_must_be_positive(run):
    with pytest.raises(live.LiveError):
        live.stream(run, lambda k, v: None, speed=0)


NETFIX_CONFIG = f"""
type: server
host: 127.0.0.1
port: {PORT}
buffer_size: 1024
timeout: 1.0
"""


@pytest.fixture
def gateway(database, tmp_path):
    """A netfix server and the flight-plan engine on the real database, and
    a netfix client connected to it: the bench, minus pyEfis."""
    nc, nc_meta = cfg.from_yaml(NETFIX_CONFIG, metadata=True)
    nf = fixgw.plugins.netfix.Plugin("netfix", nc, nc_meta)
    nf.start()
    fp = flightplan.Plugin(
        "flightplan",
        {"CONFIGPATH": str(tmp_path), "state_file": str(tmp_path / "s.json"), "restore_delay": 0.0},
        {},
    )
    time.sleep(0.1)
    client = Client("127.0.0.1", PORT)
    assert client.connect()
    yield client, fp
    client.disconnect()
    nf.stop()


def _wait(cond, timeout=5.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.02)
    return cond()


def test_live_run_commits_a_new_route_and_the_engine_follows(gateway, database, tmp_path):
    """A gateway that has seen an earlier route and earlier commands: the run
    commits FPLSEQ one past it, numbers its ACT after the last ack, and the
    real engine loads the route, accepts the ACT and navigates the streamed
    position. The hook sees each mark with the readback already taken."""
    client, fp = gateway
    database.write("FPLSEQ", 7)
    database.write("FPLCMDACK", -12)
    assert live.other_writer(client, watch_s=0.2) == []
    fplseq, base = live.start_state(client)
    assert (fplseq, base) == (8, 12)

    r = generator.generate(str(PROFILE), fplseq=fplseq, cmd_seq_base=base)
    assert live.missing_keys(client, r) == []
    # the bench Pi's database has no sim.yaml: its REF* writes are named up front
    short_db = generator.Run(r.profile, r.frequency_ms, [(0.0, {"LAT": 1.0, "REFNOPE": 2.0})])
    assert live.missing_keys(client, short_db) == ["REFNOPE"]
    assert r.rows[1][1]["FPLCMD"] == "13 ACT 4"
    short = generator.Run(r.profile, r.frequency_ms, r.rows[:40],
                          [{"t": 0.0, "what": "start"}] + [e for e in r.timeline if e["t"] <= r.rows[39][0]])
    log = tmp_path / "marks.jsonl"
    env_out = tmp_path / "env.txt"
    marks = live.Marks(client, "fp8", log=str(log), hook=f'echo "$SYN_INDEX $SYN_WHAT $SYN_FIX" >> {env_out}',
                       hook_on=["start"], delay_s=0.3, readback=["FPLSEQ", "LAT", "NOSUCHKEY"])
    st = live.stream(short, client.write, speed=20.0, on_mark=marks)
    marks.close()
    assert st.rows == 40 and not st.stopped

    last = short.rows[-1][1]
    assert _wait(lambda: database.read("LAT")[0] == last["LAT"])
    assert fp.thread.engine.seq == 8, f"route not loaded; FPLMSG={database.read('FPLMSG')[0]!r}"
    assert [w.id for w in fp.thread.engine.route][:4] == ["GVO", "NIRLE", "ELZUS", "PAMJI"]
    assert database.read("FPLCMDACK")[0] == 13, database.read("FPLMSG")[0]
    assert database.read("REFWPT")[0] == "PAMJI"
    assert _wait(lambda: abs(database.read("FPLCRS")[0] - last["REFCRS"]) < 0.5)

    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["what"] for x in lines] == ["start", "pilot"]
    assert lines[0]["readback"]["FPLSEQ"] == "8"
    assert lines[0]["readback"]["NOSUCHKEY"].startswith("<")      # a bad key is logged, not fatal
    assert lines[0]["hook"]["rc"] == 0 and "hook" not in lines[1]
    assert env_out.read_text() == "01 start \n"


def test_another_writer_moving_the_position_is_named(gateway, database):
    client, _ = gateway
    stop = threading.Event()

    def other():
        lat = 34.0
        while not stop.is_set():
            lat += 1e-4
            database.write("LAT", lat)
            time.sleep(0.05)

    t = threading.Thread(target=other, daemon=True)
    t.start()
    try:
        assert live.other_writer(client, watch_s=0.3) == ["LAT"]
    finally:
        stop.set()
        t.join()
