====================
CANaerospace Plugin
====================

A listen-only input bridge that decodes CANaerospace V1.7 frames from a
CAN interface and writes mapped values into the FIX database.  The
motivating device is the Rotax 912iS/915iS engine ECU, which broadcasts
engine data as CANaerospace — this plugin gives any MakerPlane panel
native Rotax engine instruments with no extra hardware beyond a CAN
transceiver.

CAN-FIX remains the system's native bus protocol.  CANaerospace is
consumed here as a *federated input*: the plugin absorbs the protocol's
self-describing message format once, at the gateway, so no other part of
the stack has to speak it.  The plugin never transmits — not even Node
Service replies — so its presence on an engine bus has zero bus impact.

Requirements
------------

Only ``python-can`` (already a fix-gateway dependency).  The decode layer
is implemented in the plugin itself; no third-party CANaerospace library
is used.

Configuration
-------------

::

  # CANaerospace input bridge (e.g. Rotax 912iS / 915iS ECU)
  canaerospace:
    load: yes
    module: fixgw.plugins.canaerospace
    # python-can options. Bit rate is an interface-level concern:
    # for socketcan set it with `ip link set can0 up type can bitrate ...`;
    # for serial/slcan adapters see the python-can docs.
    interface: socketcan
    channel: can0
    mapfile: "{CONFIG}/canaerospace/rotax_is_map.yaml"
    # Seconds without a frame from a higher-priority node before a
    # lower-priority node's data is accepted (dual-lane failover).
    node_timeout: 2.0

Mapfile
-------

The mapfile is the final authority on which CAN identifiers are decoded:
any mapped canid in 0-2031 is accepted; the standard channel table
(Emergency Event 0-127, Node Service 128-199/2000-2031, User-Defined
200-299/1800-1899, Normal Operation 300-1799, Debug 1900-1999) only
drives the default handling of *unmapped* traffic.

::

  ignore_fixid_missing: false
  strict_types: false        # true drops frames whose wire type differs
                             # from the mapfile's 'type' expectation

  inputs:
    - canid: 0x18C          # required, 0..2031
      fixid: TACH1          # required, validated against the database
      type: USHORT          # optional expectation check (see below)
      element: 0            # optional, default 0; index into
                            # multi-element types (SHORT2, UCHAR4, ...)
      scale: 1.0            # optional; value*scale + offset
      offset: 0.0
      converter: none       # optional, applied AFTER scale/offset:
                            #   k2c, c2f, kpa2inhg, kpa2psi,
                            #   mps2knots, m2ft, pct (0..1 -> 0..100)
      nodes: [16, 17]       # optional; allowed sender node-ids in
                            # priority order. Absent = accept any sender.

  events:                   # optional; Emergency Event channel (0..127)
    - canid: 0x00
      fixid: BTN1           # latched true when the event is seen
      match_code: null      # optional; fire only when the 4-byte payload
                            # equals this value

Behavior notes:

* **The wire's type byte is authoritative for decoding** (CANaerospace is
  self-describing).  The mapfile ``type`` is an expectation check: a
  mismatch increments the *Type Mismatches* counter and is still decoded,
  unless ``strict_types: true``.  A device firmware update that changes a
  parameter's type stays visible without silently corrupting values.
* **Source-node priority** (``nodes:``): the Rotax dual-lane ECU can
  broadcast the same parameters from both lanes.  A frame from a listed
  node is written only when every higher-priority node has been quiet for
  ``node_timeout`` seconds; senders not in the list are ignored.  This
  prevents lane flip-flop on the panel.  There is no "lane failed"
  annunciation — the FIX database ``tol`` mechanism already marks the
  value *old* when both lanes go quiet.
* **ERROR frames** mark the mapped fixid failed until the next good
  value.  **NODATA** frames are counted and written nowhere.
* **Emergency Event Data** (canid 0-127) is always logged at WARNING with
  node-id and payload.  An ``events:`` entry latches ``True`` onto its
  fixid; there is no auto-reset, because the sender's event is
  edge-triggered — reset is the job of pilot/screen logic.
* Message Code continuity is tracked per (canid, node) as a bus-health
  telltale (*Sequence Gaps* in the plugin status); it never gates
  decoding.

Rotax iS bench verification
---------------------------

The shipped ``config/canaerospace/rotax_is_map.yaml`` is a skeleton with
**every entry commented out**: the Rotax CAN identifiers are not
published in this repository and none were invented.  To fill it in:

1. Get the broadcast frame list (and the bus bit rate) from the BRP-Rotax
   Installation Manual for the 912 iS / 915 iS, CAN bus appendix.
2. Bench-capture from a real engine or ECU (``candump -ta can0``) and
   correlate values against the cockpit display while varying RPM and
   temperatures.
3. Cross-check against open-source decoders (Kanardia / MGL protocol
   notes).

Uncomment an entry only after it passes a bench check.

Simulator
---------

``fixgw.tools.canas_sim`` sends scripted CANaerospace frames onto any
python-can interface for hardware-free development and desk demos::

  # default demo script on a virtual bus
  python -m fixgw.tools.canas_sim --interface virtual --channel tcan0

  # custom script, 20 cycles/second
  python -m fixgw.tools.canas_sim --script myscript.yaml --rate 20

  # random hostile traffic (soak test)
  python -m fixgw.tools.canas_sim --fuzz --duration 60

Script format::

  frames:
    - canid: 0x12C
      node: 16
      type: USHORT
      values: [2650]
