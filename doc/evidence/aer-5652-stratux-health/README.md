# GDL4 bench run: Stratux receiver health keys against the FlightBox (AER-5652)

2026-11-15, on the Pi 5 hardware bench (10.110.10.199). The receiver is the bench
FlightBox: Stratux v1.6r1-eu023 on a Pi 2 B with a USB u-blox 7, wired at
10.110.10.169. It was running Developer Mode and 1090ES. `bench.py` runs the branch's real
`stratux` plugin (head `f192d77`, copied into `~/bench-scratch/aer5652/src` with
`git archive`) in the Pi 5's fixgw venv, against the shipped `database.yaml`.
The plugin receives the FlightBox's GDL90 on UDP 4000 with `source: 10.110.10.169`
and `traffic: false`. Each second, `bench.py` logs the count of `0x4C` AHRS messages
decoded (each one writes `PITCH`) and every STX key. The live fixgw on the Pi 5 has
`STRATUX: false` and was not touched.

## AHRS stream rate, polling off and on

    for ph in off on off; do python bench.py --status $ph --seconds 60 --out phase_<time>_$ph.jsonl; done

The three runs were back to back, under `flock /tmp/pyefis-bench.lock`.

| Run | AHRS msgs/s, seconds 6-60 | min / max | STX keys |
|---|---|---|---|
| `phase_120533_off` | 20.000 (1100 in 55 s) | 20 / 20 | never written, all old |
| `phase_120633_on`  | 20.000 (1100 in 55 s) | 19 / 21 | fresh every second, none old after 2 s; 60 polls |
| `phase_120733_off` | 20.000 (1100 in 55 s) | 19 / 21 | never written, all old |

The 19/21 pairs are a message landing either side of a second boundary. Polling at
1 Hz left the AHRS stream unchanged. In the first 3-5 s of each run the AHRS count
was near 0. The off run after the on run shows the same pattern (1 0 0 0 6), so it
comes from restarting the listener, not from polling. The 6-60 s window leaves it
out.

## Keys against the FlightBox web UI

`ui_on.jsonl` is a 50 s polling-on run. During it, the FlightBox status page was
captured three times with headless Chromium from the NAS (whose clock agrees with
the Pi 5's to within 0.2 s). Shots 1 and 3, against the key row nearest to each:

| Shot | Web UI | Keys |
|---|---|---|
| `flightbox_ui_1.png` | 3D GPS, 9.2 m; 7 in solution, 8 seen; CPU 51.9 C | `STXGPSFIX` 3, `STXGPSACC` 30.18 ft (9.20 m), `STXSATS` 7, `STXSATSV` 8, `STXCPUT` 51.92 |
| `flightbox_ui_3.png` | 3D GPS, 5.9 m; 7 in solution, 8 seen; CPU 51.9 C | `STXGPSFIX` 3, `STXGPSACC` 19.36 ft (5.90 m), `STXSATS` 7, `STXSATSV` 8, `STXCPUT` 51.92 |

`STXGPS`, `STXAHRS`, `STXBARO` and `STXALIVE` were true throughout. The status page
doesn't show the AHRS or baro flags; the recorded `/getStatus` has
`IMUConnected` and `BMPConnected` true.

To capture the page:

    chrome-headless-shell --no-sandbox --disable-gpu --window-size=1280,520 \
      --virtual-time-budget=4000 --screenshot=ui.png "http://10.110.10.169/#/"
