# NEXRAD mosaic on the Pi 5 (GDL3, AER-5937)

`pi5-2026-11-18.txt` is three runs of `bench_nexrad.py` on the bench Pi 5
(10.110.10.199, Raspberry Pi 5 Model B Rev 1.1, 8 GB, Python 3.13.5) at
fix-gateway commit `1948806`, with pyEfis running on the glass (load average
0.06 to 0.20 at the start of each run). The script ran from a copy of the
tree in `~/bench-scratch/AER-5937`, which was deleted afterwards; it did not
touch the live gateway, pyEfis, or the checkout.

| Measure | Pi 5 |
|---|---|
| Live decode, the tg30 capture (963 uplinks, 457 s, 1,985 NEXRAD APDUs) | 0.05 s CPU: 0.011% of one core |
| Full mosaic, worst case: 1,890 CONUS + 46,200 regional blocks | 27.1 MiB held (tracemalloc); RSS +26.5 MiB |
| Building it | 11.8 us per block |
| `purge()`, which the plugin calls once a second | 18.7 ms: 1.9% of one core |
| `current()`, all blocks / CONUS only | 18-20 ms / 4.7 ms |

The worst case fills every CONUS (scale 1) and every regional (scale 0)
position from 20N to 55N and 130W to 60W with a real run-length block from
tg30, re-addressed. A ground station sends regional blocks only near itself
(tg30 heard 226 per image), so a real mosaic holds far fewer regional blocks
than this. CONUS alone is 1,890 blocks.

To regenerate, from a fix-gateway checkout on the Pi:

```sh
PYTHONPATH=src python3 doc/evidence/aer-5937-nexrad-pi5/bench_nexrad.py \
    tests/plugins/stratux/data/fisb-decode-tg30-2020.978.gz
```

The bench's FlightBox hears no UAT where it sits (AER-5651), so this is the
decoder on a recorded capture, not on today's FIS-B.
