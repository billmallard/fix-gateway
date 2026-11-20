#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

"""Crew alerting (CAS2, AER-6083; CAP-119 first slice).

The gateway owns the message list and the displays only draw it
(maos-workspace ``makerplane/briefs/crew_alerting.md`` sections 5 and 12.1).
A catalogue in the connection config names each alert: an id, a level, the
window text and the condition. The plugin watches only the keys its
catalogue names, keeps the message state (active, acknowledged, latched) and
publishes the sorted list in fixed slots, ``CASMSG1``..``CASMSG12``, with
``CASCNT``, ``CASTOP`` and ``CASSEQ``. It also publishes ``NAVFAIL``, the
selected source's lateral guidance lost, which is the HSI's NAV flag rule
owned here so the HSI and CAS read one rule (brief 5.4).

Conditions:

  invalid: KEY        the key is old, bad or failed. ``after_valid: true``
                      arms it only once the key has been valid, so a source
                      an install does not have never alerts (AER-5266).
                      ``grace: s`` instead holds off a key that has not yet
                      been valid for s seconds after start, so a source
                      still starting up does not alert at every power-up;
                      once it has been valid, a loss alerts at once.
  annunciate: KEY     the key's annunciate flag, only while the key is valid:
                      a failed value is not past its limit (brief 5.1, 2).
  value: KEY op LIT   a comparison, only while the key is valid.
  never_written: KEY  the key has not been written ``grace`` seconds after
                      start. Reads ``db_item.written``, which only the
                      gateway sees (brief 9.1).
  nav_lost: true      lateral guidance lost on a nav source that had it
                      earlier in this run; never on a source that was never
                      received, such as an untuned VOR (brief 5.4, C5).

The callbacks run in the writer's thread, inside the source plugin's own
write, so they only compare flags and queue the change (C1, brief 5.5).
Everything else, the list and every write, happens on the plugin's thread.
The CAS keys carry a tol and are rewritten every second, so a stopped or
broken plugin turns them old and a display can say CAS FAIL.
"""

import operator
import queue
import re
import threading
import time
from collections import OrderedDict

import fixgw.plugin as plugin

LEVELS = {"advisory": 1, "caution": 2, "warning": 3}
SLOTS = 12
SLOT_KEYS = ["CASMSG{}".format(n + 1) for n in range(SLOTS)]
ACK_KEYS = ("CASACK", "CASACKBTN")
CAS_KEYS = SLOT_KEYS + ["CASCNT", "CASTOP", "CASSEQ"] + list(ACK_KEYS)
NAV_KEYS = ["CDI", "CDIV", "COURSE", "NAVSRC"]

TEXT_MAX = 20
DETAIL_MAX = 32
ID_RE = re.compile(r"^[A-Z0-9]{1,12}$")
# netfix splits on ';', a slot splits on '|'; neither may appear in text
# (AER-5626: a ';' in key text broke every consumer while all tests passed).
TEXT_RE = re.compile(r"^[A-Z0-9 .,:/()+%'#<>=-]+$")

OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
}

ENTRY_FIELDS = {
    "id",
    "level",
    "text",
    "detail",
    "when",
    "warning_if",
    "on_delay",
    "on_delay_reason",
    "clear_delay",
    "latch",
}
CONDITION_FIELDS = {
    "invalid",
    "after_valid",
    "annunciate",
    "value",
    "never_written",
    "grace",
    "nav_lost",
}
CONFIG_FIELDS = {
    "load",
    "module",
    "CONFIGPATH",
    "entries",
    "test_time",
    "ack_debounce",
    "fuel_no_data_tanks",
    "fuel_no_data_grace",
}


class CatalogueError(ValueError):
    pass


