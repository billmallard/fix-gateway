"""Memory and CPU of the FIS-B NEXRAD mosaic with a full CONUS picture (GDL3,
AER-5937).  Run from a fix-gateway checkout:

    PYTHONPATH=src python3 doc/evidence/aer-5937-nexrad-pi5/bench_nexrad.py \
        tests/plugins/stratux/data/fisb-decode-tg30-2020.978.gz

It measures, in one process:

1. Live decode cost: the whole tg30 capture (about 8 minutes of real FIS-B,
   963 uplinks) through fisb.parse_uat_uplink and Mosaic.ingest, as CPU
   seconds per second of capture.
2. A full CONUS mosaic, the worst case: every CONUS (scale 1) position and
   every regional (scale 0) position from 20N to 55N and 130W to 60W, each a
   real run-length block from tg30 re-addressed to that position.  A ground
   station sends regional blocks only near itself, so a real mosaic holds far
   fewer regional blocks; this bounds it from above.  Reports the memory the
   mosaic holds (tracemalloc, and the process RSS before and after) and the
   cost of the calls the plugin makes on it: purge() once a second, and
   current() for a reader.
"""
import gc
import gzip
import platform
import resource
import sys
import time
import tracemalloc

from fixgw.plugins.stratux import fisb, nexrad


def rss_kib():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def best_of(n, fn):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return min(out)


def main(path):
    with gzip.open(path, "rt") as f:
        lines = [ln.strip() for ln in f if ln.startswith("+")]
    frames = [(bytes.fromhex(ln[1:].split(";")[0]), float(ln.rsplit("t=", 1)[1].rstrip(";")))
              for ln in lines]
    span = frames[-1][1] - frames[0][1]
    print("host", platform.node(), platform.machine(), "python", platform.python_version())
    with open("/proc/cpuinfo") as f:
        models = sorted({ln.split(":", 1)[1].strip() for ln in f if ln.startswith("Model")})
    print("model", "; ".join(models) or "-")

    # 1. live decode cost
    m = nexrad.Mosaic()
    c0, t0 = cpu(), time.perf_counter()
    for data, rx in frames:
        m.ingest(fisb.parse_uat_uplink(data), rx)
    c1, t1 = cpu(), time.perf_counter()
    print("capture: %d uplinks over %.0f s, %d NEXRAD APDUs, %d cells" % (
        len(frames), span, m.counts["apdus"], len(m.cells)))
    print("capture: decode+ingest %.3f s CPU (%.3f s wall) = %.3f%% of one core at the capture's rate" % (
        c1 - c0, t1 - t0, 100.0 * (c1 - c0) / span))

    # real run-length blocks to re-address, per product
    payloads = {63: [], 64: []}
    for data, _rx in frames:
        for a in fisb.parse_uat_uplink(data).apdus:
            if a.product_id in payloads and a.payload[0] & 0x80:
                payloads[a.product_id].append(a.payload)

    def readdress(payload, number):
        return bytes([(payload[0] & 0xF0) | (number >> 16), (number >> 8) & 0xFF, number & 0xFF]) + payload[3:]

    def positions(scale):
        step = nexrad.SCALES[scale]
        out = []
        for row in range(20 * 15, 55 * 15, step):  # 4' rows
            for lon in range(-130 * 60, -60 * 60, 48 * step):
                col = (lon + 360 * 60) // 48
                out.append(row * 450 + col + (step - 1) * 450)  # north-west cell
        return out

    obs_rx = frames[-1][1]
    hour, minute = time.gmtime(obs_rx)[3:5]

    def uplinks_for(pid, scale):
        apdus = []
        src = payloads[pid]
        for i, n in enumerate(positions(scale)):
            apdus.append(fisb.Apdu(pid, 0, None, None, hour, minute, False, None, None, None,
                                   readdress(src[i % len(src)], n)))
        return [fisb.Uplink(0.0, 0.0, True, 1, apdus[i:i + 20]) for i in range(0, len(apdus), 20)]

    conus = uplinks_for(64, 1)
    regional = uplinks_for(63, 0)

    # a clean pass for RSS and build time, then a traced pass for the bytes
    # the mosaic holds (tracemalloc slows the build and adds its own RSS)
    gc.collect()
    rss0 = rss_kib()
    m = nexrad.Mosaic()
    c0 = cpu()
    for u in conus + regional:
        m.ingest(u, obs_rx)
    c1 = cpu()
    gc.collect()
    rss1 = rss_kib()
    del m
    gc.collect()
    tracemalloc.start()
    m = nexrad.Mosaic()
    for u in conus + regional:
        m.ingest(u, obs_rx)
    held, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    counts = m.product_counts()
    assert m.refused == {} and m.counts["stale"] == 0
    print("full mosaic: CONUS %d blocks, regional %d blocks (20N-55N, 130W-60W)" % (
        counts["CONUS"], counts["regional"]))
    print("full mosaic: held %.1f MiB (tracemalloc), RSS %.1f -> %.1f MiB (+%.1f)" % (
        held / 2**20, rss0 / 1024, rss1 / 1024, (rss1 - rss0) / 1024))
    print("full mosaic: built in %.3f s CPU (%.1f us per block)" % (
        c1 - c0, 1e6 * (c1 - c0) / len(m.cells)))
    conus_only = sum(1 for k in m.cells if k[0] == 64)
    print("full mosaic: CONUS alone is %d of %d cells" % (conus_only, len(m.cells)))
    now = obs_rx + 60
    t_purge = best_of(5, lambda: m.purge(now))
    t_cur = best_of(5, lambda: m.current(now))
    t_cur64 = best_of(5, lambda: m.current(now, nexrad.PRODUCT_CONUS))
    print("full mosaic: purge() %.1f ms (once a second: %.1f%% of a core)" % (1e3 * t_purge, 100 * t_purge))
    print("full mosaic: current() %.1f ms, current(CONUS) %.1f ms" % (1e3 * t_cur, 1e3 * t_cur64))
    print("process max RSS %.1f MiB" % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))


if __name__ == "__main__":
    main(sys.argv[1])
