===================================
X-Plane Flight Simulator Plugin
===================================

This plugin connects FIX-Gateway to the
`X-Plane <https://www.x-plane.com>`_ flight simulator over UDP.  It lets
X-Plane act as a data *source* -- so a pyEfis display (or any other FIX client)
can be developed and exercised against the simulator -- and as a data *sink*,
so the EFIS can write selected values back into X-Plane.  The headline use of
the write path is **nav-source follow**: when the pilot selects GPS / NAV1 /
NAV2 on the EFIS, the plugin tells X-Plane which source to drive its HSI from,
and X-Plane's own autopilot follows the EFIS (see `Nav-source selection and
autopilot follow`_ below).

The plugin is **data-driven**.  The mapping between X-Plane's data and FIX
database keys is declared entirely in the plugin's configuration block -- new
values are wired up by editing YAML, with no code changes.  The shipped
``connections/xplane.yaml`` is heavily commented and is the best line-by-line
reference; this page explains the concepts and each configuration section.

Data paths
==========

Data moves in four ways, each its own configuration section:

* ``recv`` -- *X-Plane to FIX* via X-Plane's indexed *Data Output* UDP stream.
* ``datarefs`` -- *X-Plane to FIX* via named datarefs requested with the
  ``RREF`` protocol (for values the indexed Data Output rows do not carry).
* ``dataref_writes`` -- *FIX to X-Plane* via named-dataref ``DREF`` writes,
  transmitted on change.
* ``send`` -- *FIX to X-Plane* via the indexed Data Output control stream
  (disabled by default; see the warning below).

Setting up X-Plane
==================

In X-Plane open **Settings -> Data Output** (named *Net Connections* in older
versions) and:

#. Enable **Send network data output** to the IP of the machine running
   FIX-Gateway, at the port you set as ``udp_in`` below.
#. Check the *Data Set* rows you intend to map.  The index numbers used in the
   ``recv`` section correspond to these rows, and the eight columns of each row
   map to the eight slots of that index.
#. In **Settings -> Network -> UDP Ports**, note the **legacy** receive port
   (default ``49000``).  FIX-Gateway sends control writes and both ``RREF`` and
   ``DREF`` dataref traffic there as ``udp_out``.  The newer "Port we receive
   on" (default ``49010``) does **not** accept the ``RREF`` protocol, so the
   legacy port must be used.

.. note::

   X-Plane must allow inbound UDP on ``udp_out`` through the host firewall, or
   the requests are silently dropped.  (X-Plane's outbound Data Output still
   flows, so ``recv`` can appear to work while ``datarefs`` / ``dataref_writes``
   do nothing -- check the firewall first.)

Configuration
=============

::

  xplane:
    load: XPLANE
    module: fixgw.plugins.xplane

    # IP of the machine running X-Plane -- the destination for FIX -> X-Plane
    # writes and for RREF/DREF dataref requests.
    ipaddress: 127.0.0.1

    # Local UDP port FIX-Gateway BINDS to receive X-Plane's Data Output.
    udp_in:  49001
    # X-Plane's LEGACY receive port -- where FIX-Gateway SENDS data, RREF and
    # DREF.  Must be the legacy port (default 49000), not the newer 49010.
    udp_out: 49000

    # Seconds between FIX -> X-Plane control bursts (0.1 = 10 Hz).
    send_interval: 0.1

    # RREF stream rate, Hz (how often X-Plane sends subscribed datarefs back).
    dataref_hz: 10

recv -- indexed Data Output (X-Plane to FIX)
--------------------------------------------

Each key under ``recv`` is an X-Plane Data Output *index*; its value is a list
of up to eight FIX keys, one per slot, in the same order X-Plane lists that
row's columns.  Use ``_`` (or ``x`` / blank) to skip a slot.

::

  recv:
    # idx 3 -- Speeds: [Vind_kias, Vind_keas, Vtrue_ktas, Vtrue_ktgs, ...]
    3:  [IAS, _, TAS, GS, _, _, _, _]
    # idx 17 -- Pitch, roll, headings: [Pitch, Roll, Hdg_true, _, Hdg_mag, MagVar, _, Track]
    17: [PITCH, ROLL, _, _, HEAD, MAGVAR, _, _]
    # idx 20 -- Position: [lat, lon, alt_ft_msl, alt_ft_agl, ...]
    20: [LAT, LONG, ALT, AGL, _, _, _, _]

datarefs -- RREF subscriptions (X-Plane to FIX)
-----------------------------------------------

For values the indexed rows do not carry -- notably the nav-radio OBS course and
lateral/vertical deflection the HSI needs -- subscribe to X-Plane datarefs by
name.  FIX-Gateway issues an ``RREF`` request for each, so X-Plane needs no
extra Data Output checkboxes.  Two forms are accepted:

* ``FIX_KEY: dataref`` -- written to the key as-is.
* ``FIX_KEY: [dataref, scale]`` -- written multiplied by ``scale``.

::

  datarefs:
    # X-Plane hdef is +/-2.5 dots full scale; the HSI needle is +/-1.0, so
    # scale by 0.4 (use -0.4 to flip the deflection sense).
    GPSCRS:  sim/cockpit/radios/gps_course_degtm
    GPSCDI:  [sim/cockpit/radios/gps_hdef_dot, 0.4]
    NAV1CRS: sim/cockpit/radios/nav1_obs_degm
    NAV1CDI: [sim/cockpit/radios/nav1_hdef_dot, 0.4]
    HEADBUG: sim/cockpit/autopilot/heading_mag

dataref_writes -- DREF writes (FIX to X-Plane)
----------------------------------------------

Each ``FIX_KEY: dataref`` entry sends the FIX key's current value to a named
X-Plane dataref as a ``DREF`` packet, **on change** (the plugin tracks the last
value sent and only transmits when it differs).  This reuses the same UDP
socket as the rest of the plugin.

::

  dataref_writes:
    XPHSISRC: sim/cockpit2/radios/actuators/HSI_source_select_pilot

send -- indexed control output (FIX to X-Plane)
-----------------------------------------------

The mirror of ``recv``: each index maps FIX keys onto the eight slots of an
X-Plane Data Output control row, so hardware (a throttle/mixture quadrant, an
autopilot, etc.) publishing those FIX keys can *drive* the simulator.

.. warning::

   ``send`` is **disabled by default** and should stay that way for an ordinary
   X-Plane -> pyEfis test.  These rows are transmitted every ``send_interval``;
   if the mapped FIX keys are not actively driven by real hardware they read
   ``0`` and will command X-Plane's throttle **closed** and mixture to
   **cutoff**, killing the engine.  Only enable it when genuine hardware is
   publishing those keys.

Nav-source selection and autopilot follow
=========================================

In a real panel the EFIS is an active node in the navigation system: the pilot
selects which navigation source (GPS, NAV1, NAV2) the HSI displays, and the EFIS
publishes *that selected source's* guidance onto canonical FIX keys
(``COURSE`` / ``CDI`` / ``GSI``) that both the HSI **and the autopilot** read.
This plugin lets the same selection drive X-Plane so the simulator's native
autopilot tracks whatever the EFIS has selected.

The mechanism uses this plugin together with the :doc:`compute` plugin:

#. A button on the EFIS cycles the ``NAVSRC`` selector key
   (``0`` = GPS, ``1`` = NAV1, ``2`` = NAV2).
#. The compute plugin's ``select`` function routes the chosen source's
   per-source keys (``GPSCRS`` / ``NAV1CRS`` / ``NAV2CRS``, etc., populated by
   the ``datarefs`` section above) into the canonical ``COURSE`` / ``CDI`` /
   ``GSI`` keys.
#. The compute plugin's ``remap`` function translates ``NAVSRC`` into X-Plane's
   own source ordering (X-Plane uses ``0`` = NAV1, ``1`` = NAV2, ``2`` = GPS) and
   writes it to ``XPHSISRC``.
#. ``dataref_writes`` sends ``XPHSISRC`` out to
   ``sim/cockpit2/radios/actuators/HSI_source_select_pilot``, so X-Plane
   switches its HSI source and its autopilot follows.

::

  EFIS button -> NAVSRC --select--> COURSE/CDI/GSI  (HSI + autopilot read these)
                       \--remap---> XPHSISRC --DREF--> X-Plane HSI source + AP

See the :doc:`compute` plugin page for the ``select`` and ``remap`` function
definitions, and ``connections/compute.yaml`` for the shipped rules.
