#  Copyright (c) 2026 MakerPlane
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

#  Reads the OnSpeed Gen2 AOA computer's 10 Hz ONSPEED serial sentence and
#  publishes AOA and AOA.Warn.  Listen-only.  Every other field is decoded
#  for get_status() and not written: one key, one writer.  The behaviour is
#  pyEfis docs/fixgw_onspeed_in_spec.md; section numbers below refer to it.
