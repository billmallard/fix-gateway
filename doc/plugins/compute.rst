=================
Compute Plugin
=================

The compute plugin derives new FIX database keys from existing ones.  It is how
FIX-Gateway produces values that are not measured directly but calculated from
other points -- averages and maxima across a bank of cylinders, density
altitude from pressure and temperature, cross-track error from position and a
flight plan, and so on.  It also hosts the small *routing* primitives that
implement an EFIS nav-source selector (see `Nav-source selection`_).

The plugin watches the ``inputs`` of each configured function; whenever an input
changes it recomputes and writes the function's ``output`` key.  Most functions
propagate the input quality flags (``old`` / ``bad`` / ``fail``) to the output
and force the output to ``0`` when an input has failed.

Configuration
=============

The plugin is configured with a list of ``functions``.  Each function names a
``function`` type, its ``inputs`` (a list of FIX keys), and its ``output`` key.
An input named with a database variable expands as the database's keys do:
with ``t: 3`` in ``database/variables.yaml``, ``FUELQt`` is ``FUELQ1``,
``FUELQ2`` and ``FUELQ3``.

::

  compute:
    load: COMPUTE
    module: fixgw.plugins.compute
    functions:
      - function: max
        inputs: ["CHT1c"]       # CHT11 .. CHT1<c>, c from variables.yaml
        output: CHTMAX1

      - function: altd
        inputs: ["PALT", "OAT"]
        output: DALT

Every function also accepts an optional ``require_leader`` flag:

* ``require_leader: true`` (the default) -- the function only computes on the
  node that is the current *quorum leader*.  Use this in a redundant,
  multi-node installation so a derived key has a single writer.
* ``require_leader: false`` -- the function always computes.  Use this on a
  single-node system (and for the nav-source routing below, which must run
  regardless of quorum).

A few function types take an extra configuration key (``multiplier``, ``value``
or ``table``) noted in the catalog.

Function catalog
================

``average``
    Arithmetic mean of the ``inputs`` that have been written.  Like ``sum``,
    ``average``, ``max`` and ``span`` leave out an input no source has
    written yet, and once written an input's ``old`` / ``bad`` / ``fail``
    flags carry to the output.  The shipped ``EGTAVG1``, ``EGTSPAN1``,
    ``EGTMAX1`` and ``CHTMAX1`` take ``EGT1c`` / ``CHT1c``, engine 1's
    cylinders up to ``c``, so a four-cylinder install on ``c: 6`` still
    publishes.

``sum``
    Sum of the ``inputs`` that have been written.  An input no source has
    written yet is left out, so the sum publishes from the first one; once
    written, an input's ``old`` / ``bad`` / ``fail`` flags carry to the
    output.  The shipped ``FUELQT`` is ``sum`` over ``FUELQt``.

``max``
    Largest of the ``inputs`` that have been written (e.g. hottest CHT).

``min``
    Smallest of the ``inputs``.  Unlike ``max``, it waits until every input
    has been written: the shipped ``NAV1GSV`` is ``min`` over ``NAV1DH`` and
    ``NAV1GSNF``, and must not publish from one of them alone.

``span``
    Difference between the largest and smallest input written (e.g. EGT
    spread).

``altp``
    Pressure altitude from ``inputs: [BARO, ALT_MSL]``::

        PALT = ALT_MSL + 145442.2 * (1 - (BARO / 29.92126) ** 0.190261)

``altd``
    Density altitude from ``inputs: [PALT, OAT]`` (OAT in degrees C)::

        std_temp = 15 - 1.98 * PALT / 1000
        DALT     = PALT + 120 * (OAT - std_temp)

    ISA temperature is defined on pressure altitude, so ``PALT`` is used in
    both terms.  An older config listing ``[PALT, TALT, OAT]`` still works:
    the middle input is ignored and never waited on.

``xte``
    Signed cross-track error in nautical miles from
    ``inputs: [ac_lat, ac_lon, wp_lat, wp_lon, desired_course]`` (course in
    degrees true).  Positive = right of the desired course.

