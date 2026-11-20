#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Demo source (DEMO2, makerplane/briefs/demo_mode_redesign.md sections 3-5).
#
"""Fly a synthetic-tier profile into this gateway, in process, looped, at 1x.

The demo is a data source like the X-Plane plugin or a real AHRS. It runs the
``fixgw.sim`` generator (the SYN2 live mode, without netfix) and writes only
what a sensor or a pilot would: the generator's position, attitude and air
data (:data:`SOURCE_KEYS`), the engine's sensors (:data:`ENGINE_KEYS`, DEMO3,
:mod:`fixgw.plugins.demo.engine`), and the route and its commands through the
FPL contract (slots, segments, ``FPLSEQ`` last, then ``FPLCMD``). The real
flight-plan engine, compute and annunciate do everything else: the scripted
hot cylinder is a CHT value, and its alarm is annunciate's, from the
configured limits. It never writes a compute
or engine output, an annunciate flag, or the generator's ``REF*`` reference
keys: the reference navigator is a CI tool and not part of the demo.

One writer per key, both ways round:

- at start it refuses when another plugin has written any source key (the
  AER-4476 writer registry), or when ``LAT``/``LONG`` move while it watches
  without writing them (the SYN2 live check, which also sees a netfix
  client);
- running, a second writer on a source or engine key stops it. The route keys are a
  shared contract by design (the engine republishes them on restore, a
  pilot's editor commits them), so they are not watched.

With ``traffic`` on (the default), a few ADS-B targets orbit the route's
hold through the stratux plugin's own GDL90 decoder (DEMO7,
:mod:`fixgw.plugins.demo.traffic`). The TFC block is then the demo's as
well: watched at start and while running like the sensor keys.

It also writes its own two keys (DEMO4, ``database/demo.yaml``):
``DEMOLIVE``, true about once a second while it runs and false when it stops,
which drives the DEMO marking on the glass; and ``DEMOPAGE``, the page the
``pages`` schedule names at that point in the route, which a pyEfis kiosk
follows. It refuses to start on a database without them, so the demo cannot
run without its marking. A third, ``DEMOLOOP``, is the number of the pass now
flying (1, 2, ...), written as each pass starts; a kiosk resets the settings
an attendee may have changed when it moves (DEMO12). It is optional: an
older database without it runs the demo, said once in the log.

At the end of the route it clears the plan, holds the position bad for
``restart_s`` (nav flags, SVS unavailable: honest), commits the route again
and starts over. Time runs at 1x only: faster, the airplane covers more
ground than the GS it shows.
"""

import os
import re
import threading
import time
from collections import OrderedDict

import fixgw.database as database
import fixgw.plugin as plugin
from fixgw.plugins.demo import engine, traffic
from fixgw.plugins.demo.engine import ENGINE_KEYS

# The generator's sensor set (fixgw.sim.generator.SENSOR_KEYS), kept here so
# the registry check runs before the sim package is imported; a test holds
# the two equal.
SOURCE_KEYS = ("MAGVAR", "GPS_FIX_TYPE", "GPS_ACCURACY_HORIZ", "ALT", "GS", "TAS", "IAS", "VS",
               "TRACK", "HEAD", "PITCH", "ROLL", "LAT", "LONG")
POSITION_KEYS = ("LAT", "LONG")
REF_PREFIX = "REF"
TRAFFIC_PREFIX = "TFC"
LIVE_KEY = "DEMOLIVE"
PAGE_KEY = "DEMOPAGE"
DEMO_KEYS = (LIVE_KEY, PAGE_KEY)
LOOP_KEY = "DEMOLOOP"
LIVE_EVERY_S = 1.0
WRITER = "demo"
DEFAULT_PROFILE = "demo5-ksba-v27-ksmx-rnav30-missed.yaml"


def other_writers(keys=SOURCE_KEYS):
    """{key: [plugins]} for each of ``keys`` some plugin other than demo has
    written, from the writer registry."""
    out = {}
    for key in keys:
        try:
            ws = [w for w in database.get_raw_item(key).writers if w != WRITER]
        except KeyError:
            continue
        if ws:
            out[key] = ws
    return out


