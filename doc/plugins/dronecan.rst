================
DroneCAN Plugin
================

An input bridge that subscribes to DroneCAN (UAVCAN v0) broadcasts and
writes mapped values into the FIX database.  The motivating hardware is
commodity drone-ecosystem sensors: above all an inexpensive external
DroneCAN magnetometer, plus GNSS pucks, air-data probes and power
monitors from the ArduPilot/PX4 ecosystem.

CAN-FIX remains the system's native bus protocol.  DroneCAN is consumed
here as a *federated input* -- bridge, don't migrate: the plugin harvests
the sensors, not the protocol, and nothing past this plugin ever sees a
DroneCAN type.

**This plugin needs its own CAN interface** (or a separate SLCAN USB
adapter).  Never put DroneCAN and CAN-FIX on one physical bus: their bit
rates and identifier spaces collide.

Requirements
------------

``dronecan`` (PyPI, MIT licence; pinned to the tested 1.0.27 in
``pyproject.toml``) and ``python-can``, both fix-gateway dependencies.
The library does all frame, multi-frame transfer and DSDL decoding.

The plugin hands the library its own python-can driver instead of the
library's built-in one.  In dronecan 1.0.27 the built-in python-can
driver passes ``timeout * 1000`` to ``recv()`` (which takes seconds), so
on a quiet bus the receive loop blocks for minutes and the plugin cannot
be stopped.  It also reports 11-bit frames as 29-bit.  The plugin's
driver takes seconds and drops 11-bit, error and remote frames before
the decoder sees them.

Bus posture
-----------

With ``node_id`` unset (the default) the local node is **anonymous and
listen-only**: it receives every broadcast and transmits nothing, and the
driver refuses any transmit as a second guard.

Setting ``node_id`` makes the node a normal DroneCAN participant: it
publishes its own ``uavcan.protocol.NodeStatus`` once a second, as every
DroneCAN node must, and can run the dynamic node-ID allocator.  No flight
data is ever transmitted, and parameter, file and firmware services are
not implemented.  Sensor configuration stays with vendor tools or the
DroneCAN GUI tool.

Configuration
-------------

::

  dronecan:
    load: yes
    module: fixgw.plugins.dronecan
    # python-can options.  SocketCAN: interface socketcan, channel can1
    # (set the bit rate with `ip link set can1 up type can bitrate 1000000`).
    # SLCAN USB adapter: interface slcan, channel /dev/ttyACM0 (or COM5).
    interface: socketcan
    channel: can1
    bitrate: 1000000        # DroneCAN convention; 500k/250k also common
    node_id: null           # null = anonymous, listen-only
    allocator: false        # needs node_id
    allocator_db: "{CONFIG}/dronecan/allocation.db"
    mapfile: "{CONFIG}/dronecan/map.yaml"

``allocator: true`` with ``node_id: null`` is rejected at startup.

Dynamic node-ID allocation
--------------------------

Every node on a DroneCAN bus needs a node id (1-127).  Many sensors ship
without one and, at power-up, ask the bus for one: they broadcast
anonymous allocation requests carrying their 128-bit unique id and wait
for an *allocator* (normally the flight controller) to answer.  Until
answered they publish nothing.

With ``allocator: true`` (and a ``node_id`` for the gateway itself) the
plugin runs the dronecan library's centralized allocation server.  Each
sensor gets a free id, and the unique-id-to-node-id table is persisted in
``allocator_db`` so the same sensor gets the same id on every boot.
Allocations are logged at INFO by the library.

Mapfile
-------

::

  ignore_fixid_missing: false

  inputs:
    - message: uavcan.equipment.ahrs.MagneticFieldStrength2
      source_node: null          # optional 1-127: only accept this sender
      where:                     # optional field-equality guards
        sensor_id: 0
      fields:
        - path: magnetic_field_ga[0]
          fixid: MAGX
        - { path: "magnetic_field_ga[1]", fixid: MAGY }
        - { path: "magnetic_field_ga[2]", fixid: MAGZ }

    - message: uavcan.equipment.air_data.StaticTemperature
      fields:
        - { path: static_temperature, fixid: OAT, converter: k2c }

- ``message`` is a full DSDL type name, resolved against the installed
  library at load time.  An unknown type is a load-time error.
- ``path`` is dotted field names and integer indices
  (``foo.bar[2].baz``).  Paths are checked against the DSDL schema at
  load time: a misspelt field, an index on a non-array or past a fixed
  array's size, or a path that does not end at a number is a load-time
  error, even with ``ignore_fixid_missing``.
- Several inputs may name the same message (two magnetometers told apart
  by ``where: {sensor_id: ...}`` or ``source_node``).  A ``where`` or
  ``source_node`` mismatch counts as *Filtered*, not as an error.