``aoa``
    Angle-of-attack estimator.  ``inputs`` are
    ``[PITCH, IAS, ANORM, VS, HEAD, <tuning ...>]`` followed by nine tuning
    constants; it learns a lift constant during steady, straight-and-level
    flight and derives alpha from normal acceleration and airspeed.  See the
    source for the full input list and constants.

``encoder``
    Adds ``input * multiplier`` to ``output`` (an accumulator driven by a
    rotary encoder).  Requires an extra ``multiplier`` key.

``set``
    When an input is truthy, writes a fixed ``value`` to ``output``.  Requires
    an extra ``value`` key.

``select``
    Routes one of several source keys to a single output, chosen by a selector
    key.  ``inputs[0]`` is the selector; ``inputs[1:]`` are the sources.  The
    selector value is rounded and clamped to pick a source, whose value (and its
    ``bad`` / ``fail`` flags) is copied to ``output``.  This is the heart of the
    nav-source selector -- one canonical key always carries the *selected*
    source.  A source may be a number instead of a key: a constant that is
    always fresh and valid.  A selected source key that nothing has written
    fails ``output``, unless the function sets ``unwritten: initial``: then
    the key's own initial value and flags are copied.  ``NAVSUPP`` uses this,
    because only a supplemental receiver writes its ``NAV<n>SUPP`` and a real
    radio's slot honestly reads its initial 0.

``remap``
    Emits ``table[round(index)]`` (clamped to the table) to ``output``, where
    ``inputs[0]`` is the index.  Requires an extra ``table`` key (a list).
    Translates one selector numbering into another.

Nav-source selection
====================

``select`` and ``remap`` together implement an EFIS navigation-source selector,
so the EFIS can both *display* the selected source and *publish* its guidance to
the keys the autopilot reads -- and, with the :doc:`xplane` plugin, drive a
simulator's autopilot to match.

A single selector key, ``NAVSRC`` (``0`` = NAV1, ``1`` = NAV2, ``2`` = GPS), is
cycled by a button on the EFIS.  ``select`` routes the chosen source's
per-source keys into the canonical ``COURSE`` / ``CDI`` / ``GSI`` keys that the
HSI and autopilot consume::

  functions:
    - function: select
      inputs: ["NAVSRC", "NAV1CRS", "NAV2CRS", "GPSCRS"]
      output: COURSE
      require_leader: false
    - function: select
      inputs: ["NAVSRC", "NAV1CDI", "NAV2CDI", "GPSCDI"]
      output: CDI
      require_leader: false
    - function: select
      inputs: ["NAVSRC", "NAV1GSI", "NAV2GSI", "GPSGSI"]
      output: GSI
      require_leader: false

``NAVSRC`` already uses X-Plane's HSI source numbering (``0`` = NAV1,
``1`` = NAV2, ``2`` = GPS), so the ``remap`` to ``XPHSISRC`` is an identity map;
the table is kept as the seam that collapses an optional second GPS (``3`` =
GPS2) onto X-Plane's single GPS, for the :doc:`xplane` plugin's
``dataref_writes`` to send out::

    - function: remap
      inputs: ["NAVSRC"]
      output: XPHSISRC
      table: [0, 1, 2]
      require_leader: false

The per-source keys (``GPSCRS``, ``NAV1CRS``, ...) are populated by the
:doc:`xplane` plugin's ``datarefs`` subscriptions; on a real aircraft they would
come from the GPS / nav-radio source plugins instead.

``NAVSRC`` and the bearing-pointer selectors ``BRG1SRC`` / ``BRG2SRC`` allow
``0`` .. ``3`` (``database/ahrs.yaml``). pyEfis's HSI labels every value in that
range, ``3`` as ``GPS2`` in the GPS colour (ENUM2, AER-6457); nothing writes
``3`` yet, and the ``select`` functions above have no fourth input for it. CI
runs pyEfis's ``tests/instruments/hsi/test_source_enums.py`` against this
tree's database, so a range widened here needs its pyEfis label first.
