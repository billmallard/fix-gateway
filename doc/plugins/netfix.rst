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