def check_text(text, limit, what):
    if not isinstance(text, str) or not text:
        raise CatalogueError("{} must be text".format(what))
    if len(text) > limit:
        raise CatalogueError(
            "{} {!r} is longer than {} characters".format(what, text, limit)
        )
    if ";" in text or "|" in text:
        raise CatalogueError("{} {!r} contains ';' or '|'".format(what, text))
    if not TEXT_RE.match(text):
        raise CatalogueError(
            "{} {!r} must be upper case letters, digits and simple "
            "punctuation".format(what, text)
        )


def _seconds(value, what):
    try:
        s = float(value)
    except (TypeError, ValueError):
        raise CatalogueError("{} must be a number of seconds".format(what))
    if s < 0:
        raise CatalogueError("{} must not be negative".format(what))
    return s


class Condition(object):
    """One entry's ``when:``. ``keys`` lists the FIX keys it watches."""

    def __init__(self, when, has_key):
        if not isinstance(when, dict):
            raise CatalogueError("when must be a mapping")
        unknown = set(when) - CONDITION_FIELDS
        if unknown:
            raise CatalogueError("unknown condition field {}".format(sorted(unknown)))
        kinds = [k for k in ("invalid", "annunciate", "value", "never_written",
                             "nav_lost") if k in when]
        if len(kinds) != 1:
            raise CatalogueError(
                "when needs exactly one of invalid, annunciate, value, "
                "never_written, nav_lost"
            )
        self.kind = kinds[0]
        self.after_valid = bool(when.get("after_valid", False))
        if "after_valid" in when and self.kind != "invalid":
            raise CatalogueError("after_valid applies only to invalid")
        if "grace" in when and self.kind not in ("invalid", "never_written"):
            raise CatalogueError("grace applies only to invalid and never_written")
        if "grace" in when and self.after_valid:
            raise CatalogueError("grace and after_valid do not combine")
        self.key = None
        self.keys = []
        if self.kind == "nav_lost":
            if when["nav_lost"] is not True:
                raise CatalogueError("nav_lost must be true")
            self.keys = list(NAV_KEYS)
        elif self.kind == "value":
            tokens = str(when["value"]).split()
            if len(tokens) != 3 or tokens[1] not in OPERATORS:
                raise CatalogueError(
                    "value must read 'KEY op literal', e.g. 'APDISC == true'"
                )
            self.key, op, literal = tokens
            self.oper = OPERATORS[op]
            self.literal = literal
            self.keys = [self.key]
        else:
            self.key = when[self.kind]
            self.keys = [self.key]
        default = 30 if self.kind == "never_written" else 0
        self.grace = _seconds(when.get("grace", default), "grace")
        for k in self.keys:
            if not has_key(k):
                raise CatalogueError("no FIX key {}".format(k))

    def bind_literal(self, item):
        """Coerce the literal to the key's type once, at load."""
        if self.kind != "value":
            return
        if item.dtype is bool:
            if self.literal.lower() not in ("true", "false"):
                raise CatalogueError("{} is a bool: compare with true or false"
                                     .format(self.key))
            self.literal = self.literal.lower() == "true"
        else:
            try:
                self.literal = item.dtype(self.literal)
            except ValueError:
                raise CatalogueError("bad literal {!r} for {}"
                                     .format(self.literal, self.key))


