"""VOR and localizer decoding from complex baseband IQ (SDRVOR1, AER-6141).

SUPPLEMENTAL, NOT A CERTIFIED NAV RECEIVER: day VFR situational awareness only.

Written clean-room from the published signal definitions, ICAO Annex 10
Volume I:

- VOR (3.3): an AM carrier carrying a 30 Hz "variable" tone and a 9,960 Hz
  subcarrier frequency-modulated +/-480 Hz by a 30 Hz "reference" tone. The
  variable tone lags the reference by the magnetic bearing of the aircraft
  from the station (the radial). Ident is 1,020 Hz Morse (not decoded here).
- Localizer (3.1.3): an AM carrier carrying 90 Hz and 150 Hz tones. The
  difference in depth of modulation (DDM) of the two gives the deviation:
  90 Hz predominates left of the course line seen from the approach, and
  0.155 DDM is full scale.

Everything here is pure numpy on a block of samples, so the tests drive it
with synthetic signals and recorded files without a radio.

One block is ``window_s`` seconds of IQ at ``sample_rate``, with the station
carrier expected near ``+tune_offset`` Hz (the radio is tuned below the
station so the carrier is clear of the dongle's DC spike). The decoder:

1. finds the carrier within ``+/-search_hz`` of the offset (dongle ppm error)
   and measures carrier-to-noise in a 25 kHz channel against a guard band;
2. cuts a +/-12 kHz channel around the carrier and decimates it, in the
   frequency domain, to ``CHANNEL_RATE``;
3. takes the AM envelope, normalised by the carrier, as the modulation;
4. measures each tone as one DFT bin over the central part of the block (an
   integer number of 30, 90 and 150 Hz cycles, edges trimmed of the block
   filter's ringing), and its SNR against the neighbouring bins.

The decoder reports what it measured and whether each check passed. It does
not hold state across blocks; the bearing-stability check is ``Validator``.
"""

import math
from dataclasses import dataclass, field

import numpy as np

# Channel half-width. A VOR's sidebands reach 9,960 + 480 + 30 Hz.
CHANNEL_HALF_HZ = 12000.0
CHANNEL_RATE = 2 * CHANNEL_HALF_HZ

# The bandwidth carrier-to-noise is quoted in: one 25 kHz channel.
CN_BANDWIDTH_HZ = 25000.0

# Noise is measured between 14 and 30 kHz either side of the carrier: clear of
# the channel's own sidebands (10.5 kHz) and of a 50 kHz neighbour's (39.5 kHz).
GUARD_LO_HZ = 14000.0
GUARD_HI_HZ = 30000.0

# Seconds trimmed from each end of the block before tones are measured. With a
# 1.0 s block this leaves 0.9 s: 27 cycles of 30 Hz, 81 of 90 Hz, 135 of 150 Hz.
TRIM_S = 0.05

VOR_SUBCARRIER_HZ = 9960.0
VOR_SUBCARRIER_HALF_BW_HZ = 700.0  # +/-480 Hz deviation + 30 Hz, with margin
VOR_TONE_HZ = 30.0
LOC_LOW_HZ = 90.0
LOC_HIGH_HZ = 150.0
LOC_FULL_SCALE_DDM = 0.155

# Tone SNR: the tone bin's power over the mean power of the bins around it,
# from LO to HI Hz away, either side.
TONE_NOISE_LO_HZ = 4.0
TONE_NOISE_HI_HZ = 20.0

VOR = 1
LOC = 2


