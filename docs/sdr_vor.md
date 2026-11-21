# sdr_vor: a software VOR and localizer receiver on an RTL-SDR

SDRVOR1 (AER-6141), capability CAP-215.

**Supplemental, not a certified nav receiver, day VFR situational awareness.**
It is never an approach or IFR source, and it has no glideslope. Every surface
that names it says so: the plugin and connection file headers, its status
page, and the log line each time it tunes.

## What it does

The plugin is one NAV radio slot, NAV2 by default (`nav_slot`), so it never
shares keys with a real NAV1 radio. It tunes an RTL-SDR dongle to the
frequency the pilot sets on the bus, decodes the VOR or localizer, and writes
the slot's existing keys. The HSI shows it through `NAVSRC`, as it shows any
NAV radio. Nothing is added on the display side.

| Key | Written | Meaning here |
|---|---|---|
| `NAV2DH` | yes | 1 when the decoder vouches for the signal (the rule below), else 0 |
| `NAV2CDI` | yes | VOR: deviation from `NAV2CRS`, 10 deg full scale. LOC: DDM / 0.155. Failed while not valid |
| `NAV2TF` | yes | 1 TO, 2 FROM. 0 on a localizer, while not valid, or with a failed course |
| `NAV2TYPE` | yes | 1 VOR, 2 LOC, from the channel plan. 0 when not tuned |
| `NAV2GSF` | yes | always 1: no glideslope, so compute keeps `NAV2GSV` 0 |
| `VOR2BRG` | yes | bearing TO the station, magnetic (radial + 180). Failed unless a valid VOR |
| `NAV2FREQ` | read | the active frequency, MHz. New key, see below |
| `NAV2CRS` | read | the OBS course, pilot-set as before |
| `NAV2CDIV` | no | compute writes it from `NAV2DH` |

The brief named `NAVnCDIV` as a key to write. Compute already writes it from
`NAV2DH` (`connections/compute.yaml`, the X-Plane shape). Writing it from the
plugin as well would give the key two writers, so the plugin writes `NAV2DH`.
The tests check `NAV2CDIV` and `CDIV` through the real gateway, and they check
that every key in the table has one writer. Enable it only when nothing else
writes the slot. X-Plane writes NAV2, and so would a CAN-FiX NAV2 radio.

### The one gap: the active frequency

No key held a NAV radio's frequency. CAN-FiX has one: 1220 "VOR/ILS
Frequency", index 0 is the current frequency. This change adds `NAV1FREQ` and
`NAV2FREQ` (`database/ahrs.yaml`): float MHz, 0 = none, max 117.95, initial 0,
tol 0 because it is a setting written on change. The pilot is the writer.
pyEfis has no tuning page yet, so for now the frequency is set with `fixgwc`
(`w NAV2FREQ 113.9`). A tuning control is a separate item. The roadmap 9.1
design-record block is on AER-6141 for a session to land.

## Signal model

Written clean-room from ICAO Annex 10 Volume I. RB-Avionics' rb-vor source was
not read. Its licence is undeclared.

- **VOR (3.3).** The bearing is the phase of the 30 Hz AM "variable" tone
  against the 30 Hz FM "reference" on the 9,960 Hz subcarrier (+/-480 Hz). The
  variable lags the reference by the radial. The 1,020 Hz ident is not decoded.
- **Localizer (3.1.3).** DDM = depth(90 Hz) - depth(150 Hz). 90 Hz
  predominates left of course seen from the approach, so positive DDM means fly
  right, which is positive `CDI`. 0.155 DDM is full scale. Back course is not
  handled.
- **Channel plan.** 108.00-111.95 MHz: localizers on odd tenths, VORs on even.
  112.00-117.95 MHz: VORs. Any other value releases the radio.

Decoding, `fixgw/plugins/sdr_vor/dsp.py`: the receiver samples 240 kHz, tuned
60 kHz below the station, which keeps the carrier off the dongle's DC spike.
The decoder finds the carrier within +/-6 kHz, about 55 ppm, and cuts a
+/-12 kHz channel. It takes the AM envelope and measures each tone as one DFT
bin over 0.9 s. That window holds 27 cycles of 30 Hz, 81 of 90 Hz and 135 of
150 Hz. Each block is 1 s, with a decode every 0.5 s. Each decode takes about
40 ms on the development machine. It has not been timed on a Pi yet.

## Validity rule

Noise decodes as a centred needle. A block is vouched for only if every check
passes:

1. **C/N at least 15 dB.** Carrier power over noise in a 25 kHz channel. The
   noise is measured 14-30 kHz either side of the carrier, clear of the
   channel's sidebands and of a 50 kHz neighbour's.
