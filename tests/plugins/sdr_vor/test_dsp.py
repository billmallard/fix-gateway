"""SDRVOR1 decoder against synthetic VOR and localizer signals (AER-6141)."""

import numpy as np
import pytest

from fixgw.plugins.sdr_vor import dsp
from tests.plugins.sdr_vor import siggen

GOOD_CN = 25.0  # dB in 25 kHz


@pytest.fixture(scope="module")
def decoder():
    return dsp.Decoder()


def test_bearing_within_2_deg_at_every_radial(decoder):
    rng = np.random.default_rng(1)
    worst = 0.0
    for radial in range(360):
        x = siggen.vor(
            radial, GOOD_CN, seed=radial, carrier_err_hz=rng.uniform(-5000, 5000)
        )
        m = decoder.decode(x, dsp.VOR)
        assert m.ok, (radial, m.failed())
        worst = max(worst, abs(dsp.wrap180(m.radial - radial)))
    assert worst < 2.0


@pytest.mark.parametrize("radial", [0.4, 89.6, 179.5, 270.25, 359.7])
def test_bearing_through_rtl_sdr_8_bit_samples(decoder, radial):
    x = dsp.cu8_to_complex(siggen.to_cu8(siggen.vor(radial, GOOD_CN, seed=7)))
    m = decoder.decode(x, dsp.VOR)
    assert m.ok
    assert abs(dsp.wrap180(m.radial - radial)) < 2.0


def test_localizer_ddm_within_5_percent_of_full_scale(decoder):
    for i, ddm in enumerate(np.linspace(-0.155, 0.155, 31)):
        m = decoder.decode(siggen.localizer(ddm, GOOD_CN, seed=i), dsp.LOC)
        assert m.ok, (ddm, m.failed())
        assert abs(m.ddm - ddm) < 0.05 * dsp.LOC_FULL_SCALE_DDM
        assert abs(dsp.loc_needle(m.ddm) - ddm / 0.155) < 0.05


def test_localizer_sense_90_hz_predominant_is_fly_right(decoder):
    # Annex 10: 90 Hz predominates left of course seen from the approach, so the
    # course is to the right: needle right, positive, as FIX CDI.
    m = decoder.decode(siggen.localizer(+0.1, GOOD_CN), dsp.LOC)
    assert dsp.loc_needle(m.ddm) > 0.5


@pytest.mark.parametrize("kind", [dsp.VOR, dsp.LOC])
def test_pure_noise_never_passes(decoder, kind):
    for seed in range(40):
        m = decoder.decode(siggen.noise(seed=seed, sigma=0.02 + 0.01 * seed), kind)
        assert not m.ok
        assert not m.checks["cn"]


def test_unmodulated_carrier_is_not_a_vor(decoder):
    m = decoder.decode(siggen.vor(0, GOOD_CN, var_depth=0, sub_depth=0, ident=False), dsp.VOR)
    assert m.checks["cn"]
    assert not m.ok
    assert not m.checks["var_snr"] and not m.checks["ref_snr"]


def test_vor_missing_one_30_hz_component_is_not_valid(decoder):
    no_var = decoder.decode(siggen.vor(45, GOOD_CN, var_depth=0), dsp.VOR)
    no_ref = decoder.decode(siggen.vor(45, GOOD_CN, sub_depth=0), dsp.VOR)
    assert not no_var.ok and not no_var.checks["var_snr"]
    assert not no_ref.ok and not no_ref.checks["ref_snr"]


def test_wrong_signal_for_the_channel_is_not_valid(decoder):
    assert not decoder.decode(siggen.localizer(0.0, GOOD_CN), dsp.VOR).ok
    assert not decoder.decode(siggen.vor(10, GOOD_CN), dsp.LOC).ok


@pytest.mark.parametrize("kind", ["vor", "loc"])
def test_threshold_both_sides(decoder, kind):
    """Above the C/N floor (15 dB) every block passes; well below it none does."""
    for seed in range(20):
        for cn, expect in ((19.0, True), (11.0, False)):
            if kind == "vor":
                m = decoder.decode(siggen.vor(seed * 17, cn, seed=seed), dsp.VOR)
            else:
                m = decoder.decode(siggen.localizer(0.05, cn, seed=seed), dsp.LOC)
            assert m.ok is expect, (cn, seed, m.cn_db, m.failed())