def wrap360(a):
    return a % 360.0


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def navaid_kind(freq_mhz):
    """The navaid a frequency belongs to by the ICAO channel plan, or None.

    108.0-111.95 MHz: localizers on odd tenths (108.10, 108.15, 108.30 ...),
    VORs on even tenths. 112.0-117.95 MHz: VORs only. Channels are 50 kHz.
    """
    if freq_mhz is None:
        return None
    khz = int(round(freq_mhz * 1000.0))
    if khz < 108000 or khz > 117950 or khz % 50:
        return None
    if khz < 112000 and (khz // 100) % 2 == 1:
        return LOC
    return VOR


@dataclass
class DecoderConfig:
    sample_rate: float = 240000.0
    tune_offset: float = 60000.0
    search_hz: float = 6000.0  # about 55 ppm at 108 MHz
    window_s: float = 1.0


@dataclass
class Thresholds:
    """The validity rule. Values are set from the measured sweep recorded in
    docs/sdr_vor.md; the tests pin both sides of each."""

    min_cn_db: float = 15.0
    min_tone_snr_db: float = 20.0
    # VOR: Annex 10 sets each 30 Hz component at 30% depth and the reference
    # deviation at 480 Hz. Wide plausibility bands: they reject a signal that
    # is not a VOR, not a VOR with some multipath.
    vor_depth: tuple = (0.15, 0.45)
    vor_deviation_hz: tuple = (350.0, 620.0)
    # Localizer: the sum of the two depths is 40% nominal, at most 60%.
    loc_sdm: tuple = (0.25, 0.65)
    # Bearing stability: the last N bearings all within this of their mean.
    stable_count: int = 3
    stable_deg: float = 2.0


@dataclass
class Measurement:
    kind: int
    cn_db: float = -math.inf
    carrier_hz: float = 0.0
    checks: dict = field(default_factory=dict)
    # VOR
    radial: float = None  # bearing FROM the station, degrees magnetic
    var_depth: float = 0.0
    ref_deviation_hz: float = 0.0
    var_snr_db: float = -math.inf
    ref_snr_db: float = -math.inf
    # Localizer
    ddm: float = None  # signed: + = 90 Hz predominates = course to the right
    sdm: float = 0.0
    loc_snr_db: float = -math.inf

    @property
    def ok(self):
        return bool(self.checks) and all(self.checks.values())

    def failed(self):
        return [k for k, v in self.checks.items() if not v]


def _db(x):
    return 10.0 * math.log10(x) if x > 0 else -math.inf


def _tone(signal, times, freq):
    """Complex amplitude of a real tone: A cos(2 pi f t + phi) -> A e^(j phi)."""
    return 2.0 * np.mean(signal * np.exp(-2j * np.pi * freq * times))


def _tone_snr_db(signal, freq):
    """Power of the tone's bin over the mean power of the bins around it.

    ``signal`` spans a whole number of cycles of ``freq`` at CHANNEL_RATE, so
    the tone sits on a bin and the probes, on other bins, see none of it.
    """
    spec = np.abs(np.fft.rfft(signal)) ** 2
    step = CHANNEL_RATE / len(signal)
    k = int(round(freq / step))
    offs = np.arange(math.ceil(TONE_NOISE_LO_HZ / step), int(TONE_NOISE_HI_HZ / step) + 1)
    probes = np.concatenate([k - offs, k + offs])
    probes = probes[probes > 0]
    return _db(spec[k] / max(np.mean(spec[probes]), 1e-30))


class Decoder:
    def __init__(self, config=None):
        self.cfg = config or DecoderConfig()
        self.block_len = int(round(self.cfg.sample_rate * self.cfg.window_s))
        self.chan_len = int(round(CHANNEL_RATE * self.cfg.window_s))
        trim = int(round(TRIM_S * CHANNEL_RATE))
        self._core = slice(trim, self.chan_len - trim)
        self._t = np.arange(self.chan_len) / CHANNEL_RATE
        if self.cfg.tune_offset + self.cfg.search_hz + GUARD_HI_HZ > self.cfg.sample_rate / 2:
            raise ValueError("tune_offset plus guard band exceeds the sample rate")

    # -- front end ---------------------------------------------------------

    def _channel(self, iq):
        """Find the carrier, measure C/N, and return the decimated channel."""
        n = self.block_len
        fs = self.cfg.sample_rate
        spec = np.fft.fft(iq)
        power = np.abs(spec) ** 2
        df = fs / n
        lo = int(round((self.cfg.tune_offset - self.cfg.search_hz) / df))
        hi = int(round((self.cfg.tune_offset + self.cfg.search_hz) / df))
        idx = np.arange(lo, hi + 1) % n
        kc = int(idx[np.argmax(power[idx])])
        # Carrier: the peak bin and its neighbours (a carrier between bins
        # spreads over them); its 30 Hz sidebands are well outside +/-3 bins.
        pc = np.sum(power[(kc + np.arange(-3, 4)) % n]) / n**2
        g = np.arange(int(GUARD_LO_HZ / df), int(GUARD_HI_HZ / df))
        guard = np.concatenate([(kc + g) % n, (kc - g) % n])
        # Median of exponentially distributed bin powers is ln2 x their mean.
        n0 = np.median(power[guard]) / math.log(2) / (n * fs)  # per Hz
        cn_db = _db(pc / (n0 * CN_BANDWIDTH_HZ)) if n0 > 0 else math.inf
        half = self.chan_len // 2
        sel = (kc + np.arange(-half, half)) % n
        chan = np.fft.ifft(np.fft.ifftshift(spec[sel])) * (self.chan_len / n)
        carrier_hz = (kc if kc < n / 2 else kc - n) * df
        return chan, cn_db, carrier_hz

    # -- decoding ------------------------------------------------------------

    def decode(self, iq, kind, thresholds=None):
        """Measure one block. ``kind`` is VOR or LOC (from the tuned
        frequency). Returns a Measurement whose checks say what passed."""
        th = thresholds or Thresholds()
        iq = np.asarray(iq, dtype=np.complex128)
        if len(iq) != self.block_len:
            raise ValueError(f"block is {len(iq)} samples, expected {self.block_len}")
        chan, cn_db, carrier_hz = self._channel(iq)
        m = Measurement(kind=kind, cn_db=cn_db, carrier_hz=carrier_hz)
        m.checks["cn"] = cn_db >= th.min_cn_db
        env = np.abs(chan)
        mean = np.mean(env[self._core])
        if mean <= 0:
            m.checks["carrier"] = False
            return m
        mod = env / mean - 1.0
        core = self._core
        t = self._t[core]
        if kind == VOR:
            self._decode_vor(m, mod, t, th)
        else:
            self._decode_loc(m, mod[core], t, th)
        return m

    def _decode_vor(self, m, mod, t, th):
        core = self._core
        var = _tone(mod[core], t, VOR_TONE_HZ)
        m.var_depth = abs(var)
        m.var_snr_db = _tone_snr_db(mod[core], VOR_TONE_HZ)
        # Reference: the subcarrier as an analytic signal (positive band only),
        # then its instantaneous frequency. The phase step between samples n and
        # n+1 is the frequency at n + 0.5, so the times carry that half sample.
        spec = np.fft.fft(mod)
        f = np.fft.fftfreq(len(mod), 1.0 / CHANNEL_RATE)
        band = np.abs(f - VOR_SUBCARRIER_HZ) <= VOR_SUBCARRIER_HALF_BW_HZ
        sub = np.fft.ifft(np.where(band, 2.0 * spec, 0.0))
        inst = np.angle(sub[1:] * np.conj(sub[:-1])) * CHANNEL_RATE / (2 * np.pi)
        inst_t = (np.arange(len(inst)) + 0.5) / CHANNEL_RATE
        start, stop = core.start, core.stop
        fi = inst[start:stop] - np.mean(inst[start:stop])
        ti = inst_t[start:stop]
        ref = _tone(fi, ti, VOR_TONE_HZ)
        m.ref_deviation_hz = abs(ref)
        m.ref_snr_db = _tone_snr_db(fi, VOR_TONE_HZ)
        # The variable tone lags the reference by the radial.
        m.radial = wrap360(math.degrees(np.angle(ref) - np.angle(var)))
        m.checks["var_snr"] = m.var_snr_db >= th.min_tone_snr_db
        m.checks["ref_snr"] = m.ref_snr_db >= th.min_tone_snr_db
        m.checks["var_depth"] = th.vor_depth[0] <= m.var_depth <= th.vor_depth[1]
        m.checks["ref_deviation"] = (
            th.vor_deviation_hz[0] <= m.ref_deviation_hz <= th.vor_deviation_hz[1]
        )

    def _decode_loc(self, m, mod, t, th):
        a90 = _tone(mod, t, LOC_LOW_HZ)
        a150 = _tone(mod, t, LOC_HIGH_HZ)
        m.ddm = abs(a90) - abs(a150)
        m.sdm = abs(a90) + abs(a150)
        # The SNR of the stronger tone: off course one tone fades, by design.
        strong = LOC_LOW_HZ if abs(a90) >= abs(a150) else LOC_HIGH_HZ
        m.loc_snr_db = _tone_snr_db(mod, strong)
        m.checks["loc_snr"] = m.loc_snr_db >= th.min_tone_snr_db
        m.checks["sdm"] = th.loc_sdm[0] <= m.sdm <= th.loc_sdm[1]


class Validator:
    """Holds the bearing-stability history across blocks for one tuning."""

    def __init__(self, thresholds=None):
        self.th = thresholds or Thresholds()
        self.history = []

    def reset(self):
        self.history = []

    def accept(self, m):
        """True when the block's checks pass and, for a VOR, the bearing has
        been stable over the last ``stable_count`` passing blocks."""
        if not m.ok:
            self.history = []
            return False
        if m.kind != VOR:
            return True
        self.history.append(m.radial)
        self.history = self.history[-self.th.stable_count:]
        if len(self.history) < self.th.stable_count:
            m.checks["stable"] = False
            return False
        c = np.mean(np.exp(1j * np.radians(self.history)))
        centre = math.degrees(np.angle(c))
        spread = max(abs(wrap180(b - centre)) for b in self.history)
        m.checks["stable"] = spread <= self.th.stable_deg
        return m.checks["stable"]


def vor_needle(bearing_to, course):
    """CDI (-1..1, + = course to the right, fly right) and TO/FROM (1 TO,
    2 FROM) for a VOR, from the bearing to the station and the OBS course.
    Full scale is 10 degrees."""
    d = wrap180(bearing_to - course)
    if abs(d) <= 90.0:
        tf, dev = 1, d
    else:
        # FROM: the radial the aircraft is on is bearing_to - 180; off to the
        # right of the outbound course means the course line is to the left.
        tf, dev = 2, wrap180(course - (bearing_to - 180.0))
    return max(-1.0, min(1.0, dev / 10.0)), tf


def loc_needle(ddm):
    """CDI for a localizer front course: 0.155 DDM is full scale."""
    return max(-1.0, min(1.0, ddm / LOC_FULL_SCALE_DDM))


def cu8_to_complex(raw):
    """rtl_sdr's output: interleaved unsigned 8-bit I and Q, centred on 127.5."""
    a = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 127.5
    return (a[0::2] + 1j * a[1::2]) / 127.5