class Entry(object):
    def __init__(self, defn, has_key):
        if not isinstance(defn, dict):
            raise CatalogueError("an entry must be a mapping")
        unknown = set(defn) - ENTRY_FIELDS
        if unknown:
            raise CatalogueError("unknown field {}".format(sorted(unknown)))
        self.id = defn.get("id")
        if not isinstance(self.id, str) or not ID_RE.match(self.id):
            raise CatalogueError("id must be 1-12 upper case letters or digits")
        if defn.get("level") not in LEVELS:
            raise CatalogueError("level must be warning, caution or advisory")
        self.level = LEVELS[defn["level"]]
        self.text = defn.get("text")
        check_text(self.text, TEXT_MAX, "text")
        self.detail = defn.get("detail", "")
        if self.detail:
            check_text(self.detail, DETAIL_MAX, "detail")
        else:
            self.detail = ""
        self.warning_if = defn.get("warning_if")
        if self.warning_if is not None and not has_key(self.warning_if):
            raise CatalogueError("no FIX key {}".format(self.warning_if))
        can_warn = self.level == LEVELS["warning"] or self.warning_if is not None
        self.on_delay = _seconds(defn.get("on_delay", 0), "on_delay")
        if self.on_delay and can_warn and not defn.get("on_delay_reason"):
            # A delay on the onset of a warning is latency added to a real
            # alert; it must say why (R11).
            raise CatalogueError("on_delay on a warning needs an on_delay_reason")
        self.clear_delay = _seconds(defn.get("clear_delay", 0), "clear_delay")
        self.latch = bool(defn.get("latch", False))
        self.condition = Condition(defn.get("when"), has_key)

    @property
    def keys(self):
        keys = list(self.condition.keys)
        if self.warning_if and self.warning_if not in keys:
            keys.append(self.warning_if)
        return keys


class TestEntry(object):
    """CAS TEST: one advisory at start, gone after test_time seconds. Seeing
    it proves plugin, bus, widget and glass on every boot (brief 6.4, C2)."""

    id = "CASTEST"
    level = LEVELS["advisory"]
    text = "CAS TEST"
    detail = ""
    warning_if = None
    on_delay = 0
    clear_delay = 0
    latch = False
    keys = []

    def __init__(self, seconds):
        self.seconds = seconds


def fuel_entries(tanks, grace):
    """``FUEL QTY <n> NO DATA`` for each listed tank, never for an unlisted
    one: the default database defines tanks many installs lack."""
    out = []
    for n in tanks:
        n = int(n)
        out.append({
            "id": "FUELQ{}ND".format(n),
            "level": "caution",
            "text": "FUEL QTY {} NO DATA".format(n),
            "when": {"never_written": "FUELQ{}".format(n), "grace": grace},
        })
    return out


class KeyState(object):
    """What the plugin thread knows about one watched key. ``ever_valid``
    and ``last_valid`` count only written values: a key's initial value from
    the database is not a reading, and arming on it would alert on an
    install that has no such source."""

    def __init__(self, item):
        self.item = item
        self.value = None
        self.flags = (False, False, False, False)  # annunciate, old, bad, fail
        self.valid = False
        self.annunciate = False
        self.ever_valid = False
        self.last_valid = None

    def update(self, x):
        v, annunc, old, bad, fail = x[0], x[1], x[2], x[3], x[4]
        self.value = v
        self.flags = (bool(annunc), bool(old), bool(bad), bool(fail))
        self.annunciate = bool(annunc)
        self.valid = not (old or bad or fail)
        if self.valid and self.item.written:
            self.ever_valid = True
            self.last_valid = v


