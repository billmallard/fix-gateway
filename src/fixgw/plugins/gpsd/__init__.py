#!/usr/bin/env python

#  Copyright (c) 2024 Janne Mäntyharju
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

#  This plugin pulls the location information from GPSD

import json
import threading
from collections import OrderedDict

import gpsd2
import time
import fixgw.plugin as plugin

# Poll period. gpsd answers a ?POLL at once, so without a pause the thread
# spins on the socket (AER-6406).
POLL_INTERVAL = 0.2

M_TO_FT = 3.28084
MS_TO_KT = 1.94384


def poll():
    """Poll gpsd; return the parsed response and the raw TPV it came from.

    This is gpsd2.get_current() keeping the TPV. py-gpsd2 0.1.0 fills every
    absent TPV field with 0, so the parsed response cannot tell a missing epx
    from a perfect fix; only the raw TPV can (AER-6406).
    """
    gpsd2.gpsd_stream.write("?POLL;\n")
    gpsd2.gpsd_stream.flush()
    response = json.loads(gpsd2.gpsd_stream.readline())
    if response["class"] != "POLL":
        raise Exception(
            "Unexpected message received from gps: {}".format(response["class"])
        )
    return gpsd2.GpsResponse.from_json(response), response["tpv"][-1]


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        self.getout = False  # indicator for when to stop
        self.parent = parent  # parent plugin object
        self.log = parent.log  # simplifies logging

    def run(self):
        try:
            gpsd2.connect()
        except:
            self.parent.log.error("Can't connect to gpsd")
            return

        while not self.getout:
            p = None
            try:
                p, tpv = poll()
            except UserWarning as e:
                self.parent.log.warning(f"gpsd warning: {e}")
                time.sleep(5)
                continue

            self.parent.db_write("GPS_SATS_VISIBLE", p.sats)
            self.parent.db_write("GPS_FIX_TYPE", p.mode)

            # A field gpsd left out writes nothing, so its key goes old by
            # its tol instead of publishing the library's 0 as a value.
            if p.mode >= 2:
                self._write(tpv, "lat", "LAT")
                self._write(tpv, "lon", "LONG")
                self._write(tpv, "track", "TRACK")
                self._write(tpv, "speed", "GS", MS_TO_KT)
                self._write(tpv, "epx", "GPS_ACCURACY_HORIZ", M_TO_FT)
            if p.mode >= 3:
                self._write(tpv, "alt", "GPS_ELLIPSOID_ALT", M_TO_FT)
                self._write(tpv, "epv", "GPS_ACCURACY_VERTICAL", M_TO_FT)
                self._write(tpv, "eps", "GPS_ACCURACY_SPEED", MS_TO_KT)
            time.sleep(POLL_INTERVAL)

    def _write(self, tpv, field, key, scale=1):
        if field in tpv:
            self.parent.db_write(key, tpv[field] * scale)

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.thread = MainThread(self)
        self.status = OrderedDict()

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return self.status
