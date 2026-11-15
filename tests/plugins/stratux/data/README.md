# Stratux `/getStatus` fixtures (GDL4, AER-5652)

| File | Where it came from |
|---|---|
| `getstatus_flightbox_3d.json` | **Recorded** from the bench FlightBox (Stratux v1.6r1-eu023, Pi 2 B, USB u-blox 7) at 10.110.10.169, from the Pi 5, on 2026-11-15: a 3D fix without SBAS, 7 satellites in the solution. |
| `getstatus_v1.4r2_stratux_notes.json` | Recorded by the Stratux project: the example response in cyoung/stratux `notes/app-vendor-integration.md` (commit 4afdebbc5a61d07582e97d81e95c0e573efbe0a4), copied verbatim. Stratux is BSD-3-Clause. |
| `getstatus_eu023_fix_sbas.json`, `getstatus_eu023_no_fix.json`, `getstatus_eu023_booting.json` | **Built, not recorded.** Every field of the `status` struct in b3nn0/stratux `main/gen_gdl90.go` at tag `v1.6r1-eu023` (the version on the bench FlightBox), in struct order, with values set to the states that `updateStatus()`, `isGPSValid()` and `cputemp.go` produce: a 3D SBAS fix; no fix (accuracy 999999); booting (GPS disconnected, CPU temperature -99). |

Only the 3D-fix state could be recorded from the bench box: producing no
fix, a disconnected GPS or a booting receiver means unplugging or rebooting
Bill's hardware bench. The built files cover those states. To record more,
run this from the Pi 5:

    curl -s http://10.110.10.169/getStatus > getstatus_flightbox_<state>.json

# GDL90 frames from the bench FlightBox (GDL1, AER-5650)

| File | Where it came from |
|---|---|
| `gdl90_eu023_bench_ownship.jsonl` | **Recorded** from the bench FlightBox (Stratux v1.6r1-eu023, 10.110.10.169) on 2026-11-15, on the Pi 5 with the stratux plugin off. Each line is one UDP datagram exactly as received (`datagram`, hex, still framed and stuffed) holding a 0x0A or a 0x0B, with the nearest `/getSituation` reading from the same box (`situation`, `situation_dt` seconds away): what the FlightBox web UI shows. 12 lines, roughly one of each message every 11 s, taken from a 60 s capture of 1763 datagrams. |
| `gdl90_eu023_bench_traffic.jsonl` | **Recorded** in the same capture: the first datagram holding one Traffic Report (a real target, call sign `ENY3839`), unedited. It pins Stratux's NUL padding of a short call sign. |
| `bench_log_ownship_2026-11-15.txt` | The live bench log for GDL1: the branch's plugin and database on the Pi 5 against the FlightBox for 30 s, every STX key beside the FlightBox's own `/getSituation`. Produced by `bench_log_ownship.py` (this directory); the header gives the command. |

The box was stationary on the bench with a 5-satellite fix, its
`GDL90MSLAlt_Enabled` setting on (so 0x0B is MSL), `OwnshipModeS` F00000 (no
ICAO address set) and its pressure sensor enabled. Recorded with
`capture_gdl90.py` (this directory):

    python3 capture_gdl90.py --seconds 60 --stratux 10.110.10.169 --out cap
    curl -s http://10.110.10.169/getSettings > settings.json

The fixture lines were then picked from `cap.gdl90.jsonl` and
`cap.situation.jsonl`; no datagram was edited.

# FIS-B uplinks (GDL2, AER-5651)

### `dump978-sample-2015.txt.gz`

dump978's `sample-data.txt.gz`, unchanged: about 1,100 demodulated UAT
messages from RF captures, 704 of them ground uplinks (lines starting `+`,
the 432-byte uplink payload in hex). The uplinks are Bay Area FIS-B ground
stations, 23 to 25 January 2015.

- Source: https://github.com/mutability/dump978, file `sample-data.txt.gz`,
  at commit `c7c4ad2f953692eadc5702aa8f059ea93fba2934`.
- Licence: GPL-2.0-or-later (dump978), compatible with this repository.

### `fisb_oracle.json`

What fisb-decode, an independent decoder written against DO-358B, makes of
every text product in that file: product 413 text, TWGO text records, and the
one segmented NOTAM-TFR joined by its Unsegmenter. `test_fisb.py` requires
fixgw's decoder to agree with it on all 704 uplinks.

To regenerate:

```sh
git clone https://github.com/rand-projects/fisb-decode
git -C fisb-decode checkout c3d3b0473429336b1a98672b2e8b276c24d49765
python3 make_fisb_oracle.py fisb-decode dump978-sample-2015.txt.gz > fisb_oracle.json
```

fisb-decode is BSD-2-Clause Plus Patent and is not a dependency; only its
output is committed.
