#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  RAIM availability prediction (RAIM2, AER-6756).
#
#  A Qt-free library beside fixgw.geo: no plugin, no keys, no bus traffic.
#  The flightplan engine (RAIM3) is its caller. Design:
#  makerplane/briefs/flight_plan_plan.md section 3.3.2.

from fixgw.raim.almanac import AlmanacEntry, AlmanacError, almanac_age_days, parse_yuma
from fixgw.raim.availability import Exclusion, Outage, Prediction, Step, predict

__all__ = [
    "AlmanacEntry",
    "AlmanacError",
    "Exclusion",
    "Outage",
    "Prediction",
    "Step",
    "almanac_age_days",
    "parse_yuma",
    "predict",
]