2. **Both components present.** A VOR needs the 30 Hz variable and the 30 Hz
   reference, each at least 20 dB over the neighbouring bins. A localizer needs
   the stronger of 90 and 150 Hz at 20 dB.
3. **Plausible modulation.** For a VOR, variable depth 0.15-0.45 (30% nominal)
   and reference deviation 350-620 Hz (480 nominal). For a localizer, SDM
   0.25-0.65 (40% nominal). A localizer tuned as a VOR fails, and so does a VOR
   tuned as a localizer.
4. **Stable bearing, VOR only.** The last 3 passing blocks are within 2 deg of
   their mean. A failed block restarts the count, so a new station shows about
   1.5 s after tuning. At 120 kt this check flags inside about 1 nm abeam the
   station, which is where a real receiver's needle swings.

If the course key `NAV2CRS` is failed or bad, the bearing stays, and the needle
and TO/FROM drop.

**How the floor was set.** `tests/plugins/sdr_vor/sweep.py` decodes 200
one-second blocks at each C/N, with a random radial or DDM and a random carrier
offset of +/-5 kHz:

| C/N dB | VOR error p99 / max, deg | LOC error p99 / max, % of full scale | blocks passing, VOR / LOC | worst passing block, VOR deg / LOC % |
|---|---|---|---|---|
| 8 | 2.03 / 2.18 | 11.1 / 11.9 | 0% / 0% | - |
| 12 | 0.77 / 0.87 | 5.4 / 6.3 | 0% / 0% | - |
| 14 | 0.58 / 0.69 | 3.9 / 4.6 | 0% / 0% | - |
| 15 | 0.51 / 0.62 | 3.3 / 4.0 | 8% / 7.5% | 0.45 / 2.3 |
| 16 | 0.45 / 0.55 | 2.8 / 3.4 | 100% / 100% | 0.55 / 3.4 |
| 20 | 0.29 / 0.35 | 1.5 / 2.0 | 100% / 100% | 0.35 / 2.0 |

The localizer sets the floor. At 13 dB and below, envelope detection shrinks
the DDM, and the worst block is more than 5% of full scale off (5.4% at 13 dB). The 15 dB floor sits above that
edge. Every block that passed in the sweep was within 2 deg and 5%. The worst
was 0.55 deg and 3.4%. The full output is in the PR.
`test_a_block_that_passes_near_the_floor_is_within_tolerance` repeats the
check at 14-16 dB. On pure noise (400 blocks of each kind), the C/N estimate
never rose above -29.9 dB and no tone reached 10 dB. None passed.

These are synthetic signals with white noise. Real RF adds things the sweep
does not model: adjacent-channel signals in the guard band, multipath, dongle
overload and ppm drift. SDRVOR2 records real captures and checks the rule
against them.

## SDR access

The plugin runs `rtl_sdr` (osmocom rtl-sdr) as a subprocess. Its 8-bit I/Q
comes in on stdout. This is the `rtl_433` plugin's precedent, and nothing is
linked into fix-gateway. Retuning restarts the process. The dongle is shared:
with no frequency selected (0, or not a nav channel), the process is stopped
and the device is free. A missing `rtl_sdr` binary flags the slot and does not
stop the gateway. `replay:` reads an `rtl_sdr` capture file instead of the
radio. The tests use this, and so will SDRVOR2. The snap does not package
`rtl_sdr` yet. Install the `rtl-sdr` package on the host.

## Licence check

fix-gateway is GPL-2.0-or-later.

- `rtl_sdr` / librtlsdr (osmocom): GPL-2.0-or-later. It is executed, not
  linked, and it is compatible either way.
- numpy: BSD-3-Clause. It was already a dependency. No dependency is added.
- RB-Avionics rb-vor: not read and not used. Only its README's public
  behaviour is known: supplemental, day VFR.

## Tests

- `tests/plugins/sdr_vor/siggen.py` generates the synthetic signals. It is
  built from the Annex 10 text and does not import the decoder. It can also
  write `.cu8` files.
- `test_dsp.py` checks the bearing at every radial 0-359 and the DDM across
  full scale. It checks noise, a missing component, the wrong signal type, the
  floor from both sides, soundness at the floor, the ppm window, the needle and
  TO/FROM geometry, and stability.
- `test_plugin.py` checks the slot's keys from replayed captures. It checks
  TO/FROM on both sides of the station, flagging on noise and below the floor,
  slot 1, and refused configs. It runs a stand-in `rtl_sdr` to check tuning,
  retuning and release. It runs the real gateway with compute to check
  `NAV2CDIV`/`CDIV` and one writer per key.