class Message(object):
    """Runtime state of one catalogue entry."""

    def __init__(self, entry):
        self.entry = entry
        self.active = False
        self.acked = False
        self.onset_seq = None  # the CASSEQ that first published it
        self.level = entry.level
        self.pending_since = None  # condition held, waiting out on_delay
        self.clear_since = None  # condition clear, waiting out clear_delay

    def slot(self):
        return "{}|{}|{}|{}|{}".format(
            self.level, 1 if self.acked else 0, self.entry.id, self.entry.text,
            self.entry.detail)


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.clock = time.monotonic
        self.events = queue.Queue()
        self.messages = []
        self.watched = OrderedDict()  # key -> KeyState
        self.sig = {}  # key -> last flag signature seen by the callback
        self.value_keys = set()  # keys whose value, not only flags, matters
        self.refused = []
        self.seq = 0
        self.ack_debounce = 0.5
        self.last_btn = None
        self.navfail = True
        self.nav_had_valid = set()
        self.published = None
        self.published_navfail = None
        self.last_publish = None
        self.start_time = None
        self.thread = None
        self.getout = threading.Event()
        self.errors = 0
        self.callback_errors = 0
        self._registered = []

    # -- catalogue ---------------------------------------------------------

    def _has_key(self, key):
        try:
            return self.db_get_item(key) is not None
        except KeyError:
            return False

    def load_catalogue(self):
        unknown = set(self.config) - CONFIG_FIELDS
        for k in sorted(unknown):
            self.log.warning("Unknown option {} ignored".format(k))
        self.test_time = _seconds(self.config.get("test_time", 5), "test_time")
        if self.test_time < 1:
            # The power-up test is the only proof the window is on the glass
            # (C2), so it cannot be turned off.
            self.log.warning("test_time below 1 s: using 5")
            self.test_time = 5.0
        self.ack_debounce = _seconds(self.config.get("ack_debounce", 0.5),
                                     "ack_debounce")
        defs = list(self.config.get("entries") or [])
        tanks = self.config.get("fuel_no_data_tanks") or []
        grace = self.config.get("fuel_no_data_grace", 30)
        defs.extend(fuel_entries(tanks, grace))
        entries = [TestEntry(self.test_time)]
        ids = {e.id for e in entries}
        for defn in defs:
            name = defn.get("id") if isinstance(defn, dict) else defn
            try:
                e = Entry(defn, self._has_key)
                if e.id in ids:
                    raise CatalogueError("duplicate id")
                if e.condition.kind == "value":
                    e.condition.bind_literal(self.db_get_item(e.condition.key))
            except CatalogueError as err:
                # One bad entry costs that alert, logged by name, never the
                # whole list (FHA CAS-2 counts it as a missing alert).
                self.log.error("Catalogue entry {} refused: {}".format(name, err))
                self.refused.append(str(name))
                continue
            ids.add(e.id)
            entries.append(e)
        return entries

    # -- callback side (runs in the writer's thread) ----------------------

    def _signature(self, key, x):
        flags = (bool(x[1]), bool(x[2]), bool(x[3]), bool(x[4]))
        if key in self.value_keys:
            return (x[0],) + flags
        return flags

    def on_change(self, key, x, udata):
        """Record a flag edge and queue it, nothing more. Constant time, no
        lock held by the plugin thread, and it catches its own exceptions,
        since the aux path in database.py does not (C1)."""
        try:
            if "." in key:  # an aux write: a configuration change, not state
                return
            if key not in ACK_KEYS:
                # Acknowledges are never deduplicated: a stale CASACK that was
                # ignored must not swallow a later real one of the same value.
                sig = self._signature(key, x)
                if self.sig.get(key) == sig:
                    return
                self.sig[key] = sig
            self.events.put((key, x))
        except Exception:
            self.callback_errors += 1

    # -- plugin thread -----------------------------------------------------

    def _apply(self, key, x, now):
        if key == "CASACK":
            self._ack_to(x[0], "CASACK")
        elif key == "CASACKBTN":
            st = self.watched[key]
            rising = bool(x[0]) and not bool(st.value)
            st.update(x)
            if rising and st.valid:
                if self.last_btn is None or now - self.last_btn >= self.ack_debounce:
                    self.last_btn = now
                    self._ack_to(self.seq, "CASACKBTN")
            return
        if key in self.watched:
            self.watched[key].update(x)

    def _ack_to(self, seq, source):
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            return
        if seq > self.seq:
            # A sequence this plugin has not published yet: a display's stale
            # setting after a restart. Honouring it would acknowledge alerts
            # nobody has seen (C3).
            self.log.warning("{} {} is ahead of CASSEQ {}: ignored"
                             .format(source, seq, self.seq))
            return
        for m in self.messages:
            if m.active and m.onset_seq is not None and m.onset_seq <= seq:
                m.acked = True

    def _poll(self):
        for key, st in self.watched.items():
            if key in ACK_KEYS:
                continue
            st.update(self.db_read(key))

    def _lateral_lost(self):
        """The HSI's NAV flag rule (pyEfis hsi ``_nav_flag_shown``): the
        selected source's CDI stale, invalid or failed, no lateral signal
        (CDIV below 0.5, or CDIV itself untrusted), or no valid COURSE."""
        cdi = self.watched["CDI"]
        cdiv = self.watched["CDIV"]
        _annunc, _old, course_bad, course_fail = self.watched["COURSE"].flags
        return (not cdi.valid or not cdiv.valid or cdiv.value < 0.5
                or course_bad or course_fail)

    def _update_nav(self):
        if not all(k in self.watched for k in NAV_KEYS):
            return
        self.navfail = self._lateral_lost()
        src = int(round(self.watched["NAVSRC"].value or 0))
        if not self.navfail and all(self.watched[k].item.written
                                    for k in ("CDI", "CDIV")):
            self.nav_had_valid.add(src)
        self.nav_lost = self.navfail and src in self.nav_had_valid

    def _holds(self, entry, now):
        c = getattr(entry, "condition", None)
        if c is None:  # CAS TEST
            return now - self.start_time < entry.seconds
        if c.kind == "nav_lost":
            return self.nav_lost
        if c.kind == "never_written":
            return (now - self.start_time >= c.grace
                    and not self.db_get_item(c.key).written)
        st = self.watched[c.key]
        if c.kind == "invalid":
            if st.valid:
                return False
            if st.ever_valid:
                return True
            return not c.after_valid and now - self.start_time >= c.grace
        if c.kind == "annunciate":
            return st.valid and st.annunciate
        return st.valid and c.oper(st.value, c.literal)

    def _evaluate(self, now, polled=True):
        # NAV state is judged only on a fresh read of all its inputs: a
        # queued NAVSRC change is applied before the CDI and CDIV that
        # compute rewrote for the new source, and judging between them
        # would mix two sources.
        if polled:
            self._update_nav()
        for m in self.messages:
            e = m.entry
            holds = self._holds(e, now)
            if not m.active:
                if not holds:
                    m.pending_since = None
                    continue
                if e.on_delay:
                    if m.pending_since is None:
                        m.pending_since = now
                    if now - m.pending_since < e.on_delay:
                        continue
                m.active = True
                m.acked = False
                m.onset_seq = None
                m.clear_since = None
                m.level = e.level
                if e.warning_if:
                    if self.watched[e.warning_if].last_valid:
                        m.level = LEVELS["warning"]
            elif holds:
                m.clear_since = None
            elif e.latch and not m.acked:
                continue
            else:
                if e.clear_delay:
                    if m.clear_since is None:
                        m.clear_since = now
                    if now - m.clear_since < e.clear_delay:
                        continue
                m.active = False
                m.pending_since = None

    def ordered(self):
        """Warnings, cautions, advisories; within a level unacknowledged
        first, then newest first (R2, R3), catalogue order on a tie."""
        active = [m for m in self.messages if m.active]
        return sorted(
            active,
            key=lambda m: (-m.level, m.acked, -(m.onset_seq or 0),
                           self.messages.index(m)))

    def _publish(self, now):
        unseen = [m for m in self.messages if m.active and m.onset_seq is None]
        state = [(m.entry.id, m.level, m.acked) for m in self.ordered()]
        changed = bool(unseen) or state != self.published
        nav = self.has_navfail and "CDI" in self.watched
        if changed:
            self.seq += 1
            for m in unseen:
                m.onset_seq = self.seq
        elif self.last_publish is not None and now - self.last_publish < 1.0:
            # The HSI reads NAVFAIL: a change goes out now, not at the next
            # refresh, and without a new CASSEQ, since the list is the same.
            if nav and self.navfail != self.published_navfail:
                self.db_write("NAVFAIL", self.navfail)
                self.published_navfail = self.navfail
            return
        order = self.ordered()
        slots = [m.slot() for m in order[:SLOTS]]
        slots += [""] * (SLOTS - len(slots))
        top = max([m.level for m in order if not m.acked] or [0])
        for key, value in zip(SLOT_KEYS, slots):
            self.db_write(key, value)
        self.db_write("CASCNT", len(order))
        self.db_write("CASTOP", top)
        if nav:
            self.db_write("NAVFAIL", self.navfail)
            self.published_navfail = self.navfail
        # The sequence goes last, so a display that redraws on CASSEQ reads
        # every slot of the list it names.
        self.db_write("CASSEQ", self.seq)
        self.published = [(m.entry.id, m.level, m.acked) for m in order]
        self.last_publish = now

    def step(self, now=None, timeout=0.0):
        """One plugin-thread cycle: apply queued changes in order, poll the
        watched keys, evaluate and publish. Tests drive this directly."""
        now = self.clock() if now is None else now
        try:
            key, x = self.events.get(timeout=timeout)
            while True:
                self._apply(key, x, now)
                self._evaluate(now, polled=False)  # a short blip still counts
                key, x = self.events.get_nowait()
        except queue.Empty:
            pass
        self._poll()
        self._evaluate(now)
        self._publish(now)

    def _loop(self):
        while not self.getout.is_set():
            try:
                self.step(timeout=0.2)
            except Exception as e:
                # Skip this cycle's refresh: a list that cannot be computed
                # must go old on the bus, not be rewritten as current (C4).
                self.errors += 1
                if self.errors <= 5 or self.errors % 100 == 0:
                    self.log.error("CAS evaluation failed ({}): {}"
                                   .format(self.errors, e))
                self.getout.wait(0.2)

    # -- plugin interface --------------------------------------------------

    def run(self):
        self.setup()
        self.thread = threading.Thread(target=self._loop, name="cas", daemon=True)
        self.thread.start()

    def setup(self):
        """Everything run() does except start the thread; tests then drive
        step() with their own clock."""
        missing = [k for k in CAS_KEYS if not self._has_key(k)]
        if missing:
            # An install whose edited database.yaml lacks the cas.yaml
            # include (the AER-3915 case): say so, rather than half-run.
            raise plugin.PluginFail(
                "CAS keys missing from the database: {} (include "
                "database/cas.yaml in database.yaml)".format(", ".join(missing)))
        self.has_navfail = self._has_key("NAVFAIL")
        entries = self.load_catalogue()
        self.messages = [Message(e) for e in entries]
        keys = list(ACK_KEYS)
        self.value_keys = {"CDIV", "NAVSRC"}
        if all(self._has_key(k) for k in NAV_KEYS):
            keys += NAV_KEYS
        for e in entries:
            for k in e.keys:
                if k not in keys:
                    keys.append(k)
                    self.log.info("Watching {} for {}".format(k, e.id))
            if e.warning_if:
                self.value_keys.add(e.warning_if)
            c = getattr(e, "condition", None)
            if c is not None and c.kind == "value":
                self.value_keys.add(c.key)
        for k in keys:
            self.watched[k] = KeyState(self.db_get_item(k))
            self.watched[k].update(self.db_read(k))
        self.nav_lost = False
        # Restart-safe sequence (C3): start above any CASSEQ or CASACK on the
        # bus, so a stale acknowledge cannot cover the next message.
        self.seq = max(int(self.db_read("CASSEQ")[0]),
                       int(self.db_read("CASACK")[0]), 0)
        self.start_time = self.clock()
        for k in keys:
            self.db_callback_add(k, self.on_change)
            self._registered.append(k)

    def stop(self):
        for k in self._registered:
            self.db_callback_del(k, self.on_change)
        self._registered = []
        self.getout.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(2.0)
            if self.thread.is_alive():
                raise plugin.PluginFail

    def get_status(self):
        return OrderedDict([
            ("Entries", len(self.messages)),
            ("Refused", ", ".join(self.refused) or "none"),
            ("Active", sum(1 for m in self.messages if m.active)),
            ("Sequence", self.seq),
            ("Evaluation errors", self.errors),
            ("Callback errors", self.callback_errors),
        ])
