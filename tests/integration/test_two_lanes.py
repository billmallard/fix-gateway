# Two lanes, one dies, the other stays up (CERT13a, AER-6243; MAOS brief
# briefs/redundant_lanes.md, section 8).
#
# Two real gateways start through fixgw.server:main from the shipped two-lane
# configs, each with quorum and canfix loaded.  Their CAN-FIX bus is a set of
# ptys joined by a hub in this process: each gateway runs python-can's serial
# interface on its own pty, and the hub forwards every frame to the other
# lane.  That needs no vcan module and no new dependency, and it lets a test
# cut the link.
#
# The brief's gaps Q1 and Q3 came from reading the quorum and canfix code.
# These tests turn them into evidence.  Q1 is pinned as it is today: slice 4
# (brief L4) inverts test_partition_today_makes_two_leaders.

import os
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import tty

import pytest
import yaml

CONFIG = os.path.abspath("src/fixgw/config")
DEFAULT_MAP = os.path.join(CONFIG, "canfix", "map.yaml")
TWO_LANE_MAP = os.path.join(CONFIG, "canfix", "two_lane_map.yaml")

# The values each lane takes from its own source, so two_lane_map.yaml drops
# their inputs from map.yaml.
OWN_SOURCE_KEYS = {"PITCH", "ROLL", "IAS", "ALT", "TAS", "VS", "ALAT", "ROT", "AOA"}

# Brief section 8: lane 2 leads within 1.5 s of any lane-1 gateway death.
TAKEOVER_LIMIT = 1.5

# The quorum plugin holds LEADER true for its first 4 s, then votes every
# 0.3 s.  Generous, for a slow CI runner.
STARTUP_LIMIT = 30.0

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="needs Linux ptys"
)


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


class Hub:
    """A CAN bus for N lanes made of ptys.  Frames from one lane reach every
    other lane while `link` is true.  A lane that is not reading (SIGSTOP)
    misses frames, as it would on a real bus, instead of stalling the hub."""

    def __init__(self, lanes):
        self.masters = []
        self.slaves = []  # held open, or a lane that dies takes the pty with it
        self.paths = []
        for _ in range(lanes):
            m, s = os.openpty()
            tty.setraw(s)
            os.set_blocking(m, False)
            self.masters.append(m)
            self.slaves.append(s)
            self.paths.append(os.ttyname(s))
        self.link = True
        self.frames = []  # (time, from lane, arbitration id, data)
        self._buf = [bytearray() for _ in range(lanes)]
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop:
            ready, _, _ = select.select(self.masters, [], [], 0.1)
            for m in ready:
                lane = self.masters.index(m)
                try:
                    self._buf[lane] += os.read(m, 4096)
                except (BlockingIOError, OSError):
                    continue
                for f in self._split(lane):
                    arb = struct.unpack("<I", f[6:10])[0]
                    self.frames.append((time.monotonic(), lane, arb, bytes(f[10:-1])))
                    if self.link:
                        for other, om in enumerate(self.masters):
                            if other != lane:
                                self._write(om, f)

    def _split(self, lane):
        buf = self._buf[lane]
        out = []
        while True:
            start = buf.find(b"\xaa")
            if start < 0:
                buf.clear()
                return out
            del buf[:start]
            if len(buf) < 6:
                return out
            total = 11 + buf[5]
            if buf[5] > 8:
                del buf[:1]
                continue
            if len(buf) < total:
                return out
            if buf[total - 1] != 0xBB:
                del buf[:1]
                continue
            out.append(bytes(buf[:total]))
            del buf[:total]

    def _write(self, m, f):
        try:
            os.write(m, f)
        except (BlockingIOError, OSError):
            pass

    def close(self):
        self._stop = True
        self._thread.join(1)
        for fd in self.masters + self.slaves:
            os.close(fd)


class NetFix:
    def __init__(self, port):
        deadline = time.monotonic() + STARTUP_LIMIT
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.2)
        self.buf = b""

    def write(self, key, value):
        self.sock.sendall(f"{key};{value}\n".encode())

    def read(self, key):
        """(value, old, bad, fail) as strings and bools."""
        self.sock.sendall(f"@r{key}\n".encode())
        prefix = f"@r{key};".encode()
        while True:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("gateway closed the connection")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            if line.startswith(prefix):
                value, flags = line[len(prefix) :].decode().split(";")
                return value, flags[1] == "1", flags[2] == "1", flags[3] == "1"

    def close(self):
        self.sock.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


DRIVER = (
    "import sys; sys.argv = ['fixgw', '--config-file', sys.argv[1]]; "
    "import fixgw.server as server; server.main()"
)


