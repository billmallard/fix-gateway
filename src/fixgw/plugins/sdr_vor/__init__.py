"""Software VOR and localizer receiver on an RTL-SDR dongle (SDRVOR1, AER-6141).

SUPPLEMENTAL, NOT A CERTIFIED NAV RECEIVER: day VFR situational awareness
only. Never an approach or IFR source. No glideslope.

The plugin is one NAV radio slot (``nav_slot``, NAV2 by default so it never
shares keys with a real NAV1 radio). It tunes to the frequency the pilot sets
in ``NAV<n>FREQ``, decodes the station, and writes the slot's existing keys:

    NAV<n>DH    1 when the decoder vouches for the signal, else 0. Compute
                copies it into NAV<n>CDIV, so the HSI hides the needle.
    NAV<n>CDI   VOR: deviation from NAV<n>CRS, 10 deg full scale.
                LOC: DDM / 0.155. Failed while the signal is not valid.
    NAV<n>TF    1 TO, 2 FROM; 0 on a localizer or while not valid.
    NAV<n>TYPE  1 VOR, 2 LOC (from the channel plan); 0 when not tuned.
    NAV<n>GSF   always 1: this receiver has no glideslope.
    NAV<n>SUPP  always 1: the slot's source is supplemental (SDRVOR3). Compute
                selects it into NAVSUPP, and the HSI marks the source.
    VOR<n>BRG   bearing TO the station, magnetic; failed unless a valid VOR.

It reads NAV<n>FREQ and NAV<n>CRS, and writes neither.

The radio is ``rtl_sdr`` run as a subprocess, writing raw 8-bit IQ to its
stdout; nothing is linked. With no frequency selected (0, or not a VOR or
localizer channel) the subprocess is stopped, which frees the dongle for
another program. ``replay:`` reads an ``rtl_sdr`` capture file instead of the
radio, for tests and for checking recorded signals. Design note:
docs/sdr_vor.md.
"""

import math
import os
import select
import subprocess
import threading
import time
from collections import OrderedDict

import fixgw.plugin as plugin
from fixgw.plugins.sdr_vor import dsp

SUPPLEMENTAL = (
    "supplemental, not a certified nav receiver, day VFR situational awareness"
)

# Seconds between decodes. Each decode covers the last DecoderConfig.window_s.
HOP_S = 0.5


def rtl_sdr_path(configured):
    if configured:
        return configured
    if "SNAP" in os.environ:
        return os.path.join(os.environ["SNAP"], "usr", "bin", "rtl_sdr")
    return "rtl_sdr"


