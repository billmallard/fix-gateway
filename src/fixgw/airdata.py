#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Shared air-data helpers.
#
#  Extracted from fixgw.plugins.compute (CERT13h, AER-6082) so the compute
#  plugin's PALT function and the onspeed plugin's ALT share one altimeter-
#  setting formula.  The expression is what altPressure() used inline; no
#  behaviour change.  A source that sends pressure altitude and one that sends
#  indicated altitude therefore round-trip exactly through compute's PALT.

# Altimeter settings no sea-level pressure on record falls outside (the
# extremes are about 25.69 and 32.03 inHg). Anything beyond is a bad input,
# not weather.
BARO_PLAUSIBLE = (25.0, 33.0)


def baro_plausible(setting):
    return BARO_PLAUSIBLE[0] <= setting <= BARO_PLAUSIBLE[1]


def baro_offset_ft(setting):
    """Pressure altitude minus indicated altitude, ft, for an altimeter
    setting in inHg:  PALT = ALT + baro_offset_ft(BARO).

    Negative above 29.92 inHg (the altimeter reads higher than pressure
    altitude), positive below.
    """
    return 145442.2 * (1 - (setting / 29.92126) ** 0.190261)
