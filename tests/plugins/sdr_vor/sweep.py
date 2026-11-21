"""Measure decoder error against carrier-to-noise, to set the validity rule.

The thresholds in fixgw.plugins.sdr_vor.dsp.Thresholds come from this run;
docs/sdr_vor.md records its output. Regenerate with:

    PYTHONPATH=src:tests/plugins/sdr_vor python3 tests/plugins/sdr_vor/sweep.py

For each C/N (dB in 25 kHz) it decodes TRIALS one-second blocks with random
radials / DDMs and carrier offsets. It reports the bearing error (VOR,
degrees) or DDM error (LOC, percent of full scale) as its 99th percentile, its
maximum, and its maximum over the blocks that pass; the lowest tone SNR seen;
and the share of blocks whose per-block checks pass at the shipped thresholds
(the VOR's stability check comes on top of these). It then decodes pure-noise
blocks and reports the highest C/N and tone SNR noise ever produced.
"""

import sys
from multiprocessing import Pool

import numpy as np

import siggen
from fixgw.plugins.sdr_vor import dsp

TRIALS = 200
CN_POINTS = list(range(0, 21, 2)) + [13, 15, 25, 30]
NOISE_TRIALS = 400


def one(args):
    kind, cn, seed = args
    rng = np.random.default_rng(10_000 + seed)
    err_hz = rng.uniform(-5000, 5000)
    d = dsp.Decoder()
    if kind == "vor":
        radial = rng.uniform(0, 360)
        m = d.decode(siggen.vor(radial, cn, seed=seed, carrier_err_hz=err_hz), dsp.VOR)
        err = abs(dsp.wrap180(m.radial - radial))
        snr = min(m.var_snr_db, m.ref_snr_db)
    else:
        ddm = rng.uniform(-0.155, 0.155)
        m = d.decode(siggen.localizer(ddm, cn, seed=seed, carrier_err_hz=err_hz), dsp.LOC)
        err = abs(m.ddm - ddm) / dsp.LOC_FULL_SCALE_DDM * 100.0
        snr = m.loc_snr_db
    return err, snr, m.cn_db, m.ok


def noise_one(args):
    kind, seed = args
    d = dsp.Decoder()
    m = d.decode(siggen.noise(seed=seed, sigma=0.05 + 0.3 * (seed % 7) / 7), kind)
    snr = min(m.var_snr_db, m.ref_snr_db) if kind == dsp.VOR else m.loc_snr_db
    return m.cn_db, snr, m.ok


def main():
    with Pool() as pool:
        for kind, unit in (("vor", "deg"), ("loc", "%FS")):
            print(
                f"\n{kind.upper()}: C/N dB | err p99 {unit} | err max | tone SNR min dB"
                " | C/N est mean | checks pass | err max of passing"
            )
            for cn in CN_POINTS:
                rows = pool.map(one, [(kind, cn, s) for s in range(TRIALS)])
                err = np.array([r[0] for r in rows])
                snr = np.array([r[1] for r in rows])
                est = np.array([r[2] for r in rows])
                passing = np.array([r[3] for r in rows])
                worst = f"{err[passing].max():8.3f}" if passing.any() else "       -"
                print(
                    f"  {cn:5.1f} | {np.percentile(err, 99):8.3f} | {err.max():8.3f} |"
                    f" {snr.min():7.1f} | {est.mean():6.1f} | {passing.mean():6.1%} | {worst}"
                )
        for kind, name in ((dsp.VOR, "VOR"), (dsp.LOC, "LOC")):
            rows = pool.map(noise_one, [(kind, s) for s in range(NOISE_TRIALS)])
            cn = max(r[0] for r in rows)
            snr = max(r[1] for r in rows)
            ok = sum(r[2] for r in rows)
            print(f"\npure noise as {name}: {NOISE_TRIALS} blocks, C/N est max {cn:.1f} dB,"
                  f" tone SNR max {snr:.1f} dB, checks passed {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
