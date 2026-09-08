#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  The flightplan navigation engine plugin (FP2, fix-gateway#23).
#
"""The flight-plan navigation engine: fixgw.plugins.flightplan.

A compute-only plugin -- it never needs a nav database, only coordinates.
It reads the route block (``FPL1..50``, ``FPLCOUNT``, on ``FPLSEQ`` change),
``DTO*``, ``FPLCMD``, ``LAT``/``LONG``/``GS``/``MAGVAR``, and writes the
engine output keys defined by FP1 (``doc/flightplan_keys.md``): leg guidance
(DTK/XTK/CDI/TO-FROM), sequencing, Direct-To, SUSP/RESUME, the full DO-229
CDI-scaling/flight-phase behaviour including the approach (Bill's ruling of
2026-09-08 -- there is no VFR-advisory mode), and persistence.

The navigation logic itself lives in :mod:`fixgw.plugins.flightplan.engine`
(pure Python, no fixgw.database dependency) so it is directly unit testable;
this module is the callback/persistence wiring, precedented by
``plugins/compute.py`` (callbacks), ``plugins/state_persist`` (atomic JSON,
restore-after-delay) and ``plugins/skel.py`` (lifecycle).

Config (``connections/flightplan.yaml``)::

    flightplan:
      load: FLIGHTPLAN
      module: fixgw.plugins.flightplan
      state_file: "{CONFIG}/flightplan_state.json"   # optional; default shown
      restore_delay: 2.0
      rate_hz: 5
      turn_rate_deg_s: 3.0
      terminal_nm: 30
      approach_ramp_nm: 2.0
      alert_s: 10
      integrity_key: GPS_ACCURACY_HORIZ
      hal_nm: {enr: 2.0, term: 1.0, lnav: 0.3}
"""

import json
import os
import threading
import time

import fixgw.plugin as plugin
from fixgw.plugins.flightplan.engine import Engine, Waypoint

GPS_ACCURACY_FT_PER_NM = 6076.12


def _quality_ok(value_tuple):
    # (value, annunciate, old, bad, fail, secfail)
    return not (value_tuple[2] or value_tuple[3] or value_tuple[4])


