=============================
Net-FIX Protocol Plugin
=============================

Net-FIX is the TCP/IP implementation of the FIX protocol suite.  There will be two versions of
this protocol set ASCII and Binary.  Currently this plugin only deals with the ASCII version of
the protocol.  [1]_

.. [1] The binary version hasn't even been invented yet.

Currently only the server side is implemented.

Net-FIX is the main way in which we connect FIX-Gateway to the pyEFIS program.  Net-FIX/ASCII is
currently the only communication mechanism that pyEFIS understands.  In fact FIX-Gateway was
written specifically to remove the complexities of multiple communications and data gathering
mechanisms from pyEFIS.

Configuration
--------------

::

  netfix:
    load: NETFIX
    module: fixgw.plugins.netfix
    type: server
    host: 127.0.0.1
    port: 3490
    buffer_size: 1024
    timeout: 1.0

**host** is the address the server listens on.  The shipped default is
``127.0.0.1``, so only clients on the same host can connect.  Leaving it
empty, or setting ``0.0.0.0``, listens on every interface.

Network exposure
----------------

Net-FIX has no authentication.  Any client that can connect can write any
key, and a value sentence with its flags omitted (``PITCH;15.0``) is written
with every flag clear, which means valid.  A display that trusts the gateway
will show that value as a good attitude, airspeed or altitude.  So the port
must be reachable only by the clients the installation intends.

**Single host** (pyEfis and FIX-Gateway on the same computer): keep the
default ``host: 127.0.0.1``.  Nothing off the host can reach the server.

**Multiple hosts** (a second display, a moving map or a control head on its
own computer): set ``host:`` explicitly, to the address of the one interface
that faces those clients, for example ``host: 192.168.50.1`` on a dedicated
avionics network.  Do not use ``0.0.0.0`` to make it work: that also listens
on every other network the host has joined.  Put the change in
``connections/netfix.yaml`` in your configuration directory.  An update keeps
an edited file and writes the new default beside it as ``netfix.yaml.dist``.

**Installation note.** The computer running FIX-Gateway should join only the
networks the installation needs.  In the aircraft that is the avionics
network, if there is one, and not a hangar, shop or home network, a phone
hotspot or a simulator bench.  On a network it does not need, any host that
can reach port 3490 can write data to the displays.  This applies to every
plugin that listens on the network, not only Net-FIX.

FIS-B weather request
---------------------

*Protocol addition, AER-5936.*  A client asks for the FIS-B text weather the
gateway holds for one station with a server specific command::

  @xweather;KSCK

The station is an identifier as the FIS-B text carries it, two to five letters
or digits; lower case is accepted and upper-cased.  The products come from the
weather store of every ``stratux`` plugin running with ``fisb: true`` (see ``doc/plugins/stratux.md``,
*FIS-B text weather*).  The match on the station is exact, and FIS-B does not use one
form for every product: METARs, TAFs and NOTAM-Ds name the airport by its ICAO
identifier (``KSCK``), while PIREPs and winds aloft use three letters (``SCK``,
or a winds-aloft site such as ``BCE``).  Ask for each form wanted.  TFRs,
SIGMETs and AIRMETs carry no station and are not returned by this request.

The reply is one line: the command, the station, and a JSON object::

  @xweather;KSCK;{"station":"KSCK","now":"2026-11-18T16:05:00Z","products":[...]}

Each product in ``products`` is an object with these fields:

``kind``
  ``METAR``, ``SPECI``, ``TAF``, ``TAF.AMD``, ``PIREP``, ``WINDS``,
  ``NOTAM-D`` and so on: the first word of the product.
``station``
  The station the product is stored under.
``text``
  The product text, exactly as stored.  A TAF's line breaks and any ``;`` are
  part of it.
``issued``
  The product's own time (observation, issue, report or forecast availability
  time), or ``null`` for a NOTAM, which is dated by its effective period.
``valid_from``
  When the product takes effect, or ``null``.  Before it, ``state`` is
  ``future``.
``old_at``
  When the product becomes old, or ``null`` for one that does not age (a
  NOTAM with no end time).
``first_received``, ``last_received``
  When the gateway first and last received the product.
``state``
  ``current``, ``future`` or ``old``, computed when the reply was built.
  ``now`` in the reply is the gateway's clock at that moment.  The age limits
  are in ``doc/plugins/stratux.md``.

Times are UTC, in ISO 8601 form (``2026-11-18T16:05:00Z``).  Products are
sorted by kind, then newest first.  A station the store holds nothing for gets
``"products":[]``.

Why JSON: Net-FIX is a line protocol with ``;`` between fields.  A TAF spans
several lines and a NOTAM may contain ``;``, and either would break a plain
``;``-separated value.  The JSON encoder escapes every line break, so the reply
is always one line.  The JSON object is the last field, so a client takes
everything after the second ``;`` and decodes it; a ``;`` inside it is never a
separator.

Errors follow the usual form, ``!`` and a code after the request:

``@xweather;KSCK!005``
  No store: no ``stratux`` plugin with ``fisb: true`` is running, so nothing
  could answer.  This is not the same as an empty list.
``@xweather;KS-CK!003``
  The station is missing or is not two to five letters or digits.

A gateway from before this addition answers ``@xweather;KSCK!001`` (unknown
server specific command).  Before the same change that reply was sent without
its line ending, so a client should not wait on it indefinitely.