class Lane:
    def __init__(self, n, tmp_path, hub, mapfile, extra_outputs=()):
        self.n = n
        cfg = load_yaml(os.path.join(CONFIG, f"two_lane_{n}.yaml"))
        d = tmp_path / f"lane{n}"
        d.mkdir()
        # The server reads preferences.yaml beside its config file.
        with open(os.path.join(CONFIG, "preferences.yaml")) as f:
            (d / "preferences.yaml").write_text(f.read())
        if extra_outputs:
            maps = load_yaml(mapfile)
            maps["outputs"] = list(maps["outputs"]) + list(extra_outputs)
            mapfile = str(d / "map.yaml")
            with open(mapfile, "w") as f:
                yaml.safe_dump(maps, f)
        cfg["database file"] = os.path.join(CONFIG, "database.yaml")
        cfg["initialization files"] = [
            os.path.join(CONFIG, p.replace("{CONFIG}/", ""))
            for p in cfg["initialization files"]
        ]
        conns = cfg["connections"]
        self.port = free_port()
        conns["netfix"]["host"] = "127.0.0.1"
        conns["netfix"]["port"] = self.port
        conns["canfix"]["interface"] = "serial"
        conns["canfix"]["channel"] = hub.paths[n - 1]
        conns["canfix"]["mapfile"] = mapfile
        self.canfix_node = conns["canfix"]["node"]
        path = d / "lane.yaml"
        with open(path, "w") as f:
            yaml.safe_dump(cfg, f)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.abspath("src")]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        self.log = open(d / "gateway.log", "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(path)],
            stdout=self.log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.fix = NetFix(self.port)

    def leader(self):
        return self.fix.read("LEADER")[0] == "True"

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGCONT)
            self.proc.kill()
        self.proc.wait(10)
        self.fix.close()
        self.log.close()


@pytest.fixture
def lanes(tmp_path):
    started = []
    hub = Hub(2)

    def start(mapfile=TWO_LANE_MAP, lane2_outputs=()):
        one = Lane(1, tmp_path, hub, mapfile)
        started.append(one)
        two = Lane(2, tmp_path, hub, mapfile, lane2_outputs)
        started.append(two)
        wait_for(
            lambda: not one.leader() and two.leader(),
            STARTUP_LIMIT,
            "lane 2 never led with lane 1 following: the lanes do not see each other",
        )
        return hub, one, two

    yield start
    for lane in started:
        lane.stop()
    hub.close()


def wait_for(predicate, limit, message):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


class Feeder:
    """The test source for a lane's PITCH: writes a new value at 10 Hz."""

    def __init__(self, lane):
        self.fix = NetFix(lane.port)
        self.value = 0.0
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop:
            self.value = round((self.value + 0.7) % 40.0, 1)
            self.fix.write("PITCH", self.value)
            time.sleep(0.1)

    def stop(self):
        self._stop = True
        self._thread.join(1)
        self.fix.close()


def watch_takeover(survivor, kill, after=3.0):
    """Kill the other lane with `kill()`, then sample the survivor at 20 Hz.
    Returns the seconds from the kill to LEADER true, and the PITCH samples
    from 1 s before the kill to `after` seconds past the takeover."""
    feeder = Feeder(survivor)
    try:
        wait_for(
            lambda: not survivor.fix.read("PITCH")[1],
            5,
            "the feeder never reached PITCH",
        )
        samples = []
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            samples.append(survivor.fix.read("PITCH"))
            time.sleep(0.05)
        t_kill = time.monotonic()
        kill()
        took = None
        end = t_kill + 10.0
        while time.monotonic() < end:
            samples.append(survivor.fix.read("PITCH"))
            if took is None and survivor.leader():
                took = time.monotonic() - t_kill
                end = time.monotonic() + after
            time.sleep(0.05)
        return took, samples
    finally:
        feeder.stop()


def assert_pitch_live(samples):
    stale = [s for s in samples if s[1] or s[2] or s[3]]
    assert not stale, f"{len(stale)} of {len(samples)} PITCH samples old, bad or failed"
    values = [s[0] for s in samples]
    changes = sum(1 for a, b in zip(values, values[1:]) if a != b)
    assert changes >= len(samples) // 4, f"PITCH stopped updating: {values}"


