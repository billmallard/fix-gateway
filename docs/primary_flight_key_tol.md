# Primary flight keys and their time-to-live

CERT2 (AER-5032). A FIX key reads `old` once its last write is older than its
`tol` (ms). With `tol: 0`, the `define_item` default, the key never goes old,
so a source that stops writing leaves its last value on the glass with no flag.
This table lists every key the shipped pyEfis configuration shows the pilot on
the PFD, the HSI and the engine pages (EMS, EMS2), with its `tol` in the
shipped database. `tests/test_primary_key_tol.py` fails if any key in the
first table has `tol: 0`, and it fails if this table and the test's lists drift
apart.

How the list was built: pyEfis `dev` at 8017016a. It covers the default screen
list (`screens/default_list.yaml`: PFD, EMS, EMS2), with every include and
`preferences.yaml` substitution resolved, plus the keys each instrument class
subscribes to in Python. Most PFD and HSI keys are hard-coded in the
instrument, not in the YAML. Items the shipped preferences leave disabled are
not built and are not listed: the VSI needle, wind, the pressure readouts
(DALT, PALT), trim and dimmer.

The "Proposed" column holds the values this change sets. They are for Bill to
rule on.

## Displayed values: must have a nonzero tol

| Key | Path | Shown as | TOL on dev (ms) | Proposed (ms) | Reason |
|---|---|---|---|---|---|
| `ALAT` | PFD | slip ball | 300 | no change |  |
| `ALT` | PFD | altimeter tape, SVS | 2000 | no change |  |
| `COMACTFREQ1` | PFD | COM active frequency | 200 | no change |  |
| `COMACTRX1` | PFD | COM RX annunciator | 300 | no change |  |
| `COMACTRXLEVEL1` | PFD | COM receive level | 300 | no change |  |
| `COMACTTX1` | PFD | COM TX annunciator | 300 | no change |  |
| `COMTXPWR1` | PFD | COM transmit power | 300 | no change |  |
| `COMVSWR1` | PFD | COM VSWR | 300 | no change |  |
| `GS` | PFD/HSI | FPM, HSI track-diamond gate | 2000 | no change |  |
| `HEAD` | PFD/HSI | heading box, HSI rose | 2000 | no change |  |
| `IAS` | PFD | airspeed tape | 2000 | no change |  |
| `LAT` | PFD | SVS position, position flag | 2000 | no change |  |
| `LONG` | PFD | SVS position, position flag | 2000 | no change |  |
| `MAGVAR` | PFD | SVS true/magnetic | 60000 | no change |  |
| `PITCH` | PFD | attitude, pitch | 300 | no change |  |
| `ROLL` | PFD | attitude, bank | 300 | no change |  |
| `TAS` | PFD | TAS readout, FPM | 2000 | no change |  |
| `TIMEL` | PFD | local clock | 0 | 2000 | system plugin writes it every second, beside TIMEZ (2000) |
| `TIMEZ` | PFD | UTC clock | 2000 | no change |  |
| `TRACK` | PFD | flight path marker | 2000 | no change |  |
| `VPATH` | PFD | flight path marker | 2000 | no change |  |
| `VS` | PFD | VS tape, FPM | 2000 | no change |  |
| `BRG1` | HSI | bearing pointer 1 (off by default) | 2000 | no change |  |
| `BRG2` | HSI | bearing pointer 2 (off by default) | 2000 | no change |  |
| `CDI` | HSI | lateral deviation bar | 2000 | no change |  |
| `CDIV` | HSI | CDI validity, NAV flag | 2000 | no change |  |
| `COURSE` | HSI | course pointer, CRS readout | 2000 | no change |  |
| `GSI` | HSI | glideslope needle | 2000 | no change |  |
| `GSV` | HSI | GS needle validity | 2000 | no change |  |
| `TOFROM` | HSI | TO/FROM triangle | 2000 | no change |  |
| `TRACKM` | HSI | ground-track diamond | 2000 | no change |  |
| `VOR1BRG` | HSI | CRS push-to-centre | 2000 | no change |  |
| `VOR2BRG` | HSI | CRS push-to-centre | 2000 | no change |  |
| `CAT` | EMS | carb/intake air temp | 2000 | no change |  |
| `CHT11` | EMS | CHT cylinder 1 | 2000 | no change |  |
| `CHT12` | EMS | CHT cylinder 2 | 2000 | no change |  |
| `CHT13` | EMS | CHT cylinder 3 | 2000 | no change |  |
| `CHT14` | EMS | CHT cylinder 4 | 2000 | no change |  |
| `CHTMAX1` | EMS | hottest-CHT highlight | 0 | 2000 | compute `max` of CHT11-14, refreshed on every input write; inputs are 2000 |
| `CURRNT` | EMS | amps | 2000 | no change |  |
| `EGT11` | EMS | EGT cylinder 1 | 2000 | no change |  |
| `EGT12` | EMS | EGT cylinder 2 | 2000 | no change |  |
| `EGT13` | EMS | EGT cylinder 3 | 2000 | no change |  |
| `EGT14` | EMS | EGT cylinder 4 | 2000 | no change |  |
| `EGTMAX1` | EMS | hottest-EGT highlight | 0 | 2000 | compute `max` of EGT11-14, refreshed on every input write; inputs are 2000 |
| `FUELF1` | EMS | fuel flow | 2000 | no change |  |
| `FUELP1` | EMS | fuel pressure | 2000 | no change |  |
| `FUELQ1` | EMS | fuel tank 1 | 2000 | no change |  |
| `FUELQ2` | EMS | fuel tank 2 | 2000 | no change |  |
| `FUELQ3` | EMS | fuel tank 3 | 2000 | no change |  |
| `FUELQT` | EMS | fuel total | 2000 | no change |  |
| `H2OT1` | EMS | coolant temp | 2000 | no change |  |
| `MAP1` | EMS | manifold pressure | 2000 | no change |  |
| `OAT` | EMS | OAT | 2000 | no change |  |
| `OILP1` | EMS | oil pressure | 2000 | no change |  |
| `OILT1` | EMS | oil temp | 2000 | no change |  |
| `TACH1` | EMS | RPM arc/bar | 2000 | no change |  |
| `THR1` | EMS | throttle position | 1000 | no change |  |
| `VOLT` | EMS | volts | 2000 | no change |  |

