# Stratux `/getStatus` fixtures (GDL4, AER-5652)

| File | Where it came from |
|---|---|
| `getstatus_v1.4r2_stratux_notes.json` | Recorded by the Stratux project: the example response in cyoung/stratux `notes/app-vendor-integration.md` (commit 4afdebbc5a61d07582e97d81e95c0e573efbe0a4), copied verbatim. Stratux is BSD-3-Clause. |
| `getstatus_eu023_fix_sbas.json`, `getstatus_eu023_no_fix.json`, `getstatus_eu023_booting.json` | **Built, not recorded.** Every field of the `status` struct in b3nn0/stratux `main/gen_gdl90.go` at tag `v1.6r1-eu023` (the version on the bench FlightBox), in struct order, with values set to the states that `updateStatus()`, `isGPSValid()` and `cputemp.go` produce: a 3D SBAS fix; no fix (accuracy 999999); booting (GPS disconnected, CPU temperature -99). |

The bench FlightBox (10.110.10.169) did not answer on 2026-11-14, so no
response could be recorded from it. When it is back, record one per state
with

    curl -s http://10.110.10.169/getStatus > getstatus_flightbox_<state>.json

from the Pi 5, and add a test per file beside the built ones.

# GDL90 ownship frames from the bench FlightBox (GDL1, AER-5650)

| File | Where it came from |
|---|---|
| `gdl90_eu023_bench_ownship.jsonl` | **Recorded** from the bench FlightBox (Stratux v1.6r1-eu023, 10.110.10.169) on 2026-11-15, on the Pi 5 with the stratux plugin off. Each line is one UDP datagram exactly as received (`datagram`, hex, still framed and stuffed) holding a 0x0A or a 0x0B, with the nearest `/getSituation` reading from the same box (`situation`, `situation_dt` seconds away): what the FlightBox web UI shows. 12 lines, roughly one of each message every 11 s, taken from a 60 s capture of 1763 datagrams. |

The box was stationary on the bench with a 5-satellite fix, its
`GDL90MSLAlt_Enabled` setting on (so 0x0B is MSL), `OwnshipModeS` F00000 (no
ICAO address set) and its pressure sensor enabled. Recorded with
`capture_gdl90.py` (this directory):

    python3 capture_gdl90.py --seconds 60 --stratux 10.110.10.169 --out cap
    curl -s http://10.110.10.169/getSettings > settings.json

The fixture lines were then picked from `cap.gdl90.jsonl` and
`cap.situation.jsonl`; no datagram was edited.
| `gdl90_eu023_bench_traffic.jsonl` | **Recorded** in the same capture: the first datagram holding one Traffic Report (a real target, call sign `ENY3839`), unedited. It pins Stratux's NUL padding of a short call sign. |