def test_two_lane_map_is_map_minus_own_source_inputs():
    default = load_yaml(DEFAULT_MAP)
    two = load_yaml(TWO_LANE_MAP)
    dropped = [i for i in default["inputs"] if i["fixid"] in OWN_SOURCE_KEYS]
    assert {i["fixid"] for i in dropped} == OWN_SOURCE_KEYS
    assert two["inputs"] == [i for i in default["inputs"] if i not in dropped]
    for section in ("outputs", "encoders", "switches", "meta replacements"):
        assert two[section] == default[section], section
    # No lane transmits a primary flight value under its parameter id.
    assert not {o["fixid"] for o in two["outputs"]} & OWN_SOURCE_KEYS


def test_two_lane_configs_differ_only_in_node_ids():
    one = load_yaml(os.path.join(CONFIG, "two_lane_1.yaml"))
    two = load_yaml(os.path.join(CONFIG, "two_lane_2.yaml"))
    assert one["connections"]["quorum"]["nodeid"] == 1
    assert two["connections"]["quorum"]["nodeid"] == 2
    assert one["connections"]["canfix"]["node"] != two["connections"]["canfix"]["node"]
    for cfg in (one, two):
        assert (
            cfg["connections"]["canfix"]["mapfile"]
            == "{CONFIG}/canfix/two_lane_map.yaml"
        )
        del cfg["connections"]["quorum"]["nodeid"]
        del cfg["connections"]["canfix"]["node"]
    assert one == two


def test_leader_moves_when_lane_dies(lanes):
    hub, one, two = lanes()
    took, samples = watch_takeover(one, two.proc.kill)
    print(f"lane 1 led {took} s after SIGKILL of lane 2")
    assert took is not None and took <= TAKEOVER_LIMIT, took
    assert_pitch_live(samples)


def test_frozen_lane_loses_lead(lanes):
    hub, one, two = lanes()
    took, samples = watch_takeover(one, lambda: two.proc.send_signal(signal.SIGSTOP))
    print(f"lane 1 led {took} s after SIGSTOP of lane 2")
    assert took is not None and took <= TAKEOVER_LIMIT, took
    assert_pitch_live(samples)
    assert two.proc.poll() is None, "lane 2 should be stopped, not dead"


def test_partition_today_makes_two_leaders(lanes):
    # Gap Q1, as it is today: with total_nodes 2, a lane that sees no vote
    # finds its own vote highest, so a cut bus gives two leaders.  Both would
    # write CAN-FIX outputs and command the autopilot.  Slice 4 inverts this.
    hub, one, two = lanes()
    hub.link = False
    t_cut = time.monotonic()
    wait_for(one.leader, 10, "lane 1 never led after the cut")
    print(f"lane 1 led {time.monotonic() - t_cut:.2f} s after the cut")
    end = time.monotonic() + 3.0
    while time.monotonic() < end:
        assert one.leader() and two.leader(), "a lane gave up the lead while cut"
        time.sleep(0.1)
    # The link was the only cause: rejoin it and lane 1 follows again.
    hub.link = True
    wait_for(lambda: not one.leader(), 10, "lane 1 kept the lead after the rejoin")
    assert two.leader()


@pytest.mark.parametrize(
    "mapfile",
    [
        TWO_LANE_MAP,
        pytest.param(
            DEFAULT_MAP,
            marks=pytest.mark.xfail(
                strict=True,
                reason="gap Q3: map.yaml takes PITCH from any node, so lane 2's "
                "attitude becomes lane 1's",
            ),
        ),
    ],
    ids=["two_lane_map", "default_map"],
)
def test_cross_lane_attitude_is_not_ingested(lanes, mapfile):
    # Lane 2 puts its own PITCH on the bus, as a lane would to let the other
    # compare.  It goes out through lane 2's real canfix output, as owner.
    pitch_out = {
        "fixid": "PITCH",
        "canid": 0x180,
        "index": 0,
        "owner": True,
        "require_leader": False,
    }
    hub, one, two = lanes(mapfile, lane2_outputs=[pitch_out])
    one.fix.write("PITCH", 5.0)
    seen = []
    end = time.monotonic() + 3.0
    i = 0
    while time.monotonic() < end:
        two.fix.write("PITCH", 30.0 + (i % 10) * 0.5)
        i += 1
        seen.append(float(one.fix.read("PITCH")[0]))
        time.sleep(0.1)
    sent = [
        f
        for f in hub.frames
        if f[1] == 1 and f[2] == 0x180 and f[3][0] == two.canfix_node
    ]
    assert (
        len(sent) >= 10
    ), "lane 2 never put its PITCH on the bus; the test proves nothing"
    assert not one.leader(), "lane 1 lost lane 2's votes: the bus was not delivering"
    assert set(seen) == {
        5.0
    }, f"lane 1's PITCH took lane 2's values: {sorted(set(seen))}"