class MainThread(threading.Thread):
    def __init__(self, parent):
        super().__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log
        cfg = parent.config

        self.engine = Engine(
            {
                "terminal_nm": cfg.get("terminal_nm", 30.0),
                "approach_ramp_nm": cfg.get("approach_ramp_nm", 2.0),
                "turn_rate_deg_s": cfg.get("turn_rate_deg_s", 3.0),
                "alert_s": cfg.get("alert_s", 10.0),
                "hal_nm": cfg.get("hal_nm", {"enr": 2.0, "term": 1.0, "lnav": 0.3}),
            }
        )

        default_path = os.path.join(cfg.get("CONFIGPATH", "."), "flightplan_state.json")
        self.state_file = cfg.get("state_file", default_path)
        self.restore_delay = float(cfg.get("restore_delay", 2.0))
        self.rate_hz = float(cfg.get("rate_hz", 5.0))
        self.min_interval = 1.0 / self.rate_hz if self.rate_hz > 0 else 0.0
        self.integrity_key = cfg.get("integrity_key", "GPS_ACCURACY_HORIZ") or None

        self._lock = threading.Lock()
        self._last_update_time = 0.0
        self._restoring = False
        self._last_persisted = None

        self.parent.db_callback_add("FPLSEQ", self._on_fplseq)
        self.parent.db_callback_add("FPLCMD", self._on_fplcmd)
        self.parent.db_callback_add("LAT", self._on_position)
        self.parent.db_callback_add("LONG", self._on_position)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------
    def _read(self, key):
        return self.parent.db_read(key)[0]

    def _read_tuple(self, key):
        return self.parent.db_read(key)

    def _setval(self, key, value, fail=False, bad=False):
        item = self.parent.db_get_item(key)
        item.value = value
        item.fail = fail
        item.bad = bad

    # ------------------------------------------------------------------
    # Route block
    # ------------------------------------------------------------------
    def _on_fplseq(self, key, value, udata=None):
        if not isinstance(value, tuple):
            return
        if self._restoring:
            return
        self._load_route_from_db()

    def _load_route_from_db(self):
        count = int(self._read("FPLCOUNT"))
        name = self._read("FPLNAME")
        seq = int(self._read("FPLSEQ"))
        waypoints = []
        for i in range(1, count + 1):
            waypoints.append(
                Waypoint(
                    id=self._read(f"FPL{i}ID"),
                    lat=self._read(f"FPL{i}LAT"),
                    lon=self._read(f"FPL{i}LON"),
                    type=int(self._read(f"FPL{i}TYPE")),
                    role=int(self._read(f"FPL{i}ROLE")),
                )
            )
        self.engine.load_route(waypoints, name, seq)

    def _republish_route(self):
        for i, wp in enumerate(self.engine.route, start=1):
            self.parent.db_write(f"FPL{i}ID", wp.id)
            self.parent.db_write(f"FPL{i}LAT", wp.lat)
            self.parent.db_write(f"FPL{i}LON", wp.lon)
            self.parent.db_write(f"FPL{i}TYPE", wp.type)
            self.parent.db_write(f"FPL{i}ROLE", wp.role)
        self.parent.db_write("FPLCOUNT", len(self.engine.route))
        self.parent.db_write("FPLNAME", self.engine.route_name)
        if self.engine.seq is not None:
            self.parent.db_write("FPLSEQ", self.engine.seq)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def _on_fplcmd(self, key, value, udata=None):
        if not isinstance(value, tuple):
            return
        cmd_str = value[0]
        if not cmd_str:
            return
        position = self._position_tuple()
        dto = self._read_dto_staging()
        ack, msg = self.engine.handle_command(cmd_str, position, dto)
        self.parent.db_write("FPLCMDACK", ack)
        if msg:
            self.parent.db_write("FPLMSG", msg)
        self._run_update_cycle()

    def _read_dto_staging(self):
        ident = self._read("DTOID")
        if not ident:
            return None
        return (ident, self._read("DTOLAT"), self._read("DTOLON"), int(self._read("DTOTYPE")))

    def _position_tuple(self):
        lat_t = self._read_tuple("LAT")
        lon_t = self._read_tuple("LONG")
        ok = _quality_ok(lat_t) and _quality_ok(lon_t)
        return (lat_t[0], lon_t[0], ok)

    # ------------------------------------------------------------------
    # Guidance cycle
    # ------------------------------------------------------------------
    def _on_position(self, key, value, udata=None):
        if not isinstance(value, tuple):
            return
        now = time.time()
        if now - self._last_update_time < self.min_interval:
            return
        self._last_update_time = now
        self._run_update_cycle(now)

    def _read_integrity(self):
        fix_t = self._read_tuple("GPS_FIX_TYPE")
        if fix_t[2]:  # old -- never actively published (e.g. X-Plane's FMS)
            fix_type_ok = None
        else:
            fix_type_ok = (fix_t[0] >= 3) and not fix_t[4]

        accuracy_nm = None
        if self.integrity_key:
            acc_t = self._read_tuple(self.integrity_key)
            if not acc_t[2]:
                accuracy_nm = acc_t[0] / GPS_ACCURACY_FT_PER_NM
        return fix_type_ok, accuracy_nm

    def _run_update_cycle(self, now=None):
        now = now if now is not None else time.time()
        lat_t = self._read_tuple("LAT")
        lon_t = self._read_tuple("LONG")
        position_ok = _quality_ok(lat_t) and _quality_ok(lon_t)
        gs = self._read("GS")
        magvar = self._read("MAGVAR")
        fix_type_ok, accuracy_nm = self._read_integrity()

        out = self.engine.update(lat_t[0], lon_t[0], gs, magvar, position_ok, fix_type_ok, accuracy_nm, now)
        self._write_outputs(out)
        self._maybe_persist()

    def _write_outputs(self, out):
        fail = out.get("fail", False)
        bad = out.get("bad", False)
        wp_ete_bad = out.get("wp_ete_bad", False)

        self._setval("FPLSTATE", out["state"])
        self._setval("FPLACTLEG", out["act_leg"])
        self._setval("FPLPHASE", out["phase"])
        self._setval("FPLAPR", out["apr"])
        self._setval("FPLINTEG", out["integ"])
        self._setval("CDISCALE", out["cdiscale"])
        self._setval("FPLCRS", out["crs"], fail=fail, bad=bad)
        self._setval("FPLXTK", out["xtk"], fail=fail, bad=bad)
        self._setval("FPLCDI", out["cdi"], fail=fail, bad=bad)
        self._setval("FPLTF", out["tf"], fail=fail, bad=bad)
        self._setval("FPLFRLAT", out["fr_lat"])
        self._setval("FPLFRLON", out["fr_lon"])
        self._setval("WPFROM", out["wp_from"])
        self._setval("WPNEXT", out["wp_next"])
        self._setval("WPLAT", out["wp_lat"])
        self._setval("WPLON", out["wp_lon"])
        self._setval("WPNAME", out["wp_name"])
        self._setval("WPDIS", out["wp_dis"], fail=fail, bad=bad)
        self._setval("WPETE", out["wp_ete"], fail=fail, bad=bad or wp_ete_bad)
        self._setval("FPLREMDIS", out["rem_dis"], fail=fail, bad=bad)
        self._setval("FPLREMETE", out["rem_ete"], fail=fail, bad=bad or wp_ete_bad)
        self._setval("FPLALERT", out["alert"])
        if out.get("msg"):
            self.parent.db_write("FPLMSG", out["msg"])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_persisted(self):
        try:
            with open(self.state_file, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            self.log.warning(f"flightplan: could not read {self.state_file}: {e}")
            return None

    def _restore(self):
        data = self._load_persisted()
        if data is None:
            return
        self.engine.restore_from_dict(data)
        self._restoring = True
        try:
            self._republish_route()
        finally:
            self._restoring = False
        self._last_persisted = self.engine.to_persisted_dict()
        self.log.info(f"flightplan: restored plan '{self.engine.route_name}' from {self.state_file}")

    def _maybe_persist(self):
        snapshot = self.engine.to_persisted_dict()
        if snapshot == self._last_persisted:
            return
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(snapshot, f)
            os.replace(tmp, self.state_file)
            self._last_persisted = snapshot
        except Exception as e:
            self.log.error(f"flightplan: could not write {self.state_file}: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self):
        deadline = time.time() + self.restore_delay
        while not self.getout and time.time() < deadline:
            time.sleep(0.05)
        if not self.getout:
            self._restore()
        while not self.getout:
            time.sleep(1.0)
            if self.getout:
                break
            self._run_update_cycle()
        self._maybe_persist()

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
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
        return {
            "Route": self.thread.engine.route_name,
            "State": self.thread.engine.mode,
            "ActiveLeg": self.thread.engine.act_leg,
        }
