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

# A python-can backed CAN driver for the dronecan library's Node.
#
# dronecan.make_node() picks its own PythonCAN driver for any interface
# name whenever python-can is importable (always, for fixgw).  As of
# dronecan 1.0.27 that driver has two defects that matter here:
#
#   * receive(timeout) passes timeout * 1000 to python-can's recv(), which
#     takes seconds, so node.spin(0.5) on a quiet bus blocks for 500 s and
#     the plugin can never be stopped;
#   * it reports every received frame as 29-bit, so 11-bit traffic on the
#     bus is fed to the DroneCAN transfer decoder.
#
# This driver keeps the same AbstractDriver contract, takes seconds, drops
# 11-bit, error and remote frames before the decoder sees them, and refuses
# to transmit unless the plugin was configured with a node id.

import time

import can
from dronecan.driver.common import AbstractDriver, CANFrame, DriverError


class PythonCANDriver(AbstractDriver):
    def __init__(self, interface, channel, bitrate=None, listen_only=True, bus=None):
        super(PythonCANDriver, self).__init__()
        self.listen_only = listen_only
        self.frame_count = 0
        self.foreign_count = 0
        if bus is not None:
            self._bus = bus
        else:
            kwargs = {}
            if bitrate is not None:
                kwargs["bitrate"] = bitrate
            self._bus = can.Bus(channel, interface=interface, **kwargs)

    def receive(self, timeout=None):
        msg = self._bus.recv(timeout)
        if msg is None:
            return None
        self.frame_count += 1
        if (
            not msg.is_extended_id
            or getattr(msg, "is_error_frame", False)
            or getattr(msg, "is_remote_frame", False)
        ):
            # Not DroneCAN: CAN-FIX, CANaerospace or other 11-bit traffic.
            self.foreign_count += 1
            return None
        frame = CANFrame(
            msg.arbitration_id,
            bytearray(msg.data),
            True,
            ts_monotonic=time.monotonic(),
            ts_real=msg.timestamp or time.time(),
            canfd=bool(getattr(msg, "is_fd", False)),
        )
        self._rx_hook(frame)
        return frame

    def send_frame(self, frame):
        if self.listen_only:
            raise DriverError(
                "dronecan plugin is listen-only (no node_id configured); "
                "refusing to transmit frame 0x{:08X}".format(frame.id)
            )
        self._bus.send(
            can.Message(
                arbitration_id=frame.id,
                data=bytes(frame.data),
                is_extended_id=frame.extended,
                is_fd=frame.canfd,
            )
        )
        self._tx_hook(frame)

    def close(self):
        self._bus.shutdown()
