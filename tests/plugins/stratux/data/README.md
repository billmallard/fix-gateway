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
