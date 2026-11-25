"""rpi_virtualwire: the airspeed radio writes only what it receives (SRC3).

The plugin polls the receiver every 0.25 s. A dead radio must let IAS and
OAT age old on their tol, not hold the tape at its last value, fresh, with
IASW still 3 (AER-6542).
"""

from types import ModuleType, SimpleNamespace
import importlib
import logging
import sys
import time

import pytest

POLL = 0.25  # s, the plugin's receive loop
KEYS = ("IAS", "OAT", "IASW", "OATW")


class FakeRx:
    """The virtualwire receiver: queued messages, as bytes."""

    def __init__(self):
        self.queue = []

    def send(self, text):
        self.queue.append([ord(c) for c in text])

    def ready(self):
        return bool(self.queue)

    def get(self):
        return self.queue.pop(0)

    def cancel(self):
        pass


class Clock:
    """time.time and time.sleep together: each sleep advances the clock,
    so the database ages items exactly as far as the loop has run."""

    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def radio(database, monkeypatch):
    rx = FakeRx()
    pigpio = ModuleType("pigpio")
    pigpio.pi = lambda: SimpleNamespace(stop=lambda: None)
    vw_pkg = ModuleType("virtualwire")
    vw_pkg.virtualwire = SimpleNamespace(rx=lambda _pi, _pin, _bps: rx)
    monkeypatch.setitem(sys.modules, "pigpio", pigpio)
    monkeypatch.setitem(sys.modules, "virtualwire", vw_pkg)
    module = importlib.import_module("fixgw.plugins.rpi_virtualwire")
    module = importlib.reload(module)

    clock = Clock()
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(time, "sleep", clock.sleep)

    writes = []

    def db_write(key, value):
        writes.append(key)
        database.write(key, value)

    parent = SimpleNamespace(
        log=logging.getLogger("test.rpi_virtualwire"),
        config={"rxpin": 23, "bps": 2000},
        db_write=db_write,
    )
    thread = module.MainThread(parent)

    def poll(n):
        """Run the receive loop for n passes, then stop it."""
        target = thread.count + n

        def sleep(seconds):
            clock.sleep(seconds)
            if thread.count + 1 >= target:
                thread.getout = True

        monkeypatch.setattr(module.time, "sleep", sleep)
        thread.getout = False
        thread.run()
        assert thread.count == target

    return SimpleNamespace(rx=rx, poll=poll, writes=writes, db=database)


def test_silence_writes_nothing_and_ias_ages_old(radio):
    radio.rx.send("120.0, 15.0")
    radio.poll(1)
    assert radio.db.read("IAS")[2] is False
    assert radio.db.read("IASW")[0] == 3
    radio.writes.clear()

    # One second with no message: nothing is rewritten.
    radio.poll(round(1.0 / POLL))
    assert [key for key in radio.writes if key in KEYS] == []

    # So once the last message is older than each key's tol, the key reads
    # old: the tape flags, rather than freezing at its last value.
    tol = max(radio.db.get_raw_item(key).tol for key in KEYS) / 1000.0
    radio.poll(round(tol / POLL))
    assert {key: radio.db.read(key)[2] for key in KEYS} == {
        key: True for key in KEYS
    }

    # The next message clears it.
    radio.rx.send("121.0, 15.5")
    radio.poll(1)
    assert {key: radio.db.read(key)[2] for key in KEYS} == {
        key: False for key in KEYS
    }
    assert radio.db.read("OAT")[0] == pytest.approx(15.5)


def test_garbled_or_sentinel_message_flags_without_a_value_write(radio):
    radio.rx.send("120.0, 15.0")
    radio.poll(1)
    radio.writes.clear()

    radio.rx.send("12#.!")
    radio.poll(1)
    assert radio.writes == ["IASW", "OATW"]
    assert radio.db.read("IASW")[0] == 2
    assert radio.db.read("OATW")[0] == 2
    radio.writes.clear()

    # 400 and -50 are the sender's no-reading values: each flags its own
    # key only, and writes no value.
    radio.rx.send("400, 16.0")
    radio.poll(1)
    assert sorted(radio.writes) == ["IASW", "OAT", "OATW"]
    assert radio.db.read("IASW")[0] == 2
    assert radio.db.read("OATW")[0] == 3
