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
