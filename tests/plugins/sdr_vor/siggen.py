"""Synthetic VOR and localizer IQ, built from ICAO Annex 10 Volume I (AER-6141).

Kept apart from the decoder on purpose: nothing here imports from
fixgw.plugins.sdr_vor, so a sign or phase error in the decoder is not shared.

- VOR (3.3.5): carrier AM by a 30 Hz variable tone (30%) and a 9,960 Hz
  subcarrier (30%) that is FM +/-480 Hz by a 30 Hz reference tone. The
  variable tone is the reference waveform DELAYED in time by
  radial / 360 of one 30 Hz cycle: that is what "lags by the bearing" means.
- Localizer (3.1.3.5): carrier AM by 90 Hz and 150 Hz. With SDM the sum of
  the depths, DDM = depth(90) - depth(150); 90 Hz predominates left of
  course seen from the approach.

``cn_db`` is carrier power over noise power in a 25 kHz bandwidth; the noise
is white complex Gaussian across the whole capture.

Run as a script to write an rtl_sdr-format (.cu8) file:

    python tests/plugins/sdr_vor/siggen.py vor --radial 123 --cn 30 -o vor.cu8
    python tests/plugins/sdr_vor/siggen.py loc --ddm 0.08 --cn 30 -o loc.cu8
"""

import argparse

import numpy as np

FS = 240000.0
OFFSET = 60000.0
B_REF = 25000.0


def _carrier(audio, t, cn_db, offset, carrier_err_hz, rng, amplitude):
    x = amplitude * (1.0 + audio) * np.exp(
        1j * (2 * np.pi * (offset + carrier_err_hz) * t + rng.uniform(0, 2 * np.pi))
    )
    if cn_db is not None:
        pc = amplitude**2
        sigma2 = pc / 10 ** (cn_db / 10.0) * (FS / B_REF)
        x = x + rng.normal(0, np.sqrt(sigma2 / 2), len(t)) + 1j * rng.normal(
            0, np.sqrt(sigma2 / 2), len(t)
        )
    return x


def vor(
    radial,
    cn_db=40.0,
    seconds=1.0,
    start=0.0,
    offset=OFFSET,
    carrier_err_hz=0.0,
    seed=0,
    amplitude=0.4,
    var_depth=0.30,
    sub_depth=0.30,
    deviation=480.0,
    ident=True,
):
    rng = np.random.default_rng(seed)
    t = start + np.arange(int(round(FS * seconds))) / FS
    w = 2 * np.pi * 30.0
    delay = (radial % 360.0) / 360.0 / 30.0
    variable = np.cos(w * (t - delay))
    # Subcarrier phase: integral of 9960 + deviation * cos(w t).
    sub = np.cos(2 * np.pi * 9960.0 * t + (deviation / 30.0) * np.sin(w * t))
    audio = var_depth * variable + sub_depth * sub
    if ident:
        # 1,020 Hz ident, 10%, keyed on and off at 7 Hz: present so the decoder
        # is shown to ignore it.
        key = (np.floor(t * 7.0) % 2 == 0).astype(float)
        audio = audio + 0.10 * key * np.cos(2 * np.pi * 1020.0 * t)
    return _carrier(audio, t, cn_db, offset, carrier_err_hz, rng, amplitude)


def localizer(
    ddm,
    cn_db=40.0,
    seconds=1.0,
    start=0.0,
    offset=OFFSET,
    carrier_err_hz=0.0,
    seed=0,
    amplitude=0.4,
    sdm=0.40,
):
    rng = np.random.default_rng(seed)
    t = start + np.arange(int(round(FS * seconds))) / FS
    m90 = (sdm + ddm) / 2.0
    m150 = (sdm - ddm) / 2.0
    audio = m90 * np.cos(2 * np.pi * 90.0 * t) + m150 * np.cos(2 * np.pi * 150.0 * t)
    return _carrier(audio, t, cn_db, offset, carrier_err_hz, rng, amplitude)


def noise(seconds=1.0, seed=0, sigma=0.1):
    rng = np.random.default_rng(seed)
    n = int(round(FS * seconds))
    return rng.normal(0, sigma, n) + 1j * rng.normal(0, sigma, n)


def to_cu8(x):
    """Quantise to rtl_sdr's interleaved unsigned 8-bit I/Q."""
    out = np.empty(2 * len(x))
    out[0::2] = x.real
    out[1::2] = x.imag
    return np.clip(np.round(out * 127.5 + 127.5), 0, 255).astype(np.uint8).tobytes()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("kind", choices=["vor", "loc", "noise"])
    p.add_argument("--radial", type=float, default=0.0)
    p.add_argument("--ddm", type=float, default=0.0)
    p.add_argument("--cn", type=float, default=40.0, help="C/N in 25 kHz, dB")
    p.add_argument("--seconds", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("-o", "--output", required=True)
    a = p.parse_args(argv)
    if a.kind == "vor":
        x = vor(a.radial, a.cn, a.seconds, seed=a.seed)
    elif a.kind == "loc":
        x = localizer(a.ddm, a.cn, a.seconds, seed=a.seed)
    else:
        x = noise(a.seconds, a.seed)
    with open(a.output, "wb") as f:
        f.write(to_cu8(x))


if __name__ == "__main__":
    main()