def _describe(found):
    return ", ".join("{} ({})".format(k, ", ".join(v)) for k, v in sorted(found.items()))


def renumber(rows, fplseq, cmd_base):
    """``rows`` with the route commit at ``fplseq`` and the pilot's commands
    numbered from ``cmd_base`` + 1, as a gateway that has seen earlier
    commits and commands needs. The generator numbers them from 1."""
    out = []
    n = 0
    for t, row in rows:
        if "FPLSEQ" in row or "FPLCMD" in row:
            row = dict(row)
            if "FPLSEQ" in row:
                row["FPLSEQ"] = fplseq
            if "FPLCMD" in row:
                n += 1
                row["FPLCMD"] = "{} {}".format(cmd_base + n, row["FPLCMD"].split(" ", 1)[1])
        out.append((t, row))
    return out


def route_rows(run, have):
    """The run's rows less the ``REF*`` keys and any key this database does
    not define, and the sorted list of the undefined ones."""
    absent = set()
    rows = []
    for t, row in run.rows:
        kept = {}
        for k, v in row.items():
            if k.startswith(REF_PREFIX):
                continue
            if k not in have:
                absent.add(k)
                continue
            kept[k] = v
        rows.append((t, kept))
    return rows, sorted(absent)


def parse_pages(pages):
    """The ``pages`` config as a sorted [(route_s, page)] list. Each entry is
    ``{at: <seconds into the route>, page: <SCREEN or SCREEN/TAB>}``. Raises
    ValueError on anything else, so a typo refuses at start, not mid-show."""
    out = []
    for entry in pages or ():
        if not isinstance(entry, dict) or set(entry) != {"at", "page"}:
            raise ValueError("pages: each entry is {{at: <s>, page: <name>}}, not {!r}".format(entry))
        at, page = float(entry["at"]), entry["page"]
        if at < 0 or not isinstance(page, str) or not page.strip():
            raise ValueError("pages: bad entry {!r}".format(entry))
        out.append((at, page.strip()))
    return sorted(out, key=lambda e: e[0])


def page_rows(rows, pages):
    """``rows`` with ``DEMOPAGE`` written on the first row at or after each
    scheduled time (an entry past the route's end lands on its last row)."""
    if not pages or not rows:
        return rows
    out = [(t, dict(row)) for t, row in rows]
    i = 0
    for at, page in pages:
        while i < len(out) - 1 and out[i][0] < at:
            i += 1
        out[i][1][PAGE_KEY] = page
    return out


