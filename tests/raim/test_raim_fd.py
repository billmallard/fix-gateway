"""Fault-detection math of the RAIM prediction library (RAIM2, AER-6756).

The chi-square threshold and the missed-detection noncentrality are checked
against values computed independently with scipy.stats (chi2.isf and an
ncx2.cdf root), which is not a fix-gateway dependency. The geometry cases are
the analytic ones from makerplane/briefs/flight_plan_plan.md section 3.3.2.
"""

import math

import numpy as np
import pytest

from fixgw.raim import fd

# scipy 1.x: T = chi2.isf(1/15000, dof); lambda = brentq(ncx2.cdf(T, dof, nc) - 1e-3)
SCIPY_REFERENCE = {
    1: (15.903178, 50.099659),
    2: (19.231611, 54.586774),
    3: (21.954562, 57.898149),
    4: (24.391392, 60.652943),
    5: (26.652054, 63.064656),
    6: (28.789895, 65.237083),
    7: (30.835636, 67.230079),
    8: (32.808911, 69.081945),
}


@pytest.mark.parametrize("dof", sorted(SCIPY_REFERENCE))
def test_threshold_and_noncentrality_match_scipy(dof):
    t_ref, lam_ref = SCIPY_REFERENCE[dof]
    assert fd.threshold(dof) == pytest.approx(t_ref, abs=2e-6)
    assert fd.lambda_md(dof) == pytest.approx(lam_ref, abs=2e-6)


def test_threshold_is_the_false_alarm_quantile():
    for dof in (1, 4, 12):
        assert fd.chi2_sf(fd.threshold(dof), dof) == pytest.approx(fd.DEFAULT_PFA, rel=1e-9)


def test_noncentrality_gives_the_missed_detection_probability():
    for dof in (1, 4, 12):
        t = fd.threshold(dof)
        assert fd.ncx2_cdf(t, dof, fd.lambda_md(dof)) == pytest.approx(
            fd.DEFAULT_PMD, rel=1e-9
        )


def _row(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    e = math.cos(el) * math.sin(az)
    n = math.cos(el) * math.cos(az)
    u = math.sin(el)
    return [-e, -n, -u, 1.0]


def test_four_satellites_have_no_redundancy():
    g = [_row(0, 30), _row(90, 30), _row(180, 30), _row(270, 80)]
    assert fd.hpl(g) == math.inf


def test_five_satellites_one_alone_fixing_east_cannot_be_checked():
    """Brief 3.3.2 analytic case: a 5-satellite geometry with no fault detection.

    Four satellites lie in the north-south vertical plane (azimuth 0 or 180),
    so their rows have no east component and the four alone have rank 3.
    East comes from the fifth satellite only. The parity vector lies on the
    four, S_55 = 0, and a bias on the fifth moves the position east without
    reaching the test statistic. No HAL makes that available.
    """
    g = [_row(0, 20), _row(0, 60), _row(180, 30), _row(180, 75), _row(90, 40)]
    assert fd.hpl(g) == math.inf


def test_four_on_one_cone_still_protect_horizontal():
    """The near miss of the case above: four satellites on one elevation cone.

    Their up and clock columns are proportional, so a fifth satellite's
    fault is undetectable here too, but it moves only up/clock, never the
    horizontal position. The horizontal slope stays finite. (The first
    draft of this suite used this geometry as the "unavailable" case.)
    """
    g = [_row(0, 40), _row(90, 40), _row(180, 40), _row(270, 40), _row(45, 10)]
    assert math.isfinite(fd.hpl(g))
    assert fd.hpl(g) < 1e4


def test_hpl_is_sigma_root_lambda_times_the_largest_slope():
    g = np.array(
        [_row(0, 15), _row(72, 35), _row(144, 60), _row(216, 25), _row(288, 50), _row(0, 88)]
    )
    a = np.linalg.inv(g.T @ g) @ g.T
    s = np.eye(len(g)) - g @ a
    slopes = [math.hypot(a[0, i], a[1, i]) / math.sqrt(s[i, i]) for i in range(len(g))]
    expected = 33.3 * math.sqrt(SCIPY_REFERENCE[2][1]) * max(slopes)
    assert fd.hpl(g) == pytest.approx(expected, rel=1e-6)


def test_more_satellites_never_raise_hpl_here():
    base = [_row(0, 15), _row(72, 35), _row(144, 60), _row(216, 25), _row(288, 50)]
    more = base + [_row(30, 70), _row(200, 45)]
    assert fd.hpl(more) < fd.hpl(base)


def test_baro_row_supplies_the_fifth_measurement():
    four = [_row(10, 20), _row(100, 45), _row(200, 30), _row(300, 60)]
    assert fd.hpl(four) == math.inf
    assert math.isfinite(fd.hpl(four + [[0.0, 0.0, 1.0, 0.0]]))
