#!/usr/bin/env python
#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Persist selected FIX keys across restarts (issue #84).

"""Persist a small set of FIX keys across fix-gateway restarts.

Some FIX values are *pilot selections*, not sensed data -- e.g. ``NAVSRC``
(the HSI nav-source selector). Without persistence they reset to a default on
every boot, so the pilot has to re-select the source after each restart. This
plugin restores the last value of each configured key at startup and saves it
back whenever it changes.

Storage is a small JSON file written atomically (temp + ``os.replace``); no
external dependencies. Configuration (a ``connections`` entry)::

    state_persist:
      load: yes
      module: fixgw.plugins.state_persist
      keys:                       # FIX keys whose last value should survive
        - NAVSRC
      # state_file: "{CONFIG}/persist_state.json"   # optional; default shown
      # restore_delay: 2.0        # seconds to wait before restoring (see run())

The restore is deferred a couple of seconds so the other plugins (the compute
``select`` router, the source feeds) have subscribed and published first -- then
writing the restored ``NAVSRC`` immediately routes the correct ``COURSE``.
"""

import json
import os
import threading
import time

import fixgw.plugin as plugin


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log
        cfg = parent.config

        default_path = os.path.join(cfg.get("CONFIGPATH", "."), "persist_state.json")
        self.path = cfg.get("state_file", default_path)
        self.keys = list(cfg.get("keys", []))
        self.restore_delay = float(cfg.get("restore_delay", 2.0))
        self._lock = threading.Lock()
        self.dirty = False
        # The values to restore, read once from disk. `state` starts as a copy
        # and only diverges when the pilot changes a key AFTER restore.
        self._to_restore = self._load()
        self.state = dict(self._to_restore)
        # Gate: do NOT save changes until the restore has run. At startup each
        # key gets its db default (e.g. NAVSRC -> GPS); capturing that would
        # clobber the persisted selection before we get to restore it.
        self._restored = False

        # Subscribe now, but ignore changes until restored (see _on_change).
        for key in self.keys:
            self.parent.db_callback_add(key, self._on_change)

    def _load(self):
        """Read the persisted state, keeping only currently-configured keys."""
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if k in self.keys}
        except FileNotFoundError:
            return {}
        except Exception as e:
            self.log.warning(f"state_persist: could not read {self.path}: {e}")
            return {}

    def _on_change(self, key, value, udata=None):
        # value is the item tuple (value, annunciate, old, bad, fail, secfail);
        # meta/aux updates arrive as non-tuples and are ignored.
        if not isinstance(value, tuple):
            return
        if not self._restored:
            return  # ignore startup defaults until we've restored the saved value
        with self._lock:
            if self.state.get(key) != value[0]:
                self.state[key] = value[0]
                self.dirty = True

    def _flush(self):
        with self._lock:
            if not self.dirty:
                return
            data = dict(self.state)
            self.dirty = False
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)  # atomic: never a half-written file
        except Exception as e:
            self.log.error(f"state_persist: could not write {self.path}: {e}")

    def _restore(self):
        for key, val in self._to_restore.items():
            try:
                self.parent.db_write(key, val)
                self.log.info(f"state_persist: restored {key} = {val}")
            except Exception as e:
                self.log.warning(f"state_persist: could not restore {key}: {e}")
        # From here on, real changes are captured and saved. (The db_write above
        # fires _on_change while still gated, so it is not re-saved.)
        self._restored = True

    def run(self):
        # Let the other plugins subscribe + publish before restoring, so the
        # restored selection routes immediately (and does not get lost writing
        # into a compute select that has not subscribed yet).
        deadline = time.time() + self.restore_delay
        while not self.getout and time.time() < deadline:
            time.sleep(0.1)
        if not self.getout:
            self._restore()
        while not self.getout:
            time.sleep(1.0)
            self._flush()
        self._flush()  # final save on shutdown

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(2.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return {}