class RtlSdrSource:
    """rtl_sdr tuned ``tune_offset`` below the station, streaming to stdout."""

    def __init__(self, parent, freq_hz):
        cfg = parent.config
        self.parent = parent
        lo = int(round(freq_hz - parent.decoder.cfg.tune_offset))
        self.command = [
            rtl_sdr_path(cfg.get("rtl_sdr")),
            "-d",
            str(cfg.get("rtl_device", 0)),
            "-f",
            str(lo),
            "-s",
            str(int(parent.decoder.cfg.sample_rate)),
        ]
        if cfg.get("gain") is not None:
            self.command += ["-g", str(cfg["gain"])]
        if cfg.get("ppm"):
            self.command += ["-p", str(int(cfg["ppm"]))]
        self.command.append("-")
        self.process = subprocess.Popen(
            self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        parent.status["rtl_sdr starts"] += 1
        parent.status["rtl_sdr pid"] = self.process.pid

    def read(self, n, timeout):
        """Up to n bytes; b"" on timeout; None when the process has ended."""
        out = self.process.stdout
        ready, _, _ = select.select([out], [], [], timeout)
        if not ready:
            return b""
        data = os.read(out.fileno(), n)
        return data if data else None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()
        self.parent.status["rtl_sdr pid"] = None


class ReplaySource:
    """An rtl_sdr capture file, read at real time or as fast as possible."""

    def __init__(self, parent, path):
        self.f = open(path, "rb")
        self.loop = parent.config.get("replay_loop", True)
        self.realtime = parent.config.get("replay_realtime", True)
        self.rate = parent.decoder.cfg.sample_rate * 2  # bytes per second
        self.started = time.monotonic()
        self.sent = 0

    def read(self, n, timeout):
        if self.realtime:
            due = self.started + self.sent / self.rate - time.monotonic()
            if due > 0:
                time.sleep(min(due, timeout))
                if due > timeout:
                    return b""
        data = self.f.read(n)
        if len(data) < n and self.loop:
            self.f.seek(0)
            data += self.f.read(n - len(data))
        if not data:
            return None
        self.sent += len(data)
        return data

    def close(self):
        self.f.close()


class MainThread(threading.Thread):
    def __init__(self, parent):
        super().__init__(name="sdr_vor", daemon=True)
        self.parent = parent
        self.getout = False

    def run(self):
        p = self.parent
        try:
            while not self.getout:
                p.step()
        finally:
            p.release()
            p.publish_none()

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta=None):
        super().__init__(name, config, config_meta)
        slot = config.get("nav_slot", 2)
        if slot not in (1, 2):
            raise ValueError(f"nav_slot must be 1 or 2, not {slot!r}")
        self.slot = slot
        rate = config.get("sample_rate", 240000)
        # The sample rates rtl_sdr accepts.
        if not (225000 < rate <= 300000 or 900000 < rate <= 3200000):
            raise ValueError(f"sample_rate {rate} is outside rtl_sdr's ranges")
        n = str(slot)
        self.keys = {
            "freq": f"NAV{n}FREQ",
            "crs": f"NAV{n}CRS",
            "dh": f"NAV{n}DH",
            "cdi": f"NAV{n}CDI",
            "tf": f"NAV{n}TF",
            "type": f"NAV{n}TYPE",
            "gsf": f"NAV{n}GSF",
            "supp": f"NAV{n}SUPP",
            "brg": f"VOR{n}BRG",
        }
        self.decoder = dsp.Decoder(
            dsp.DecoderConfig(
                sample_rate=float(config.get("sample_rate", 240000)),
                tune_offset=float(config.get("tune_offset", 60000)),
            )
        )
        self.thresholds = dsp.Thresholds()
        self.validator = dsp.Validator(self.thresholds)
        self.block_bytes = self.decoder.block_len * 2
        self.hop_bytes = int(round(HOP_S * self.decoder.cfg.sample_rate)) * 2
        self.source = None
        self.tuned_mhz = None
        self.kind = None
        self.buffer = bytearray()
        self.last_publish = 0.0
        self.status = OrderedDict()
        self.status["Notice"] = SUPPLEMENTAL
        self.status["Slot"] = f"NAV{n}"
        self.status["Tuned MHz"] = None
        self.status["Valid"] = False
        self.status["C/N dB"] = None
        self.status["Failed checks"] = []
        self.status["rtl_sdr pid"] = None
        self.status["rtl_sdr starts"] = 0
        self.status["Decodes"] = 0
        self.thread = MainThread(self)

    # -- tuning --------------------------------------------------------------

    def selected_mhz(self):
        value = self.db_read(self.keys["freq"])
        freq, fail = value[0], value[4]
        if fail or not freq:
            return None
        return freq if dsp.navaid_kind(freq) else None

    def retune(self, mhz):
        self.release()
        self.validator.reset()
        self.buffer = bytearray()
        self.tuned_mhz = mhz
        self.kind = dsp.navaid_kind(mhz) if mhz else None
        self.status["Tuned MHz"] = mhz
        if mhz is None:
            self.log.info(f"NAV{self.slot}: no frequency, SDR released")
            return
        replay = self.config.get("replay")
        try:
            if replay:
                self.source = ReplaySource(self, replay)
            else:
                self.source = RtlSdrSource(self, mhz * 1e6)
        except OSError as e:
            self.log.warning(f"cannot open the SDR source: {e}")
            self.source = None
            return
        kind = "VOR" if self.kind == dsp.VOR else "LOC"
        self.log.info(f"NAV{self.slot} tuned {mhz:.2f} MHz ({kind}); {SUPPLEMENTAL}")

    def release(self):
        if self.source is not None:
            self.source.close()
            self.source = None

    # -- one pass of the thread ----------------------------------------------

    def step(self):
        mhz = self.selected_mhz()
        if mhz != self.tuned_mhz:
            self.retune(mhz)
            self.publish_invalid()
        if self.source is None:
            # Nothing tuned (or the source would not open): keep the slot
            # flagged and fresh, and look for a frequency again shortly.
            if time.monotonic() - self.last_publish >= HOP_S:
                self.publish_invalid()
            time.sleep(0.1)
            return
        # Read no further than one block, so a decode always sees exactly one
        # block and the buffer stays aligned to I/Q byte pairs.
        data = self.source.read(self.block_bytes - len(self.buffer), HOP_S)
        if data is None:
            self.log.warning("SDR source ended; reopening")
            self.retune(self.tuned_mhz)
            self.publish_invalid()
            time.sleep(1.0)
            return
        self.buffer += data
        if len(self.buffer) < self.block_bytes:
            # Filling, or the radio has stalled: no needle on old samples.
            if time.monotonic() - self.last_publish >= HOP_S * 2:
                self.publish_invalid()
            return
        block = dsp.cu8_to_complex(bytes(self.buffer))
        del self.buffer[: self.hop_bytes]
        self.process(block)

    def process(self, iq):
        m = self.decoder.decode(iq, self.kind, self.thresholds)
        valid = self.validator.accept(m)
        self.status["Decodes"] += 1
        self.status["Valid"] = valid
        self.status["C/N dB"] = None if math.isinf(m.cn_db) else round(m.cn_db, 1)
        self.status["Failed checks"] = m.failed()
        self.publish(m, valid)
        return m, valid

    # -- writing the slot ----------------------------------------------------

    def _set(self, key, value, fail=False):
        self.db_get_item(self.keys[key]).set(value, fail=fail)

    def publish(self, m, valid):
        k = self.kind
        if valid and k == dsp.VOR:
            crs = self.db_read(self.keys["crs"])
            bearing_to = dsp.wrap360(m.radial + 180.0)
            cdi, tf = dsp.vor_needle(bearing_to, crs[0])
            crs_bad = bool(crs[3] or crs[4])
            self._set("brg", bearing_to)
            self._set("cdi", cdi, fail=crs_bad)
            self._set("tf", 0 if crs_bad else tf)
            self._set("type", dsp.VOR)
            self._set("gsf", 1.0)
            self._set("supp", 1.0)
            self._set("dh", 0.0 if crs_bad else 1.0)
        elif valid and k == dsp.LOC:
            self._set("brg", 0.0, fail=True)
            self._set("cdi", dsp.loc_needle(m.ddm))
            self._set("tf", 0)
            self._set("type", dsp.LOC)
            self._set("gsf", 1.0)
            self._set("supp", 1.0)
            self._set("dh", 1.0)
        else:
            self.publish_invalid()
            return
        self.last_publish = time.monotonic()

    def publish_invalid(self):
        """Tuned but not vouched for, or not tuned: no needle, no bearing."""
        self._set("dh", 0.0)
        self._set("brg", 0.0, fail=True)
        self._set("cdi", 0.0, fail=True)
        self._set("tf", 0)
        self._set("type", self.kind or 0)
        self._set("gsf", 1.0)
        self._set("supp", 1.0)
        self.last_publish = time.monotonic()

    def publish_none(self):
        self.kind = None
        self.publish_invalid()

    # -- plugin interface ----------------------------------------------------

    def run(self):
        self.log.info(f"sdr_vor on NAV{self.slot}: {SUPPLEMENTAL}")
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(3.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return self.status