def hold_center(rows):
    """(lat, lon, alt_ft) of the route's hold fix, from the route block in
    ``rows``: the slot coded ``HM``, at its altitude constraint. None when the
    route has no hold, or the hold no altitude."""
    for _, row in rows:
        if "FPLCOUNT" not in row:
            continue
        for i in range(1, int(row["FPLCOUNT"]) + 1):
            if row.get("FPL{}PT".format(i)) == "HM":
                alts = re.findall(r"\d+", str(row.get("FPL{}ALT".format(i), "")))
                if not alts:
                    return None
                return (row["FPL{}LAT".format(i)], row["FPL{}LON".format(i)], float(alts[-1]))
        return None
    return None


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__(name="demo")
        self.parent = parent
        self.log = parent.log
        self.config = parent.config
        self.halt = threading.Event()
        self.status = parent.status
        self.refused = None
        self.running = False
        self.clock = time.monotonic     # live.stream's schedule; tests fast-forward it
        self.sleep = time.sleep
        self.traffic = None
        self.center = None
        self.live_at = None
        self.engine_last = {}
        self.loop_key_on = False

    def stop(self):
        self.halt.set()

    # The writes.  A source key goes through set() so a write after the
    # restart hold clears the bad flag the hold put on LAT/LONG; a route or
    # command key is a plain write, as an editor makes it.
    def _write(self, key, value):
        if key in ENGINE_KEYS:
            self.engine_last[key] = value
        if key in SOURCE_KEYS or key == PAGE_KEY:
            database.get_raw_item(key).set(value, bad=False)
        else:
            database.write(key, value)

    def _read(self, key):
        return database.read(key)[0]

    # DEMOLIVE: true about once a second while running, so it goes old (tol
    # 2 s) soon after the demo dies without the chance to clear it.
    def _live(self):
        now = self.clock()
        if self.live_at is None or now - self.live_at >= LIVE_EVERY_S:
            database.get_raw_item(LIVE_KEY).set(True, bad=False)
            self.live_at = now

    # Once per row, from live.stream: the traffic emitter's step (it sends on
    # its own 1 s deadline), then the second-writer check.
    def _second_writer(self):
        self._live()
        if self.traffic is not None:
            self.traffic.step()
        found = other_writers(self.parent.keys)
        if found:
            self.refused = "{} also written by another plugin".format(_describe(found))
            return True
        return self.halt.is_set()

    def _refuse(self, why):
        self.refused = why
        self.status["State"] = "refused"
        self.status["Reason"] = why
        self.log.critical("demo: not running: " + why)

    def run(self):
        self.running = True
        try:
            self._run()
        except Exception as e:
            self._refuse("{}: {}".format(type(e).__name__, e))
        finally:
            if self.live_at is not None:
                database.get_raw_item(LIVE_KEY).set(False, bad=False)
            self.running = False

    def _run(self):
        from fixgw.sim import live
        from fixgw.sim.generator import generate

        watch_s = float(self.config.get("watch_s", 3.0))
        self.status["State"] = "watching"
        first = {k: self._read(k) for k in POSITION_KEYS}
        t0 = time.monotonic()
        profile = self.parent.profile_path
        run = generate(profile, frequency_ms=int(self.config.get("frequency_ms", 200)))
        with_engine, eng = engine.add_engine(run.rows, self._event_limits())
        rows, absent = route_rows(_Run(with_engine), set(database.listkeys()))
        rows = page_rows(rows, self.parent.pages)
        if absent:
            self.log.warning("demo: the database has no {}: not written".format(" ".join(absent)))
        left = watch_s - (time.monotonic() - t0)
        if left > 0 and self.halt.wait(left):
            return
        moving = [k for k in POSITION_KEYS if self._read(k) != first[k]]
        found = other_writers(self.parent.keys)
        if found:
            return self._refuse("another source writes {}".format(_describe(found)))
        if moving:
            return self._refuse("something else is moving {} (a netfix client?)".format(" ".join(moving)))

        if self.parent.traffic_on:
            self.traffic = traffic.DemoTraffic(database.get_raw_item, self.log, clock=self.clock)
            self.center = hold_center(rows)
        restart_s = float(self.config.get("restart_s", 5.0))
        self.status["Profile"] = run.profile.name
        self.status["Route s"] = round(rows[-1][0], 1)
        self.status["CHT event"] = ", ".join("{} {:.0f} s".format(w, t) for t, w in eng.marks) or "none"
        loops = 0
        while not self.halt.is_set():
            fplseq = int(float(self._read("FPLSEQ"))) + 1
            cmd_base = abs(int(float(self._read("FPLCMDACK"))))
            self.status["State"] = "flying"
            self.status["Loops"] = loops
            if self.loop_key_on:
                database.get_raw_item(LOOP_KEY).set(loops + 1, bad=False)
            if self.traffic is not None and self.center is not None:
                self.traffic.center_on(*self.center)
            stats = live.stream(_Run(renumber(rows, fplseq, cmd_base)), self._write,
                                speed=1.0, stop=self._second_writer, clock=self.clock, sleep=self.sleep)
            if stats.stopped:
                if self.refused:
                    self.status["State"] = "stopped"
                    self.status["Reason"] = self.refused
                    self.log.critical("demo: stopped: " + self.refused)
                return
            loops += 1
            self.status["State"] = "restarting"
            self._clear_plan()
            if self._hold(restart_s):
                return

    def _event_limits(self):
        """The hot cylinder's configured (highWarn, highAlarm), read now, or
        None, said once, and no event."""
        key = "CHT1{}".format(engine.HOT_CYLINDER)
        try:
            item = database.get_raw_item(key)
            limits = engine.event_limits(item.get_aux_value("highWarn"), item.get_aux_value("highAlarm"))
        except KeyError:
            limits = None
        if limits is None:
            self.log.warning("demo: {} has no highWarn below a highAlarm: no CHT event".format(key))
        return limits

    # The restart hold; the traffic keeps flying through it, and the engine's
    # last reading is repeated each second so the gauges do not go old.
    # True if halted.
    def _hold(self, seconds):
        end = self.clock() + seconds
        next_engine = self.clock()
        while self.clock() < end:
            self._live()
            if self.traffic is not None:
                self.traffic.step()
            if self.clock() >= next_engine:
                for key, value in self.engine_last.items():
                    self._write(key, value)
                next_engine += 1.0
            if self.halt.wait(0):
                return True
            self.sleep(min(0.2, max(0.0, end - self.clock())))
        return self.halt.is_set()

    def _clear_plan(self):
        """The editor's clear (an empty route, committed), then the position
        flagged bad until the next loop writes it."""
        for key, value in (("FPLSEGCNT", 0), ("FPLCOUNT", 0), ("FPLNAME", ""), ("FPLAPRTYPE", ""),
                           ("FPLSEQ", int(float(self._read("FPLSEQ"))) + 1)):
            database.write(key, value)
        for key in POSITION_KEYS:
            database.get_raw_item(key).set(self._read(key), bad=True)