## Settings and event text: tol 0 is right

These keys are written when the pilot changes them, when an event happens, or
once at start-up. A tol would flag them old a few seconds after every write.
The test does not check them.

| Key | Shown as | TOL on dev (ms) | Why 0 is right |
|---|---|---|---|
| `BARO` | altimeter setting box | 0 | pilot setting, written when the pilot turns the knob |
| `ALTSEL` | selected-altitude box and bug | 0 | pilot setting (altitude bug), written on change |
| `COMACTNAME1` | COM frequency name | 0 | frequency name, written when a frequency is tuned |
| `MAVMSG` | autopilot message line | 0 | autopilot event text, written when the autopilot has news |
| `ACID` | aircraft ID readout | 0 | aircraft identifier, a configured constant |
| `LEADER` | L/F button | 0 | set True at start-up; only the quorum plugin (off) rewrites it |
| `GATEWAY_VERSION` | version readout | 0 | written once at start-up |
| `PYEFIS_VERSION` | version readout | 0 | written once by pyEfis at start-up |
| `EGTBTN1` | EGT Reset Peak button | 0 | EGT mode button |
| `EGTBTN2` | EGT Normalize button | 0 | EGT mode button |
| `EGTBTN3` | EGT Peak button | 0 | EGT mode button |
| `EGTBTN4` | EGT Lean button | 0 | EGT mode button |

## Found while building the table, not changed here

- Some pilot settings do carry a tol, so they read old within two seconds of
  every edit: `HEADBUG`, `NAVSRC`, `BRG1SRC`, `BRG2SRC`, `NAV1CRS`, `NAV2CRS`
  and `COMSQUELCH1`. Only the X-Plane connection streams any of them
  (`HEADBUG`, `NAV1CRS`, `NAV2CRS`); without the sim, they are written on
  change. The HSI's HDG readout shows `HEADBUG`. This is the
  opposite failure, a good value flagged stale. It is left for a separate item.
- The HSI subscribes to `NAVTYPE` (VOR/LOC source label), which the database
  does not define. The HSI handles the missing key and falls back to a label
  built from `NAVSRC`.
