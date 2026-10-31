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

# Tests for the python-can driver the plugin hands to dronecan's Node.

import threading
import time

import can
import dronecan
import pytest
from dronecan.driver.common import CANFrame, DriverError

from fixgw.plugins.dronecan.driver import PythonCANDriver


@pytest.fixture
def pair():
    drv = PythonCANDriver("virtual", "tdc_driver", bitrate=1000000)
    peer = can.Bus("tdc_driver", interface="virtual")
    yield drv, peer
    drv.close()
    peer.shutdown()


def test_receive_timeout_is_seconds(pair):
    # dronecan 1.0.27's own PythonCAN driver multiplies by 1000 here, which
    # turns node.spin(0.5) on a quiet bus into a 500 s block.
    # Run it on a daemon thread so a regression fails here, fast, instead of
    # hanging the test session.
    drv, _ = pair
    result = []
    t = threading.Thread(target=lambda: result.append(drv.receive(0.2)), daemon=True)
    start = time.monotonic()
    t.start()
    t.join(2.0)
    assert not t.is_alive(), "receive(0.2) blocked for more than 2 s"
    assert result == [None]
    assert time.monotonic() - start < 1.0


def test_spin_on_quiet_bus_returns_on_time(pair):
    drv, _ = pair
    node = dronecan.node.Node(drv)
    t = threading.Thread(target=lambda: node.spin(0.3), daemon=True)
    t.start()
    t.join(2.0)
    assert not t.is_alive(), "node.spin(0.3) blocked for more than 2 s"


def test_extended_frame_passes(pair):
    drv, peer = pair
    peer.send(
        can.Message(arbitration_id=0x1803EA2A, data=b"\x01\x02", is_extended_id=True)
    )
    frame = drv.receive(1.0)
    assert isinstance(frame, CANFrame)
    assert frame.id == 0x1803EA2A
    assert frame.extended is True
    assert bytes(frame.data) == b"\x01\x02"
    assert drv.frame_count == 1
    assert drv.foreign_count == 0


def test_standard_error_and_remote_frames_are_foreign(pair):
    drv, peer = pair
    peer.send(can.Message(arbitration_id=0x12C, data=b"\x10\x07", is_extended_id=False))
    peer.send(
        can.Message(arbitration_id=0x1803EA2A, is_extended_id=True, is_error_frame=True)
    )
    peer.send(
        can.Message(
            arbitration_id=0x1803EA2A, is_extended_id=True, is_remote_frame=True
        )
    )
    for _ in range(3):
        assert drv.receive(1.0) is None
    assert drv.frame_count == 3
    assert drv.foreign_count == 3


def test_listen_only_refuses_to_send(pair):
    drv, peer = pair
    with pytest.raises(DriverError, match="listen-only"):
        drv.send(0x1803EA2A, b"\x00", extended=True)
    assert peer.recv(0.2) is None


def test_send_when_not_listen_only():
    drv = PythonCANDriver("virtual", "tdc_driver_tx", listen_only=False)
    peer = can.Bus("tdc_driver_tx", interface="virtual")
    try:
        drv.send(0x1801552A, b"\x01\x02\x03", extended=True)
        msg = peer.recv(1.0)
        assert msg.arbitration_id == 0x1801552A
        assert msg.is_extended_id
        assert bytes(msg.data) == b"\x01\x02\x03"
    finally:
        drv.close()
        peer.shutdown()


def test_io_hooks_see_received_frames(pair):
    drv, peer = pair
    seen = []
    drv.add_io_hook(lambda direction, frame: seen.append((direction, frame.id)))
    peer.send(can.Message(arbitration_id=0x1803EA2A, data=b"", is_extended_id=True))
    drv.receive(1.0)
    assert seen == [(drv.FRAME_DIRECTION_INCOMING, 0x1803EA2A)]
