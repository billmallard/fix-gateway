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

# AvionicsDuino CAN message layouts.  AvionicsDuino (avionicsduino.com,
# GitHub bfrmd/*) puts raw little-endian memory copies of floats, doubles
# and integers on an 11-bit, 500 kbit/s bus.  There is no header, no node
# id and no data-type byte: the CAN id alone names the layout.
#
# The layouts below are facts read from the senders' source at the
# commits recorded in config/avionicsduino/ahrs21_rm3_rdam12.yaml, and
# decoded independently here; no AvionicsDuino code is copied.  The ids
# are version-locked upstream, so this table describes one version set:
# AHRS V2.1, remote module V3, RDAM V1.2.

import struct
from collections import namedtuple

MAX_CANID = 0x7FF

Layout = namedtuple("Layout", ["name", "node", "fmt", "fields", "rate_hz"])

# canid -> Layout.  fmt is a struct format, always little-endian with no
# padding ("<").  rate_hz is the sender's nominal rate, or None where the
# sender's source was not read.  A frame may carry more bytes than fmt
# needs (ID 58 is sent with DLC 8 and fills only bytes 0-3); it may not
# carry fewer.
LAYOUTS = {
    # AHRS V2.1: AHRS_AvionicsDuino_V2.1.ino
    # uint64 counter, incremented after every 20 Hz send (a loss telltale).
    10: Layout("counter", "ahrs", "<Q", ("count",), 20),
    # roll in RADIANS, pitch in DEGREES, both unfiltered.
    20: Layout("roll_pitch", "ahrs", "<ff", ("roll_rad", "pitch_deg"), 20),
    # accY = -sensor y, accZ = -sensor z (sensor axes: x fwd, y left, z
    # up), so body y right and z down, m/s^2, unfiltered.  accZ reads
    # about -9.8 in level flight.
    22: Layout("acc", "ahrs", "<ff", ("acc_y", "acc_z"), 20),
    # vz m/s (the header says positive up; see the mapfile), track deg
    # -180..+180 from the EKF.
    24: Layout("vz_track", "ahrs", "<ff", ("vz_mps", "track_deg"), 10),
    # GNSS altitude AMSL in feet, ground speed in km/h.
    30: Layout("gnss_alt_gs", "ahrs", "<ff", ("alt_msl_ft", "gs_kmh"), 10),
    # UTC date and time, then satellites in view.
    34: Layout(
        "date_time_sats",
        "ahrs",
        "<HBBBBBB",
        ("year", "month", "day", "hour", "minute", "second", "sats"),
        5,
    ),
    36: Layout("latitude", "ahrs", "<d", ("lat_deg",), 5),
    37: Layout("longitude", "ahrs", "<d", ("lon_deg",), 5),
    # UTC nanoseconds (declared uint32 by the sender, read int32 by their
    # EFIS; u-blox reports it signed) and time accuracy in ns.
    38: Layout("ns_time_accuracy", "ahrs", "<iI", ("nanosec", "time_acc_ns"), 5),
    # Remote module V3: EFIS_Remote_Module_AvionicsDuino_V3.ino.
    # 28, 42, 44 and 45 are sent with DLC 4.
    # Magnetic heading deg 0..360, tilt-compensated and IIR-filtered.
    28: Layout("mag_heading", "remote", "<f", ("heading_deg",), 10),
    # OAT degC, IIR-filtered.
    42: Layout("oat", "remote", "<f", ("oat_c",), 5),
    # Raw magnetometer axes in uT, sensor frame, before calibration.
    43: Layout("mag_xy", "remote", "<ff", ("magx_ut", "magy_ut"), 5),
    44: Layout("humidity", "remote", "<f", ("rh_pct",), 5),
    45: Layout("mag_z", "remote", "<f", ("magz_ut",), 5),
    # RDAM V1.2: RDAMcanBus.cpp, RDAMsensors.cpp, RDAMglobals.h.
    # QNH in 0.01 inHg (whatever the RDAM's display last sent, 0 with no
    # display), altitude at that QNH in ft, density altitude in ft, baro
    # vertical speed in ft/min rounded to 10.
    46: Layout(
        "qnh_alt_vario",
        "rdam",
        "<hhhh",
        ("qnh_cinhg", "qnh_alt_ft", "density_alt_ft", "baro_vs_fpm"),
        5,
    ),
    # Unfiltered IAS and TAS in whole km/h, wind speed km/h, wind direction
    # deg.
    48: Layout(
        "speeds_wind",
        "rdam",
        "<hhhh",
        ("ias_kmh", "tas_kmh", "wind_kmh", "wind_dir_deg"),
        5,
    ),
    # Engine channels.  DLC 4 for 50; 58 is sent with DLC 8 but only bytes
    # 0-3 are ever filled.
    50: Layout("manifold", "rdam", "<f", ("map",), 5),
    52: Layout("cht", "rdam", "<ff", ("cht2", "cht3"), 5),
    54: Layout("oil", "rdam", "<ff", ("oil_temp", "oil_press"), 5),
    56: Layout("egt", "rdam", "<ff", ("egt3", "egt4"), 5),
    58: Layout("afr", "rdam", "<f", ("afr",), 5),
    60: Layout("currents", "rdam", "<ff", ("bus_current_a", "battery_current_a"), 5),
    # EMS node.  Its source was not read: these layouts are how the RDAM
    # V1.2 receives them (RDAMcanBus.cpp), so the rate is unknown.
    26: Layout(
        "ems_rpm_vbat",
        "ems",
        "<BBHf",
        ("lower_limit", "upper_limit", "rpm", "vbat"),
        None,
    ),
    32: Layout("ems_fuel", "ems", "<ff", ("fuel_flow", "fuel_level"), None),
    33: Layout("ems_time_fuel", "ems", "<Hf", ("total_seconds", "fuel_used"), None),
}

class DecodeError(ValueError):
    pass


def size(canid):
    return struct.calcsize(LAYOUTS[canid].fmt)


def decode(canid, data):
    """Decode one frame's payload into {field: value}.

    Raises KeyError for an id with no layout and DecodeError for a payload
    shorter than its layout.
    """
    layout = LAYOUTS[canid]
    need = struct.calcsize(layout.fmt)
    data = bytes(data)
    if len(data) < need:
        raise DecodeError(
            "canid {} needs {} bytes, got {}".format(canid, need, len(data))
        )
    return dict(zip(layout.fields, struct.unpack_from(layout.fmt, data)))


def encode(canid, values):
    """Build a payload from {field: value}, the inverse of decode().

    For the simulator and tests.  Payloads are exactly the layout's size,
    except ID 58, which the RDAM sends as 8 bytes.
    """
    layout = LAYOUTS[canid]
    data = struct.pack(layout.fmt, *(values[f] for f in layout.fields))
    if canid == 58:
        data += bytes(8 - len(data))
    return data