- ``scale``/``offset`` are applied first, then ``converter`` -- the same
  converter names as the CANaerospace plugin: ``none``, ``k2c``, ``c2f``,
  ``kpa2inhg``, ``kpa2psi``, ``mps2knots``, ``m2ft``, ``pct``.

The shipped ``config/dronecan/map.yaml`` maps sensor 0 of
``MagneticFieldStrength2`` to ``MAGX``/``MAGY``/``MAGZ``: raw field in
gauss, uncalibrated, in the frame the sensor reports, written by this plugin
only.  The other examples are commented out: the power and temperature keys
may already have another writer (one key, one writer), and the GNSS example
is not bench-checked.  All of the examples' field paths are validated
against the library's DSDL by the test suite.

Data quality
------------

- **Non-finite values.**  DroneCAN senders use NaN for "unknown".  A NaN
  or infinite field never reaches the database as a number: the key is
  flagged *fail* and keeps its last good value.  The next finite reading
  clears the flag.
- **Sender health.**  Every DroneCAN node broadcasts ``NodeStatus``.  A
  value from a node whose last reported health is ERROR or CRITICAL is
  written with the *bad* flag set; WARNING is logged but does not flag
  data.  A node whose ``NodeStatus`` has not been seen yet is not presumed
  bad.
- **Staleness** is left to each key's FIX ``tol``.
- **Runtime path errors** (a sender on a different DSDL revision, or a
  variable-length array shorter than the mapped index) are counted as
  *Path Errors* and warned once per message and path.

Malformed transfers (CRC mismatch, broken multi-frame sequence) and
message types the library has no DSDL for (vendor messages) are counted as
*Transfer Errors* and never stop the receive loop.  Repeated driver errors
(bus off, adapter unplugged) close and re-create the node after a short
back-off; those are counted as *Spin Errors* and *Reopens*.

Status
------

``get_status()`` reports the interface, channel, bit rate, node id (or
``anonymous``), allocator on/off, and the counters Frames Received,
Foreign Frames, Transfers Received, Values Written, Filtered, Path Errors,
Non-finite Values, Rejected Values, Handler Errors, Transfer Errors, Spin
Errors and Reopens, plus a ``Nodes`` table: for each node id, its health,
mode, uptime and seconds since its last ``NodeStatus``.

Simulator
---------

``fixgw.tools.dronecan_sim`` sends DroneCAN broadcasts onto any python-can
interface, encoded by the library's own transport (multi-frame transfers,
CRC and transfer ids included), so the plugin can be developed with no
hardware::

  # plugin configured with interface: virtual, channel: tdc0
  python -m fixgw.tools.dronecan_sim --interface virtual --channel tdc0
  python -m fixgw.tools.dronecan_sim --health error       # flags data bad
  python -m fixgw.tools.dronecan_sim --script myscript.yaml --rate 20
  python -m fixgw.tools.dronecan_sim --fuzz --duration 60 # hostile traffic

The built-in demo is a magnetometer (node 42) whose field vector turns
through 360 degrees of heading every 36 s, and a power monitor (node 43)
sending ``CircuitStatus`` and ``StaticTemperature``.  Every simulated node
also sends ``NodeStatus`` once a second.  Script files list messages::

  messages:
    - message: uavcan.equipment.ahrs.MagneticFieldStrength2
      node: 42
      fields:
        sensor_id: 0
        magnetic_field_ga: [0.21, -0.05, 0.40]

Bench check (manual, not CI)
----------------------------

1. Put the magnetometer on its own bus: ``can1`` at 1 Mbps
   (``sudo ip link set can1 up type can bitrate 1000000``) or an SLCAN
   adapter.  Confirm traffic with ``candump can1``; 29-bit ids should
   appear within a second of power-up if the unit has a node id.
2. Factory-fresh unit (no traffic, or only anonymous allocation requests):
   set ``node_id: 100`` and ``allocator: true``, restart fix-gateway, and
   watch the log for the allocation and the ``Nodes`` table for the new
   node.
3. Restart with the shipped mapfile (if the sensor publishes a
   ``sensor_id`` other than 0, change the ``where`` guard) and rotate the
   sensor: ``fixgwc`` should show
   ``MAGX``/``MAGY``/``MAGZ`` moving, the horizontal magnitude staying
   roughly constant through a level 360.
4. Leave an unrelated DroneCAN device (an ESC, a GNSS) chattering on the
   same bus for a few minutes; the counters should tell the story and
   *Reopens* should stay 0.

Follow-ups
----------

- A compute stage turning ``MAGX``/``MAGY``/``MAGZ`` plus ``PITCH``/``ROLL``
  into tilt-compensated magnetic heading (``HEAD``), with hard- and
  soft-iron calibration.  It also owns ``MAGW``.  Until then the raw
  vector is present but unconsumed.
- Latched annunciator keys for sensor health (the CANaerospace emergency
  event posture).
- An AGL key for ``uavcan.equipment.range_sensor.Measurement`` when a
  rangefinder reaches the bench.
