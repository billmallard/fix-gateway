#  Copyright (c) 2026 MakerPlane contributors
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

import pytest

# Magnetometer keys as proposed in the DroneCAN-in spec (section 8.1).  They
# are NOT in the shipped FIX database: minting them is a FIX-contract change
# awaiting ratification.  Tests add them to their private database instance
# only, and only if the shipped database does not already define them.
PROVISIONAL_MAG_KEYS = ["MAGX", "MAGY", "MAGZ"]


@pytest.fixture
def mag_keys(database):
    for key in PROVISIONAL_MAG_KEYS:
        if key not in database.listkeys():
            database.add_item(
                {
                    "key": key,
                    "description": "Raw magnetic field (test-only, provisional)",
                    "type": "float",
                    "min": -10.0,
                    "max": 10.0,
                    "units": "gauss",
                    "initial": 0.0,
                    "tol": 2000,
                }
            )
    return database
