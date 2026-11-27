#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Snapshot fault-detection availability: the chi-square threshold, the
#  noncentrality a missed detection needs, and the horizontal protection
#  level (HPL) from the geometry.
#
#  RAIM2 (AER-6756; makerplane/briefs/flight_plan_plan.md section 3.3.2).
#  This is the baseline snapshot RAIM of RTCA DO-208 (TSO-C129) Appendix F,
#  as published in R. G. Brown and G. Y. Chin, "GPS RAIM: Calculation of
#  Threshold and Protection Radius Using Chi-Square Methods -- A Geometric
#  Approach", ION Red Book vol. V (1997):
#
#    - the test statistic is the sum of squared normalized least-squares
#      residuals, chi-square with n - 4 degrees of freedom when fault-free;
#    - the detection threshold T sets P(chi2(n-4) > T) = Pfa;
#    - a bias on satellite i shifts the statistic to a noncentral chi-square
#      with noncentrality lambda = (b_i / sigma)^2 * S_ii, S = I - G (G'G)^-1 G';
#      lambda_md is the noncentrality at which P(statistic < T) = Pmd;
#    - the horizontal error that bias causes is |A_H,i| * b_i, A = (G'G)^-1 G',
#      so the bias that just escapes detection with probability Pmd gives
#      HPL = sigma * sqrt(lambda_md) * max_i(|A_H,i| / sqrt(S_ii)),
#      the largest "horizontal slope";
#    - fault detection is available when n >= 5 (4 + baro) and HPL <= HAL.
#
#  The RTCA text itself is not public and was not checked; the default
#  parameters below are the published DO-208 baseline values (Pfa 1/15000
#  per test, i.e. 0.002/h at one independent test per 2 min; Pmd 0.001;
#  sigma 33.3 m with Selective Availability), which are also what the FAA
#  SAPT models for TSO-C129 with "SA on". RAIM2's golden tests against SAPT
#  are what show the combination is right.

import math
from functools import lru_cache

import numpy as np

DEFAULT_PFA = 1.0 / 15000.0
DEFAULT_PMD = 1.0e-3
DEFAULT_SIGMA_M = 33.3


def _gammainc_lower(a, x):
    """Regularized lower incomplete gamma P(a, x) (Numerical Recipes 6.2)."""
    if x <= 0.0:
        return 0.0
    log_pre = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:
        term = 1.0 / a
        total = term
        ap = a
        for _ in range(10000):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return min(1.0, total * math.exp(log_pre))
    # Continued fraction for Q(a, x), modified Lentz.
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            break
    return max(0.0, 1.0 - math.exp(log_pre) * h)


def chi2_sf(x, dof):
    """P(chi-square with `dof` degrees of freedom > x)."""
    return 1.0 - _gammainc_lower(dof / 2.0, x / 2.0)


def ncx2_cdf(x, dof, nc):
    """P(noncentral chi-square(dof, nc) <= x), as a Poisson mixture."""
    if nc <= 0.0:
        return _gammainc_lower(dof / 2.0, x / 2.0)
    half = nc / 2.0
    # Sum outward from the Poisson mode so large nc does not underflow.
    mode = int(half)
    log_w = -half + mode * math.log(half) - math.lgamma(mode + 1)
    total = 0.0
    w = math.exp(log_w)
    j = mode
    while j >= 0:
        term = w * _gammainc_lower(dof / 2.0 + j, x / 2.0)
        total += term
        if w < 1e-18 and j < mode:
            break
        w *= j / half
        j -= 1
    w = math.exp(log_w)
    j = mode
    while True:
        j += 1
        w *= half / j
        term = w * _gammainc_lower(dof / 2.0 + j, x / 2.0)
        total += term
        if w < 1e-18 or term < 1e-20:
            break
    return min(1.0, total)


def _bisect(f, lo, hi, iterations=200):
    flo = f(lo)
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


@lru_cache(maxsize=None)
def threshold(dof, pfa=DEFAULT_PFA):
    """Detection threshold T on the normalized SSE: P(chi2(dof) > T) = pfa."""
    hi = 1.0
    while chi2_sf(hi, dof) > pfa:
        hi *= 2.0
    return _bisect(lambda x: chi2_sf(x, dof) - pfa, 0.0, hi)


@lru_cache(maxsize=None)
def lambda_md(dof, pfa=DEFAULT_PFA, pmd=DEFAULT_PMD):
    """Noncentrality at which a fault is missed with probability pmd."""
    t = threshold(dof, pfa)
    hi = 1.0
    while ncx2_cdf(t, dof, hi) > pmd:
        hi *= 2.0
    return _bisect(lambda nc: ncx2_cdf(t, dof, nc) - pmd, 0.0, hi)


def hpl(geometry, sigma=DEFAULT_SIGMA_M, pfa=DEFAULT_PFA, pmd=DEFAULT_PMD):
    """Horizontal protection level (m) for one epoch's geometry.

    `geometry` is the n x 4 matrix of rows [-e, -n, -u, 1] in local
    east/north/up (unit line of sight negated, then the clock), with any
    baro pseudo-measurement already appended as a row (see predict). Each
    row is assumed to carry the same `sigma`. Returns math.inf when there
    is no redundancy (n < 5) or the geometry is singular, so a caller's
    HPL <= HAL test fails closed.
    """
    g = np.asarray(geometry, dtype=float)
    n = g.shape[0]
    if n < 5:
        return math.inf
    gtg = g.T @ g
    try:
        a = np.linalg.solve(gtg, g.T)
    except np.linalg.LinAlgError:
        return math.inf
    s_diag = 1.0 - np.einsum("ij,ji->i", g, a)
    horizontal = np.hypot(a[0], a[1])
    # S_ii ~ 0: a fault on i never reaches the test statistic. That is
    # harmless only if it cannot move the horizontal position either.
    blind = s_diag < 1e-10
    if np.any(blind & (horizontal > 1e-9)):
        return math.inf
    slopes = np.where(blind, 0.0, horizontal / np.sqrt(np.where(blind, 1.0, s_diag)))
    return float(sigma * math.sqrt(lambda_md(n - 4, pfa, pmd)) * slopes.max())