@pytest.mark.parametrize("kind", ["vor", "loc"])
def test_a_block_that_passes_near_the_floor_is_within_tolerance(decoder, kind):
    """The rule is sound: whatever passes at the edge of the floor still meets
    2 deg / 5% of full scale (the sweep in docs/sdr_vor.md, in small)."""
    passed = 0
    for seed in range(30):
        cn = 14.0 + (seed % 3)  # 14, 15, 16 dB
        if kind == "vor":
            truth = (seed * 37) % 360
            m = decoder.decode(siggen.vor(truth, cn, seed=100 + seed), dsp.VOR)
            err_ok = m.ok and abs(dsp.wrap180(m.radial - truth)) < 2.0
        else:
            truth = -0.155 + 0.31 * seed / 29
            m = decoder.decode(siggen.localizer(truth, cn, seed=100 + seed), dsp.LOC)
            err_ok = m.ok and abs(m.ddm - truth) < 0.05 * dsp.LOC_FULL_SCALE_DDM
        if m.ok:
            passed += 1
            assert err_ok, (seed, cn, m.cn_db)
    assert passed >= 10  # the edge was exercised, not skipped


def test_carrier_found_across_the_dongle_ppm_window(decoder):
    for err in (-5900.0, 0.0, 5900.0):
        m = decoder.decode(siggen.vor(222, GOOD_CN, carrier_err_hz=err), dsp.VOR)
        assert m.ok
        assert abs(m.carrier_hz - (siggen.OFFSET + err)) < 2.0
        assert abs(dsp.wrap180(m.radial - 222)) < 2.0


def test_wrong_block_length_raises(decoder):
    with pytest.raises(ValueError):
        decoder.decode(np.zeros(1000, complex), dsp.VOR)


def test_offset_and_guard_must_fit_the_sample_rate():
    with pytest.raises(ValueError):
        dsp.Decoder(dsp.DecoderConfig(sample_rate=240000, tune_offset=100000))


@pytest.mark.parametrize(
    "mhz,kind",
    [
        (108.00, dsp.VOR),
        (108.05, dsp.VOR),
        (108.10, dsp.LOC),
        (108.15, dsp.LOC),
        (109.30, dsp.LOC),
        (110.20, dsp.VOR),
        (111.95, dsp.LOC),
        (112.00, dsp.VOR),
        (113.90, dsp.VOR),
        (117.95, dsp.VOR),
        (107.95, None),
        (118.00, None),
        (108.03, None),
        (0.0, None),
        (None, None),
    ],
)
def test_channel_plan(mhz, kind):
    assert dsp.navaid_kind(mhz) == kind


@pytest.mark.parametrize(
    "bearing_to,course,cdi,tf",
    [
        (360.0, 0.0, 0.0, 1),  # south of the station, OBS 360: TO, centred
        (180.0, 0.0, 0.0, 2),  # north of the station, OBS 360: FROM, centred
        (355.0, 0.0, -0.5, 1),  # TO, east of the inbound course: fly left
        (5.0, 0.0, 0.5, 1),  # TO, west of it: fly right
        (185.0, 0.0, -0.5, 2),  # FROM radial 005, east of the outbound course: fly left
        (175.0, 0.0, 0.5, 2),  # FROM radial 355, west of it: fly right
        (303.0, 133.0, 1.0, 2),  # FROM radial 123 on course 133: course right, pegged
        (90.0, 300.0, 1.0, 2),  # FROM radial 270 on course 300: course right, clamped
        # Near abeam the TO/FROM switch is at 90 deg off the course, no sooner.
        (85.0, 0.0, 1.0, 1),
        (275.0, 0.0, -1.0, 1),
        (95.0, 0.0, 1.0, 2),
        (265.0, 0.0, -1.0, 2),
    ],
)
def test_vor_needle(bearing_to, course, cdi, tf):
    got_cdi, got_tf = dsp.vor_needle(bearing_to, course)
    assert got_tf == tf
    assert got_cdi == pytest.approx(cdi, abs=1e-9)


def test_loc_needle_full_scale_and_clamp():
    assert dsp.loc_needle(0.155) == pytest.approx(1.0)
    assert dsp.loc_needle(-0.0775) == pytest.approx(-0.5)
    assert dsp.loc_needle(0.4) == 1.0
    assert dsp.loc_needle(-0.4) == -1.0


def _m(radial, ok=True):
    m = dsp.Measurement(kind=dsp.VOR, radial=radial)
    m.checks["cn"] = ok
    return m


def test_bearing_must_be_stable_before_it_is_vouched_for():
    v = dsp.Validator()
    assert not v.accept(_m(10.0))
    assert not v.accept(_m(10.5))
    assert v.accept(_m(11.0))
    assert not v.accept(_m(40.0))  # a jump
    v.reset()
    assert not v.accept(_m(359.5))
    assert not v.accept(_m(0.5))
    assert v.accept(_m(0.0))  # stable across north


def test_a_failed_block_restarts_stability():
    v = dsp.Validator()
    for r in (10.0, 10.0, 10.0):
        v.accept(_m(r))
    assert not v.accept(_m(10.0, ok=False))
    assert not v.accept(_m(10.0))
    assert not v.accept(_m(10.0))
    assert v.accept(_m(10.0))
