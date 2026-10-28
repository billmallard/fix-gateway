#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Simulated flights, synthetic tier (SYN1; makerplane/briefs/sim_bench_plan.md
#  sections 3, 4 and 6 in maos-workspace).
#
"""Fly a scenario profile without a pilot or a simulator.

``profile``   loads the scenario format both tiers read (PROFILE_FORMAT.md).
``cifp``      the ARINC 424 records the scenario needs.
``refnav``    the reference navigator: the truth (``REF*`` keys), written
              independently of the navigation engine and importing nothing
              from it.
``aircraft``  a point-mass aircraft whose autopilot flies the reference.
``fplroute``  the route block an editor would publish for the procedure.
``generator`` profile + CIFP -> bus rows -> a data_recorder file.
``checks``    the profile's checks, evaluated on any recorded bus log.

Run ``python -m fixgw.sim --help``.
"""
