=========================
rpi_virtualwire Plugin
=========================

Class to send and receive radio messages compatible with the Virtual Wire library for Arduinos. This library is commonly used with 313MHz and 434MHz radio tranceivers.

This plugins is a complex hardware and software configuration. See in Arduino folder to see the code.

* TODO : Create a <How to> to explain how to reproduce this little device

Keys and link loss
--------------------

Each message is ``"ias, oat"``. The plugin writes ``IAS`` with ``IASW`` 3 and
``OAT`` with ``OATW`` 3 only when a message carries a value. 400 (IAS) and -50
(OAT) are the sender's no-reading values: the plugin writes that key's warning
key as 2 and no value. A garbled message writes ``IASW`` and ``OATW`` as 2.

With no message the plugin writes nothing, so a dead radio lets ``IAS``,
``OAT`` and the warning keys go old on their tol (2 s) and the tape flags.
Before SRC3 (AER-6542) it rewrote the last values every 0.25 s, so a dead
radio held the airspeed tape at its last value, fresh, with ``IASW`` still 3.

Requirement Installation
--------------------------

* http://abyz.co.uk/rpi/pigpio/

::

  rm pigpio.zip
  sudo rm -rf PIGPIO
  wget abyz.co.uk/rpi/pigpio/pigpio.zip
  unzip pigpio.zip
  cd PIGPIO
  make -j4
  sudo make install


Configuration
--------------------

::

  virtualwire:
    load: yes
    module: plugins.rpi_virtualwire
    rxpin: 23
    bps: 2000
