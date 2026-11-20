# AER-6084 (OBS2): the TONIE example, flown in OBS

The brief's example (`makerplane/briefs/gps_obs_mode.md`): "Cleared to TONIE,
intercept the 270 course inbound." The plan is FLOUT, TONIE, OSH, with TONIE
active on the 310 course from FLOUT.

- `tonie_plan_view.png`: the flown track, blue in normal mode
  (`FPLSTATE` 1) and orange in OBS (`FPLSTATE` 4), over the plan (grey
  dashes) and the 270 course line through TONIE (dotted).
- `tonie_strips.png`: `FPLSTATE`, `FPLTF`, `FPLCRS` and `FPLXTK` against time,
  with the three events marked.
- `tonie_flight.csv`: one row a second, the same run.

What happens: 30 nm out on the 310 course the pilot sends `OBS 270`.
`FPLCRS` reads 270 and `FPLXTK` about -19 nm (left of the line). The aircraft
intercepts at 45 deg and is centred 3.4 nm before TONIE. Over TONIE, `FPLTF`
goes TO to FROM, and TONIE stays the active waypoint with no `SEQ`. 4 nm past
the fix the pilot sends `OBS OFF`. The engine posts `SEQ TONIE -> OSH`, and the
aircraft turns back and joins TONIE -> OSH.

How it was made. The aircraft is the synthetic tier's point mass
(`fixgw.sim.aircraft`, 120 kt TAS, no wind), steering on the engine's own
`FPLCRS` and `FPLXTK`, as a coupled autopilot in GPSS would. The run is
`fly()` in `tests/sim/test_obs_tonie.py`, whose tests check the same
flight. The coordinates are synthetic: TONIE and OSH are the brief's teaching
example and were not looked up. MAGVAR is -12 (12 deg east). The synthetic
reference (`fixgw/sim/refnav.py`) has no OBS mode, so it is not compared.

Regenerate, from the repo root:

```
PYTHONPATH=src:. python3 doc/evidence/aer-6084-obs/plot_tonie.py
```
