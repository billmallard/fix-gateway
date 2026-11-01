===================================
FlightGear Flight Simulator Plugin
===================================

FlightGear is a free, open-source flight simulator that runs on Linux, macOS
and Windows. This plugin connects it to FIX-Gateway, so pyEfis can be flown
end to end without buying a simulator.

http://www.flightgear.org/

It writes the same FIX keys, in the same units and with the same signs, as
the X-Plane plugin, so pyEfis and the rest of FIX-Gateway do not know which
simulator is running.

.. warning::

   **One key, one writer.** Do not enable this plugin together with the
   ``xplane`` or ``demo`` plugins. They all write attitude, air data and
   position, and FIX-Gateway does not choose between sources. The plugin
   refuses to start, and logs why, if either is loaded. ``DEMO`` is enabled in
   the stock ``preferences.yaml``, so disable it in ``preferences.yaml.custom``
   when you enable ``FLIGHT_GEAR``.

Why the generic protocol
------------------------

FlightGear offers three ways to get data out:

- **Generic protocol** (used here). A user-supplied XML file defines a text
  sentence: which properties, in what order, scaled how. We control the wire
  format, so each field maps straight onto a FIX key, and FlightGear pushes
  sentences at a fixed rate over UDP.
- **Native FDM / controls protocols** (``--native-fdm``, ``--native-ctrls``).
  Fixed binary structures meant for linking FlightGear instances together.
  The layout is versioned with FlightGear and carries no nav radio or GPS
  data, which the HSI needs.
- **Property tree over telnet or HTTP** (``--telnet``, ``--httpd``). Any
  property can be read, but it is request/response over TCP: one round trip
  per poll. Fine for a dashboard, not for attitude at 10 Hz or more.

The generic protocol is the only one that is both push-based and covers
every key the X-Plane plugin writes.

Setup
-----

The protocol file is ``connections/fgfs/fix_fgfs.xml`` in the FIX-Gateway
config folder. FlightGear and FIX-Gateway must both read the same file.

1. Copy or link ``fix_fgfs.xml`` into FlightGear's ``Protocol`` directory:

   - Linux: ``/usr/share/games/flightgear/Protocol/``
   - macOS: ``/Applications/FlightGear.app/Contents/Resources/data/Protocol/``
   - Windows: ``C:\Program Files\FlightGear\data\Protocol\``

2. Enable the plugin (and disable ``DEMO`` and ``XPLANE``) in
   ``preferences.yaml.custom``::

     enabled:
       FLIGHT_GEAR: true
       DEMO: false
       XPLANE: false

3. Start FlightGear::

     fgfs --generic=socket,out,10,localhost,5500,udp,fix_fgfs

   ``10`` is sentences per second; raise it for smoother attitude.
   ``localhost`` and ``5500`` must match ``recv_host`` / ``recv_port``. Use
   the FIX-Gateway machine's address if FlightGear runs on another computer,
   and set ``recv_host`` to ``0.0.0.0`` there.

If the two programs read different copies of the XML, every sentence is
dropped and the log says how many fields arrived and how many were expected.

Configuration
-------------

::

  fgfs:
    load: FLIGHT_GEAR
    module: fixgw.plugins.fgfs
    # Searched in this order: fg_root/Protocol/, fg_root/, then the copy
    # shipped with FIX-Gateway.
    fg_root: ~/makerplane/fixgw/config/connections/fgfs
    xml_file: fix_fgfs.xml
    recv_host: localhost
    recv_port: 5500
    # FIX -> FlightGear. Off by default; see below.
    send: false
    send_host: localhost
    send_port: 5501
    rate: 10

Sending to FlightGear
---------------------

``send: true`` sends the XML's ``<input>`` section to FlightGear at ``rate``
sentences per second. Start FlightGear with a second connection to receive
it::

  --generic=socket,in,10,,5501,udp,fix_fgfs

It is off by default for a reason. Every ``<input>`` chunk is sent, every
time, whether or not anything publishes the key, and an unpublished key sends
its initial value. The X-Plane plugin's send path commanded the engine's
throttle closed the first time it reached a real port. The shipped file sends
only ``BARO`` (the altimeter setting); only add a control such as a throttle
when real hardware is publishing it. If an ``<input>`` key is not in the
database, the plugin turns sending off and logs it.

The protocol file
-----------------

Each ``<chunk>`` is one field::

  <chunk>
    <name>OILT1: Engine 1 oil temperature (C)</name>
    <type>float</type>
    <format>%.1f</format>
    <node>/engines/engine[0]/oil-temperature-degf</node>
    <offset>-17.7777778</offset>
    <factor>0.5555556</factor>
  </chunk>

- ``<name>``: FlightGear ignores it. The plugin takes the FIX key from the
  text before the ``:``.
- ``<node>``: the FlightGear property. Browse them in FlightGear under
  Debug > Browse Internal Properties.
- ``<factor>`` and ``<offset>``: FlightGear sends ``value * factor + offset``.
  The example converts Fahrenheit to Celsius.
- ``<format>``: a C ``printf`` format; it must print a number.

Two plugin rules extend what one chunk can do:

- **Chunks that name the same key are summed.** The TO/FROM keys are
  ``to-flag + 2 * from-flag`` (0 off, 1 TO, 2 FROM, the X-Plane encoding), and
  a VOR bearing is the true bearing plus a second chunk of
  ``-1 * magnetic-variation``.
- **Compass keys are wrapped into 0-360.** A key whose database range is 0 to
  just under 360 is reduced modulo 360 before it is written, so the bearing
  sum 5 - 13 lands on 352 instead of being clamped to 0.

What the shipped file sends
---------------------------

Every key the X-Plane plugin writes, plus FlightGear-only extras:

- **Air data**: IAS (airspeed instrument), TAS, GS, VS (from the flight model).
- **Attitude**: PITCH, ROLL, HEAD (magnetic), MAGVAR (west positive, as in
  the database), ROT, ALAT, AOA, VPATH, TRACK (true), TRACKM.
- **Position**: LAT, LONG, ALT (MSL, as X-Plane sends it), AGL.
- **Nav**: NAV1/NAV2 course, deviation, glideslope deviation and flags,
  TO/FROM and bearing to the station; the GPS course, deviation and TO/FROM
  as the EXT* keys; HEADBUG. FlightGear's stock GPS has no vertical guidance,
  so GPSGSF is always 1 (no glidepath).
- **Extras**: OAT, VOLT and one engine's RPM, manifold pressure, oil, EGT,
  CHT, fuel flow and two tanks. These are the default piston single's
  property paths; on an aircraft without them FlightGear sends 0.

Like X-Plane, FlightGear becomes the writer of HEADBUG, so the sim's heading
bug overrides pyEfis's HDG knob.
