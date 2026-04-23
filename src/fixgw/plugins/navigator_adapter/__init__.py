#  Copyright (c) 2026 Bill Mallard
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

"""Navigator adapter plugin — vendor-agnostic serial navigator ingest.

Configuration (connections YAML):

  navigator_adapter:
    load: NAVIGATOR
    module: fixgw.plugins.navigator_adapter
    port: /dev/ttyUSB0
    baud: 4800
    adapter: garmin_nmea          # name matching a registered ParserAdapter
    cdi_full_scale_nm: 5.0        # optional; default 5.0 nm (use 0.3 for approach)

Adding a new vendor adapter
---------------------------
1. Create ``src/fixgw/plugins/navigator_adapter/adapters/<vendor>.py``
   with a class that inherits ``ParserAdapter`` and implements
   ``name()``, ``supported_protocols()``, and ``parse_line()``.
2. Register it in the ``_ADAPTERS`` dict below.
3. Add unit tests under ``tests/plugins/navigator_adapter/``.
See ``doc/plugins/navigator_adapter.md`` for the full onboarding guide.
"""

import threading
from collections import OrderedDict

import fixgw.plugin as plugin

from .base import ParserAdapter
from .fix_map import apply_to_db, _DEFAULT_CDI_FULL_SCALE_NM
from .adapters.garmin_nmea import GarminNmeaAdapter

# Registry: adapter name → class (not instance)
_ADAPTERS: dict[str, type[ParserAdapter]] = {
    "garmin_nmea": GarminNmeaAdapter,
}


class MainThread(threading.Thread):
    def __init__(self, parent, adapter: ParserAdapter, cdi_full_scale_nm: float):
        super().__init__()
        self.daemon = True
        self.getout = False
        self.parent = parent
        self.log = parent.log
        self.adapter = adapter
        self.cdi_full_scale_nm = cdi_full_scale_nm
        self._serial = None

    def run(self):
        try:
            import serial
            self._serial = serial.Serial(
                self.parent.config["port"],
                int(self.parent.config.get("baud", 4800)),
                timeout=1.0,
            )
        except Exception as exc:
            self.log.error(
                f"navigator_adapter: cannot open {self.parent.config.get('port')}: {exc}"
            )
            return

        while not self.getout:
            try:
                raw = self._serial.readline()
            except Exception as exc:
                self.log.error(f"navigator_adapter: serial read error: {exc}")
                break

            if not raw:
                continue

            messages = self.adapter.parse_line(raw)
            for msg in messages:
                try:
                    apply_to_db(msg, self.parent.db_write, self.cdi_full_scale_nm)
                except Exception as exc:
                    self.log.debug(f"navigator_adapter: db write error: {exc}")

        if self._serial and self._serial.is_open:
            self._serial.close()

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)

        adapter_name = config.get("adapter", "")
        adapter_cls = _ADAPTERS.get(adapter_name)
        if adapter_cls is None:
            known = ", ".join(_ADAPTERS)
            raise ValueError(
                f"navigator_adapter: unknown adapter '{adapter_name}'. "
                f"Known adapters: {known}"
            )
        self._adapter = adapter_cls()
        cdi_scale = float(config.get("cdi_full_scale_nm", _DEFAULT_CDI_FULL_SCALE_NM))
        self._thread = MainThread(self, self._adapter, cdi_scale)

    def run(self):
        self._thread.start()

    def stop(self):
        self._thread.stop()
        if self._thread.is_alive():
            self._thread.join(2.0)
        if self._thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        health = self._adapter.health()
        return OrderedDict({
            "Adapter": self._adapter.name(),
            "Parse Errors": health.parse_errors,
            "Stale": health.stale,
            "Last Sentence": health.last_sentence_type or "none",
        })
