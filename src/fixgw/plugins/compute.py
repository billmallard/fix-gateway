#!/usr/bin/env python3

#  Copyright (c) 2018 Phil Birkelbach
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
#  USA.import plugin

#  This is a compute plugin.  It calculates derivative points like averages,
#  minimums or maximums and the like.  Specific calculations for things like
#  True Airspeed could be done also.

import fixgw.plugin as plugin
from fixgw.database import read
import fixgw.quorum as quorum
import math

# Great-circle geodesy helpers live in fixgw.geo (FP2, fix-gateway#23) so the
# flightplan engine plugin can share the exact same implementation. Imported
# here (not re-defined) so this module's behaviour is unchanged.
from fixgw.geo import (
    _radians,
    _initial_bearing_rad,
    _great_circle_distance_rad,
    _normalize_angle_rad,
)

# Determine pressure altitude
# inputs: BARO, ALTMSL
# Pressure Altitude = Elevation  in FT + (145442.2 * ( 1 - ( altimeter setting in inhg/29.92126)^.190261))


def altPressure(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        # This is to set the aux data in the output to one of the inputs
        o = parent.db_get_item(output)
        if type(value) != tuple:
            x = key.split(".")
            # we use the first input in the list to set the aux values
            if x[0] == inputs[0]:
                if o.get_aux_value(x[1]) != value:
                    o.set_aux_value(x[1], value)
            return
        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        pa = None
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True

        baro = list(vals)[0]
        msl = list(vals)[1]
        pa = vals[msl][0] + (145442.2 * (1 - (vals[baro][0] / 29.92126) ** 0.190261))
        o.value = pa
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


# Density altitude
# Standard Temperature = 15 – 1.98 * (A in ft) /1000
# Density Altitude = Pressure Altitude + (120 * (OAT deg C - Standard Temperature))
# inputs PALT ALTMSL OAT
def altDensity(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        # This is to set the aux data in the output to one of the inputs
        o = parent.db_get_item(output)
        if type(value) != tuple:
            x = key.split(".")
            # we use the first input in the list to set the aux values
            if x[0] == inputs[0]:
                if o.get_aux_value(x[1]) != value:
                    o.set_aux_value(x[1], value)
            return
        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        da = None
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        palt = list(vals)[0]
        talt = list(vals)[1]
        oat = list(vals)[2]
        st = 15 - (1.98 * (vals[talt][0]) / 1000)
        da = vals[palt][0] + (120 * (vals[oat][0] - st))
        o.value = da
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


# Determines the average of the inputs and writes that to output
def averageFunction(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        o = parent.db_get_item(output)
        # This is to set the aux data in the output to one of the inputs
        if type(value) != tuple:
            x = key.split(".")
            # we use the first input in the list to set the aux values
            if x[0] == inputs[0]:
                if o.get_aux_value(x[1]) != value:
                    o.set_aux_value(x[1], value)
            return

        vals[key] = value
        arrsum = 0
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            arrsum += vals[each][0]
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        o.value = arrsum / len(vals)
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


def encoderFunction(inputs, output, multiplier, require_leader):
    """Multiplies the input by the multiplier and adds the result to the output"""

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations

        o = parent.db_get_item(output)
        try:
            total = (value[0] * multiplier) + o.value[0]
        except TypeError:
            print(f"WTF Encoder output {output}")
            raise
        o.value = total

    return func


def setFunction(inputs, output, val, require_leader):
    """When fixids in inputs are True, set to output to val"""

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        if value[0]:
            o = parent.db_get_item(output)
            o.value = val

    return func


def sumFunction(inputs, output, require_leader):
    """Determines the sum of the inputs and writes that to output"""
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations

        vals[key] = value
        arrsum = 0
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            try:
                arrsum += vals[each][0]
                if vals[each][2]:
                    flag_old = True
                if vals[each][3]:
                    flag_bad = True
                if vals[each][4]:
                    flag_fail = True
                if vals[each][5]:
                    flag_secfail = True
            except TypeError:
                print("WTF {} {}".format(key, value))
                raise
        o = parent.db_get_item(output)
        o.value = arrsum
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


# Determines the max of the inputs and writes that to output
def maxFunction(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        # This is to set the aux data in the output to one of the inputs
        o = parent.db_get_item(output)
        if type(value) != tuple:
            x = key.split(".")
            # we use the first input in the list to set the aux values
            if x[0] == inputs[0]:
                if o.get_aux_value(x[1]) != value:
                    o.set_aux_value(x[1], value)
            return
        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        vmax = None
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vmax:
                if vals[each][0] > vmax:
                    vmax = vals[each][0]
            else:  # The first time through we just set vmax to the value
                vmax = vals[each][0]
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        o.value = vmax
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


# Determines the min of the inputs and writes that to output
def minFunction(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        # This is to set the aux data in the output to one of the inputs
        o = parent.db_get_item(output)
        if type(value) != tuple:
            x = key.split(".")
            # we use the first input in the list to set the aux values
            if x[0] == inputs[0]:
                if o.get_aux_value(x[1]) != value:
                    o.set_aux_value(x[1], value)
            return
        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        vmin = None
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vmin:
                if vals[each][0] < vmin:
                    vmin = vals[each][0]
            else:  # The first time through we just set vmax to the value
                vmin = vals[each][0]
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        o.value = vmin
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


# Determines the span between the highest and lowest of the inputs
# and writes that to output
def spanFunction(inputs, output, require_leader):
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        if type(value) != tuple:
            return  # This might be a meta data update
        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        vmin = None
        vmax = None
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vmin is not None:
                if vals[each][0] < vmin:
                    vmin = vals[each][0]
                if vals[each][0] > vmax:
                    vmax = vals[each][0]
            else:  # The first time through we just set vmax to the value
                vmin = vals[each][0]
                vmax = vals[each][0]

            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        o = parent.db_get_item(output)
        o.value = vmax - vmin
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


AOA_pitch_history = list()
AOA_ias_history = list()
AOA_acc_history = list()
AOA_vs_history = list()
AOA_heading_history = list()
AOA_lift_constant = None


def AOAFunction(inputs, output, require_leader):
    vals = {}
    # pitch_root: the pitch of the wing relative to the aircraft at the root
    (
        AOA_pitch_root,
        AOA_smooth_min_len,
        AOA_max_mean_vs,
        AOA_max_vs_dev,
        AOA_max_vs_trend,
        AOA_max_heading_dev,
        AOA_max_heading_trend,
        AOA_max_pitch_dev,
        AOA_max_pitch_trend,
    ) = inputs[5:]
    AOA_hist_count = 0
    for each in inputs[:5]:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        global AOA_lift_constant
        nonlocal AOA_hist_count
        if not isinstance(key, str):
            return
        # This is to set the aux data in the output to one of the inputs
        o = parent.db_get_item(output)
        vals[key] = value
        Vs = read("IAS.Vs")
        if Vs is None:
            Vs = 9999
        #
        # Accumulate history values for estimating a lift constant
        #
        if key == "PITCH":
            AOA_pitch_history.append(value[0])
            if len(AOA_pitch_history) > AOA_smooth_min_len:
                del AOA_pitch_history[0]
        if key == "IAS":
            AOA_ias_history.append(value[0])
            if len(AOA_ias_history) > AOA_smooth_min_len:
                del AOA_ias_history[0]
        if key == "ANORM":
            AOA_acc_history.append(value[0])
            if len(AOA_acc_history) > AOA_smooth_min_len:
                del AOA_acc_history[0]
        if key == "VS":
            AOA_vs_history.append(value[0])
            if len(AOA_vs_history) > AOA_smooth_min_len:
                del AOA_vs_history[0]
            AOA_hist_count += 1
        if key == "HEAD":
            AOA_heading_history.append(value[0])
            if len(AOA_heading_history) > AOA_smooth_min_len:
                del AOA_heading_history[0]
        #
        # Restart value history accumulation if any input is
        # not perfect quality
        #
        for each in vals:
            ve = vals[each]
            if not isinstance(ve, tuple):
                continue
            if ve[2]:
                AOA_hist_count = 0
                break
            if ve[3]:
                AOA_hist_count = 0
                break
            if ve[4]:
                AOA_hist_count = 0
                break
            if ve[5]:
                AOA_hist_count = 0
                break
        #
        # Compute AOA, one way or another
        #
        if len(AOA_ias_history):
            ias = AOA_ias_history[-1]
        else:
            ias = 0
        if AOA_lift_constant is not None and ias > Vs:
            # We're flying with a known lift constant, so compute alpha directly
            AOA_pitch_0 = read("AOA.0g")
            # Alpha (AOA) = lift_constant * acc[NORMAL/Z axis] / ias^2 -
            #               AOA_pitch_0
            o.value = (
                AOA_lift_constant * AOA_acc_history[-1] / (ias * ias) - AOA_pitch_0
            )
            flag_old = False
            flag_bad = False
            flag_fail = False
            flag_secfail = False
            for each in ["IAS", "ANORM"]:
                if vals[each] is None:
                    flag_fail = True
                if vals[each][2]:
                    flag_old = True
                if vals[each][3]:
                    flag_bad = True
                if vals[each][4]:
                    flag_fail = True
                if vals[each][5]:
                    flag_secfail = True
            o.old = flag_old
            o.bad = flag_bad
            o.fail = flag_fail
            o.secfail = flag_secfail
            if flag_old or flag_bad or flag_fail or flag_secfail:
                AOA_hist_count = 0
        elif ias < Vs and vals["PITCH"] is not None:
            # Give an answer for taxi'ing and/or takeoff roll
            pitch = vals["PITCH"]
            o.value = AOA_pitch_root + pitch[0]
            o.old, o.bad, o.fail, o.secfail = pitch[2:]
            # Since we're taxi'ing, we might have just refueled,
            # or changed the weight and balance, which drastically changes
            # the lift constant. Mark it as unknown to re-estimate
            # when possible.
            AOA_lift_constant = None
        elif vals["PITCH"] is not None:
            # Flying, but the lift constant is not yet established.
            # Give a guesstimate
            pitch = vals["PITCH"]
            o.value = AOA_pitch_root + pitch[0]
            o.old = pitch[2]
            o.bad = True
            o.fail = pitch[4]
        else:
            # We're not getting any basic data. Fail out.
            o.fail = True
        #
        # Update lift constant, if possible
        #
        if (
            AOA_hist_count > AOA_smooth_min_len
            and len(AOA_vs_history)
            and len(AOA_ias_history)
        ):
            # Check if we've been straight and level for a sufficient time
            AOA_hist_count = 0
            mean_vs = sum(AOA_vs_history) / len(AOA_vs_history)
            if (
                mean_vs < AOA_max_mean_vs
                and is_calm(AOA_vs_history, AOA_max_vs_dev, AOA_max_vs_trend)
                and is_calm(AOA_pitch_history, AOA_max_pitch_dev, AOA_max_pitch_trend)
                and is_calm(
                    AOA_heading_history,
                    AOA_max_heading_dev,
                    AOA_max_heading_trend,
                    wrap=360,
                )
            ):
                # Flying straight and level! We can estimate a lift constant
                acc_mean = sum(AOA_acc_history) / len(AOA_acc_history)
                ias_mean = sum(AOA_ias_history) / len(AOA_ias_history)
                pitch_mean = sum(AOA_pitch_history) / len(AOA_pitch_history)
                AOA_pitch_0 = read("AOA.0g")
                # The steady state angle of attack at wing root
                # Alpha [steady state] + AOA_pitch_0 = lift_constant * acc[NORMAL/Z axis] / ias^2
                alpha_ss = pitch_mean + AOA_pitch_root
                # (Alpha [steady state] + AOA_pitch_0) * ias^2 = lift_constant * acc
                # lift_constant = (Alpha [steady state] + AOA_pitch_0) * ias^2 / acc
                new_lift_constant = (
                    (alpha_ss + AOA_pitch_0) * ias_mean * ias_mean / acc_mean
                )
                if AOA_lift_constant is None:
                    AOA_lift_constant = new_lift_constant
                else:
                    filter_coefficient = 0.9
                    anti_filter_coefficient = 1 - filter_coefficient
                    AOA_lift_constant = (
                        new_lift_constant * anti_filter_coefficient
                        + AOA_lift_constant * filter_coefficient
                    )
                print("AOA estimation lift constant %g" % AOA_lift_constant)

    return func


def is_calm(samples, max_sample_dev, max_trend_dev, end_size=10, wrap=None):
    if wrap is None:
        mean = sum(samples) / len(samples)
        deviation = [abs(x - mean) for x in samples]
    else:
        mean = mean_wrap(samples, wrap)
        deviation = [abs_wrap(x, mean, wrap) for x in samples]
    deviation = max(deviation)
    end_count = int(round(float(len(samples)) * float(end_size) / 100.0))
    if end_count > 0:
        end_mean = sum(samples[-end_count:]) / end_count
        beg_mean = sum(samples[:end_count]) / end_count
        trend = abs(end_mean - beg_mean)
    else:
        trend = 0
    return deviation < max_sample_dev and trend < max_trend_dev


def mean_wrap(samples, wrap):
    standard = samples[0]
    sm = 0
    for s in samples:
        diff = s - standard
        if diff > wrap / 2:
            sm += s - wrap
        elif diff < -wrap / 2:
            sm += s + wrap
        else:
            sm += s
    ret = sm / len(samples)
    if ret < 0:
        ret += wrap
    elif ret >= wrap:
        ret -= wrap
    return ret


def abs_wrap(x, mean, wrap):
    diff = x - mean
    if diff > wrap / 2:
        diff -= wrap
    elif diff < -wrap / 2:
        diff += wrap
    return abs(diff)


def xteFunction(inputs, output, require_leader):
    """Computes signed cross-track error in nautical miles.

    inputs order:
      [aircraft_lat, aircraft_lon, waypoint_lat, waypoint_lon, desired_course]
    desired_course is expected in degrees true.
    """

    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations

        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True

        o = parent.db_get_item(output)
        if flag_fail:
            o.value = 0.0
            o.fail = True
            o.bad = flag_bad
            o.old = flag_old
            o.secfail = flag_secfail
            return

        own_lat = vals[inputs[0]][0]
        own_lon = vals[inputs[1]][0]
        wp_lat = vals[inputs[2]][0]
        wp_lon = vals[inputs[3]][0]
        desired_course_deg = vals[inputs[4]][0]

        if own_lat == wp_lat and own_lon == wp_lon:
            xte_nm = 0.0
        else:
            theta13 = _initial_bearing_rad(wp_lat, wp_lon, own_lat, own_lon)
            theta12 = _radians(desired_course_deg)
            delta13 = _great_circle_distance_rad(wp_lat, wp_lon, own_lat, own_lon)
            angle = _normalize_angle_rad(theta13 - theta12)
            xte_rad = math.asin(math.sin(delta13) * math.sin(angle))
            earth_radius_nm = 3440.065
            xte_nm = xte_rad * earth_radius_nm

        o.value = xte_nm
        o.fail = False
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


def bearingFunction(inputs, output, require_leader):
    """Great-circle initial bearing FROM the aircraft TO the active waypoint.

    inputs order: [aircraft_lat, aircraft_lon, waypoint_lat, waypoint_lon]
    output: a single numeric key, degrees TRUE, wrapped to [0, 360).

    This is the GPS bearing-to-waypoint source for an HSI bearing pointer -- the
    direction from the aircraft toward the active waypoint. It reuses the same
    great-circle helper as xteFunction (_initial_bearing_rad), called
    aircraft -> waypoint so the value points AT the waypoint.

    The value is TRUE. An HSI compass rose is magnetic, so the magnetic pointer
    source (GPSBRG) is produced downstream by a wrap360 of this true bearing +
    MAGVAR -- exactly the TRACK -> TRACKM idiom (see connections/compute.yaml).
    Keeping the trig here true-referenced keeps this function a pure geometric
    compute and leaves the one magnetic-variation seam in one place.
    """
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations

        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True

        o = parent.db_get_item(output)
        if flag_fail:
            o.value = 0.0
            o.fail = True
            o.bad = flag_bad
            o.old = flag_old
            o.secfail = flag_secfail
            return

        own_lat = vals[inputs[0]][0]
        own_lon = vals[inputs[1]][0]
        wp_lat = vals[inputs[2]][0]
        wp_lon = vals[inputs[3]][0]

        bearing_rad = _initial_bearing_rad(own_lat, own_lon, wp_lat, wp_lon)
        o.value = math.degrees(bearing_rad) % 360.0
        o.fail = False
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


def selectFunction(inputs, output, require_leader):
    # inputs[0] is the selector key; inputs[1:] are the source options. The
    # selector's (rounded, clamped) value picks which source is copied to the
    # output, so ONE canonical key (e.g. CDI / COURSE) always carries the
    # selected source -- which the HSI display and the autopilot both read.
    # Models an EFIS nav-source selector (GPS / NAV1 / NAV2).
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        if type(value) != tuple:
            return  # aux data, ignore
        vals[key] = value
        sel = vals[inputs[0]]
        if sel is None:
            return  # no selection yet -- leave the output untouched at boot
        o = parent.db_get_item(output)
        idx = max(0, min(len(inputs) - 2, int(round(sel[0]))))
        src = vals[inputs[idx + 1]]
        if src is None:
            # The selected source has never published a value. Do NOT leave the
            # previously selected source's value showing as if it were valid --
            # mark the canonical output FAILED so the HSI removes/flags the
            # needle (an honest "no source"), rather than a stale, sourceless
            # indication. Self-heals when the source starts publishing.
            o.fail = True
            return
        # Copy the selected source's value AND its quality flags, so a stale,
        # bad or failed source reads honestly downstream (the HSI greys/flags
        # instead of showing a frozen or sourceless needle). Mirrors
        # wrap360Function's flag handling.
        o.value = src[0]
        o.fail = src[4]
        if o.fail:
            o.value = 0.0
        o.bad = src[3]
        o.old = src[2]
        o.secfail = src[5]

    return func


def remapFunction(inputs, output, table, require_leader):
    # inputs[0] is an index key; table is a list of output values. Emits
    # table[round(index)] (clamped). Translates one selector scheme into
    # another -- e.g. collapsing NAVSRC's optional dual GPS (GPS1=2, GPS2=3) onto
    # X-Plane's single HSI_source_select GPS (2) via table [0, 1, 2, 2].
    def func(key, value, parent):
        if not quorum.leader and require_leader:
            return
        if type(value) != tuple:
            return
        i = max(0, min(len(table) - 1, int(round(value[0]))))
        o = parent.db_get_item(output)
        o.value = float(table[i])

    return func


def wrap360Function(inputs, output, require_leader):
    """Sum of the inputs wrapped to [0, 360) -- modular heading/track addition.
    e.g. magnetic ground track = true ground track + magnetic variation
    (TRACKM = TRACK + MAGVAR), since MAG = TRUE + VAR."""
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return  # This might be a meta data update
        if not quorum.leader and require_leader:
            return  # Only the leader can do calculations
        vals[key] = value
        arrsum = 0
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return  # We don't have one of each yet
            arrsum += vals[each][0]
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True
        o = parent.db_get_item(output)
        o.value = arrsum % 360.0
        o.fail = flag_fail
        if o.fail:
            o.value = 0.0
        o.bad = flag_bad
        o.old = flag_old
        o.secfail = flag_secfail

    return func


def windTriangle(inputs, output, require_leader):
    """Computes wind speed and direction from GPS wind triangle.

    inputs order: [GS, TRACK, TAS, HEAD]
    output: [windspd_key, winddir_key]
    All angles in degrees magnetic.  Speeds in knots.
    Writes windspd_key (knots) and winddir_key (degrees, FROM direction).
    """
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return
        if not quorum.leader and require_leader:
            return

        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True

        o_spd = parent.db_get_item(output[0])
        o_dir = parent.db_get_item(output[1])

        if flag_fail:
            o_spd.value = 0.0
            o_spd.fail = True
            o_dir.value = 0.0
            o_dir.fail = True
            return

        gs = vals[inputs[0]][0]
        track_rad = math.radians(vals[inputs[1]][0])
        tas = vals[inputs[2]][0]
        head_rad = math.radians(vals[inputs[3]][0])

        # Ground velocity vector (north/east components)
        gv_n = gs * math.cos(track_rad)
        gv_e = gs * math.sin(track_rad)

        # Air velocity vector (north/east components)
        av_n = tas * math.cos(head_rad)
        av_e = tas * math.sin(head_rad)

        # Wind velocity vector (direction wind is blowing TO)
        wv_n = gv_n - av_n
        wv_e = gv_e - av_e

        windspd = math.sqrt(wv_n ** 2 + wv_e ** 2)
        # Wind FROM direction = atan2 of wind-to vector + 180
        winddir = (math.degrees(math.atan2(wv_e, wv_n)) + 180.0) % 360.0

        o_spd.value = windspd
        o_spd.fail = False
        o_spd.bad = flag_bad
        o_spd.old = flag_old
        o_spd.secfail = flag_secfail

        o_dir.value = winddir
        o_dir.fail = False
        o_dir.bad = flag_bad
        o_dir.old = flag_old
        o_dir.secfail = flag_secfail

    return func


def windComponents(inputs, output, require_leader):
    """Computes headwind and crosswind components from wind speed/direction and heading.

    inputs order: [WINDSPD, WINDDIR, HEAD]
    output: [hwind_key, xwind_key]
    WINDDIR and HEAD in degrees magnetic.  WINDSPD in knots.
    HWIND positive = headwind, negative = tailwind.
    XWIND positive = wind from right, negative = wind from left.
    """
    vals = {}
    for each in inputs:
        vals[each] = None

    def func(key, value, parent):
        if type(value) != tuple:
            return
        if not quorum.leader and require_leader:
            return

        vals[key] = value
        flag_old = False
        flag_bad = False
        flag_fail = False
        flag_secfail = False
        for each in vals:
            if vals[each] is None:
                return
            if vals[each][2]:
                flag_old = True
            if vals[each][3]:
                flag_bad = True
            if vals[each][4]:
                flag_fail = True
            if vals[each][5]:
                flag_secfail = True

        o_hw = parent.db_get_item(output[0])
        o_xw = parent.db_get_item(output[1])

        if flag_fail:
            o_hw.value = 0.0
            o_hw.fail = True
            o_xw.value = 0.0
            o_xw.fail = True
            return

        windspd = vals[inputs[0]][0]
        winddir_rad = math.radians(vals[inputs[1]][0])
        head_rad = math.radians(vals[inputs[2]][0])

        # Angle between wind-from direction and heading
        # Headwind = wind_from projected onto heading axis
        # Crosswind = wind_from projected onto 90-deg-right axis
        relative_rad = winddir_rad - head_rad

        hwind = windspd * math.cos(relative_rad)
        xwind = windspd * math.sin(relative_rad)

        o_hw.value = hwind
        o_hw.fail = False
        o_hw.bad = flag_bad
        o_hw.old = flag_old
        o_hw.secfail = flag_secfail

        o_xw.value = xwind
        o_xw.fail = False
        o_xw.bad = flag_bad
        o_xw.old = flag_old
        o_xw.secfail = flag_secfail

    return func


class Plugin(plugin.PluginBase):
    # def __init__(self, name, config):
    #     super(Plugin, self).__init__(name, config)

    def run(self):
        # This directory of functions are functions that aggregate a list
        # of inputs and produce a single output.
        aggregate_functions = {
            "average": averageFunction,
            "sum": sumFunction,
            "max": maxFunction,
            "min": minFunction,
            "span": spanFunction,
            "xte": xteFunction,
            "bearing": bearingFunction,
            "aoa": AOAFunction,
            "altp": altPressure,
            "altd": altDensity,
            "encoder": encoderFunction,
            "set": setFunction,
            "select": selectFunction,
            "remap": remapFunction,
            "wrap360": wrap360Function,
            "wind_triangle": windTriangle,
            "wind_components": windComponents,
        }

        for function in self.config["functions"]:
            req_lead = True
            if "require_leader" in function:
                if not function["require_leader"]:
                    req_lead = False

            fname = function["function"].lower()
            if fname in aggregate_functions:
                if fname == "encoder":
                    f = aggregate_functions[fname](
                        function["inputs"],
                        function["output"],
                        function["multiplier"],
                        req_lead,
                    )
                elif fname == "set":
                    f = aggregate_functions[fname](
                        function["inputs"],
                        function["output"],
                        function["value"],
                        req_lead,
                    )
                elif fname == "remap":
                    f = aggregate_functions[fname](
                        function["inputs"],
                        function["output"],
                        function["table"],
                        req_lead,
                    )
                else:
                    f = aggregate_functions[fname](
                        function["inputs"], function["output"], req_lead
                    )
                for each in function["inputs"]:
                    if isinstance(each, str):
                        self.db_callback_add(each, f, self)

            else:
                self.log.warning("Unknown function - {}".format(function["function"]))

    def stop(self):
        pass

    # def get_status(self):
    #     """ The get_status method should return a dict or OrderedDict that
    #     is basically a key/value pair of statistics"""
    #     return OrderedDict({"Count":self.thread.count})


# TODO: Add a check for Warns and alarms and annunciate appropriatly
# TODO: Add tests for this plugin
# TODO: write stop function to remove all the callbacks