class _Run:
    """What live.stream reads of a generator Run."""

    def __init__(self, rows):
        self.rows = rows
        self.timeline = []


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.status = OrderedDict()
        self.keys = SOURCE_KEYS + DEMO_KEYS
        self.pages = []
        self.traffic_on = False
        self.profile_path = self._profile_path(config.get("profile", DEFAULT_PROFILE))
        self.thread = MainThread(self)

    @staticmethod
    def _profile_path(name):
        if os.path.isabs(name):
            return name
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(here, "sim", "profiles", name)

    def run(self):
        speed = self.config.get("speed", 1)
        if float(speed) != 1.0:
            self._fail("speed {} is not 1: the demo runs in real time only".format(speed))
        try:
            import fixgw.sim.generator  # noqa: F401  (needs geographiclib)
        except ImportError as e:
            self._fail("the synthetic flight tier is not installed ({}): pip install 'fixgw[sim]'".format(e))
        missing = [k for k in DEMO_KEYS if k not in database.listkeys()]
        if missing:
            self._fail("the database has no {} (database/demo.yaml): the demo does not run "
                       "without its DEMO marking".format(" ".join(missing)))
        try:
            self.pages = parse_pages(self.config.get("pages"))
        except ValueError as e:
            self._fail(str(e))
        have = set(database.listkeys())
        self.thread.loop_key_on = LOOP_KEY in have
        if not self.thread.loop_key_on:
            self.log.warning("demo: the database has no {} (database/demo.yaml): a kiosk will "
                             "not reset its visitor settings each loop".format(LOOP_KEY))
        self.keys = (SOURCE_KEYS + DEMO_KEYS + ((LOOP_KEY,) if self.thread.loop_key_on else ())
                     + tuple(k for k in ENGINE_KEYS if k in have) + self._traffic_keys())
        found = other_writers(self.keys)
        if found:
            self._fail("another source writes {}".format(_describe(found)))
        self.thread.start()

    # The TFC block, when traffic is on and the database has it (an edited
    # older config may not: said once, and no traffic).
    def _traffic_keys(self):
        if not self.config.get("traffic", True):
            return ()
        keys = tuple(k for k in database.listkeys() if k.startswith(TRAFFIC_PREFIX))
        if "TFC1TGT" not in keys:
            self.log.error("traffic: true but the database has no TFC block "
                           "(database/traffic.yaml); no demo traffic")
            return ()
        self.traffic_on = True
        return keys

    def _fail(self, why):
        self.status["State"] = "refused"
        self.status["Reason"] = why
        self.log.critical("demo: not starting: " + why)
        raise plugin.PluginFail(why)

    def is_running(self):
        return self.running and self.thread.is_alive()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(2.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return self.status
