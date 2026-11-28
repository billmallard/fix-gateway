"""RAIM availability prediction over a window (RAIM2, AER-6756).

Brief 3.3.2's second analytic case (a NANU removes the satellite that made
fault detection available) is built on the real December 2024 almanac at
KSMX with an 18 deg mask, which leaves six satellites in view at 18:00Z.
"""

import datetime as dt
import math
import pathlib
import time

import pytest

from fixgw.raim import Exclusion, parse_yuma, predict

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "raim"
KSMX = (34.89892, -120.45758, 80.0)
T18 = dt.datetime(2024, 12, 19, 18, 0, tzinfo=dt.timezone.utc).timestamp()
NM = 1852.0


@pytest.fixture(scope="module")
def entries():
    text = (FIXTURE / "2024_almanac.yuma.week0297.405504.txt").read_text()
    return parse_yuma(text, T18)


def test_nanu_removes_the_satellite_that_made_detection_available(entries):
    hal = 2.0 * NM
    clear = predict(entries, *KSMX, T18, T18, hal, mask_deg=18.0)
    assert clear.steps[0].satellites == 6
    assert clear.available

    nanu = [Exclusion(16, T18 - 3600, T18 + 3600)]
    out = predict(entries, *KSMX, T18, T18, hal, mask_deg=18.0, exclusions=nanu)
    assert out.steps[0].satellites == 5
    assert not out.available
    assert out.steps[0].hpl_m > 5 * hal


def test_an_exclusion_only_covers_its_window(entries):
    before = [Exclusion(16, T18 - 7200, T18 - 3600)]
    later = [Exclusion(16, T18 + 60)]
    for nanu in (before, later):
        p = predict(entries, *KSMX, T18, T18, 2.0 * NM, mask_deg=18.0, exclusions=nanu)
        assert p.steps[0].satellites == 6


def test_until_further_notice_has_no_end(entries):
    nanu = [Exclusion(16, T18 - 86400 * 30)]
    p = predict(entries, *KSMX, T18, T18, 2.0 * NM, mask_deg=18.0, exclusions=nanu)
    assert p.steps[0].satellites == 5


def test_unhealthy_almanac_satellites_are_never_used(entries):
    sick = [e if e.prn != 16 else e.__class__(**{**e.__dict__, "health": 63}) for e in entries]
    p = predict(sick, *KSMX, T18, T18, 2.0 * NM, mask_deg=18.0)
    assert p.steps[0].satellites == 5


def test_baro_aiding_restores_the_fifth_measurement(entries):
    """With 5 satellites, removing one more leaves 4: no detection without
    baro, and baro aiding (TSO-C129) supplies the fifth measurement."""
    nanu = [Exclusion(16, T18 - 60, T18 + 60), Exclusion(30, T18 - 60, T18 + 60)]
    bare = predict(entries, *KSMX, T18, T18, 1e9, mask_deg=18.0, exclusions=nanu)
    assert bare.steps[0].satellites == 4
    assert bare.steps[0].hpl_m == math.inf
    aided = predict(entries, *KSMX, T18, T18, 1e9, mask_deg=18.0, exclusions=nanu, baro=True)
    assert aided.steps[0].satellites == 4
    assert math.isfinite(aided.steps[0].hpl_m)


def test_steps_cover_the_window_inclusively(entries):
    p = predict(entries, *KSMX, T18 - 900, T18 + 900, 0.3 * NM)
    assert len(p.steps) == 31
    assert p.steps[0].unix == T18 - 900
    assert p.steps[-1].unix == T18 + 900


def test_outage_windows_group_consecutive_unavailable_steps(entries):
    # Five satellites at a 20 deg mask: HPL 28-190 km over these five
    # minutes, so a 0.3 nm HAL fails at every step and a 1000 km one passes.
    fail = predict(entries, *KSMX, T18, T18 + 240, 0.3 * NM, mask_deg=20.0)
    assert [(o.start_unix, o.end_unix) for o in fail.outages] == [(T18, T18 + 240)]
    assert fail.outages[0].duration_s == 300
    assert fail.longest_outage_s() == 300
    ok = predict(entries, *KSMX, T18, T18 + 240, 1_000_000.0, mask_deg=20.0)
    assert ok.available
    assert ok.outages == ()


def test_outages_split_around_an_available_step(entries):
    nanu = [Exclusion(16, T18 + 120, T18 + 180)]
    p = predict(entries, *KSMX, T18, T18 + 300, 2.0 * NM, mask_deg=18.0, exclusions=nanu)
    assert [s.available for s in p.steps] == [True, True, False, True, True, True]
    assert [(o.start_unix, o.end_unix) for o in p.outages] == [(T18 + 120, T18 + 120)]
    assert p.outages[0].duration_s == 60


@pytest.mark.parametrize("bad", [dict(step_s=0), dict(step_s=-60)])
def test_bad_step_is_refused(entries, bad):
    with pytest.raises(ValueError):
        predict(entries, *KSMX, T18, T18 + 60, NM, **bad)


def test_window_backwards_is_refused(entries):
    with pytest.raises(ValueError):
        predict(entries, *KSMX, T18, T18 - 60, NM)


def test_no_almanac_means_no_detection():
    p = predict([], *KSMX, T18, T18 + 60, NM)
    assert not p.available
    assert all(s.satellites == 0 for s in p.steps)


def test_fifteen_minute_window_is_fast_here(entries):
    """The DoD budget is 200 ms on the Pi 5 (measured there; see the PR).
    This guards against a regression on the build host, at 50 ms."""
    predict(entries, *KSMX, T18 - 900, T18 + 900, 0.3 * NM)  # warm caches
    start = time.perf_counter()
    predict(entries, *KSMX, T18 - 900, T18 + 900, 0.3 * NM)
    assert time.perf_counter() - start < 0.05
