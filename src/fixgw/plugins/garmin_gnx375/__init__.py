#!/usr/bin/env python

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

"""FIX-Gateway plugin for the Garmin GNX 375 (and compatible Garmin GPS navigators).

A config alias for ``navigator_adapter`` with ``adapter: garmin_nmea``, kept so
existing installs that load this module keep working (SRC6, Bill's ruling on
AER-6409). It has no parser of its own: the adapter's Garmin NMEA parser reads
the sentences and its ``fix_map`` writes the keys. New installs should load
``navigator_adapter`` instead. The one difference is the default baud, 9600 here
(Aviation Output 1) and 4800 there.

Reads NMEA 0183 sentences from the GNX 375 RS-232 serial port (Aviation Output 1
format, 9600 8N1 by default) and writes navigation state into the FIX-Gateway
database.

GNX 375 serial port configuration (set via unit installer menu):
  RS-232/RS-422 Port 1 or Port 2 → "Aviation Output 1"
  Baud: 9600 (fixed for Aviation Output 1)

NMEA sentences consumed
-----------------------
  $GPRMC  position, ground speed, track
  $GPGGA  position, GPS altitude, fix quality, satellites in use
  $GPGSA  2D/3D fix mode (any talker, e.g. $GNGSA)
  $GPRMB  cross-track error (XTE)
  $GPAPB  desired track (bearing origin to destination), perpendicular passed

FIX-Gateway keys written
------------------------
  LAT                 latitude (deg, decimal)
  LONG                longitude (deg, decimal)
  GS                  ground speed (knots)
  TRACK               track over ground (deg true)
  TALT                GPS altitude above MSL (feet), GGA field 9
  GPS_FIX_TYPE        contract scale: 0-1 no fix, 2 2D, 3 3D, 4 3D DGPS/WAAS,
                      5 3D RTK. Mapped from GGA quality and GSA mode by
                      fixgw.nmea_fix.gps_fix_type, written once per GGA.
  EXTXTK              signed cross-track error (nm), positive = right of track
  EXTCDI              course deviation [-1.0, +1.0], -XTE / cdi_full_scale_nm
  EXTCRS              desired track of the active leg (deg magnetic)
  EXTTF               TO/FROM: 0 off (APB not valid), 1 TO, 2 FROM

The GNX is an external navigator: it feeds the EXT* inputs, and compute's
GPSSRC select routes them into GPSCRS/GPSCDI/GPSTF and its NAVSRC select into
COURSE/CDI/TOFROM, and its GPSSRC select of EXTXTK into XTRACK. It does not
write COURSE, CDI or XTRACK, which compute owns (AER-4473).

Sign convention for EXTXTK (the FPLXTK/XTRACK convention):
  Negative → aircraft is LEFT of desired course (need to steer right)
  Positive → aircraft is RIGHT of desired course (need to steer left)

In $GPRMB the steer-direction flag encodes this as:
  'L' (steer left to return) → aircraft is RIGHT of course → XTE positive
  'R' (steer right to return) → aircraft is LEFT of course  → XTE negative
"""

from fixgw.plugins.navigator_adapter import Plugin as NavigatorPlugin

_DEFAULT_BAUD = 9600
_ADAPTER = "garmin_nmea"


class Plugin(NavigatorPlugin):
    def __init__(self, name, config, config_meta):
        config = dict(config)
        config.setdefault("baud", _DEFAULT_BAUD)
        # This module is the Garmin parser by name; another adapter is a
        # navigator_adapter config, not this one.
        config["adapter"] = _ADAPTER
        super().__init__(name, config, config_meta)
