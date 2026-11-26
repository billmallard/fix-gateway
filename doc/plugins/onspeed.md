# OnSpeed plugin

`fixgw.plugins.onspeed` reads the OnSpeed Gen2 AOA computer's `ONSPEED`
serial sentence (10 Hz, 115200 8N1) and writes two keys, plus up to four
more if you turn them on (see [Attitude and air data](#attitude-and-air-data)):

| Key | From | Meaning |
|---|---|---|
| `AOA` | AOA field, 0.1 deg | calibrated angle of attack, deg |
| `AOA.Warn` | stall-warning field, 0.1 deg | stall-warning AOA for the current flap setting, deg |

That pair is what a pitch-limit indicator needs: the margin to the stall
warning is `AOA.Warn - AOA` degrees. Both keys already exist (`AOA` is CAN-FIX
parameter 386); the plugin adds none.

The plugin is listen-only. The sentence also carries pitch, roll, IAS,
pressure altitude, VSI, accelerations, flight path, flap position and the
OnSpeed band. All of them are decoded and shown in the plugin status, so the
decode can be checked against the box's own display. By default none of them
is written: most installations with OnSpeed already have a primary AHRS and
air data, and two writers on one key fight. Pitch, roll, IAS and altitude can
be turned on for an installation where OnSpeed is the source.

The design and its reasons are in pyEfis `docs/fixgw_onspeed_in_spec.md`. The
sentence format is read off OnSpeed-Gen2 `DisplaySerial.ino` at `be1481c`;
OnSpeed Gen3 and other firmware may send something different and are not
covered.

## On the box

- `SERIALOUTFORMAT` must be `ONSPEED` (the shipped default). If it is set to
  `G3X`, the plugin logs "OnSpeed is set to G3X output" once, counts the
  lines as Wrong Format, and publishes nothing.
- `SERIALOUTPORT` `Serial3` (the shipped default) is RS-232 levels: use a USB
  RS-232 adapter. `Serial1` and `Serial5` are 3.3 V TTL.

## Configuration (`connections/onspeed.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `port` | (required) | serial device, e.g. `/dev/ttyUSB0` |
| `baud` | `115200` | the firmware only sends at 115200; anything else logs a warning but is used |
| `min_ias` | `30` | kt. Below this IAS (from the sentence), `AOA` is published `bad` |
| `attitude` | `false` | also write `PITCH` and `ROLL` |
| `airspeed` | `false` | also write `IAS` |
| `altitude` | `false` | also write `ALT`, the sentence's pressure altitude corrected to `BARO` |

The plugin ships off. Turn it on with `ONSPEED: true` under `enabled:` in
`preferences.yaml.custom`. Unknown options log a warning.

**`min_ias` must be at least the box's `MUTE_AUDIO_UNDER_IAS`.** Below that
speed OnSpeed does not stop sending: it sends AOA `+000` and percent lift `00`.
A zero AOA reads as far from stall, so the plugin flags it `bad`. If
`min_ias` is lower than the box's setting, the zero goes out as a good value.
The firmware's compiled default is 30 kt and its shipped config sets 25 kt,
so 30 is safe for both. Set higher errs safe; set lower does not.

Do not run this plugin together with anything else that writes `AOA`: the
X-Plane plugin, or a `compute` AOA function. The gateway logs a second-writer
warning if you do.

## Attitude and air data

With `attitude`, `airspeed` or `altitude` on, the plugin also writes:

| Key | From | Option |
|---|---|---|
| `PITCH` | pitch field, 0.1 deg, + nose up | `attitude` |
| `ROLL` | roll field, 0.1 deg, + right wing down | `attitude` |
| `IAS` | IAS field, 0.1 kt | `airspeed` |
| `ALT` | pressure-altitude field, 1 ft, corrected to `BARO` | `altitude` |

This is for an installation where OnSpeed is the attitude and air-data source,
for example the second lane of a redundant pair, where OnSpeed has its own
pitot, static and AOA ports and its own IMU. **Turn an option on only where
nothing else writes its keys** (the X-Plane, MAVLink, Stratux or demo plugins,
a CAN-FIX AHRS, a `compute` function). The gateway logs a second-writer
warning if two plugins write one key, and the last writer wins. OnSpeed sends
no heading, so `HEAD` has to come from somewhere else (the compass puck).

`ALT`: the firmware computes pressure altitude against 1013.25 hPa
(29.92 inHg) with no altimeter setting, so the plugin applies `BARO` with the
same formula the `compute` plugin's `PALT` function uses
(`fixgw/airdata.py`). `ALT` and a `compute` `PALT` fed from it round-trip to
OnSpeed's pressure altitude. `BARO` is read on every frame, so a knob change
shows on the next frame.

Quality of these outputs:

| Condition | `PITCH`, `ROLL`, `IAS` | `ALT` |
|---|---|---|
| Good frame | value, flags clear | value, flags clear |
| `BARO` `bad` or `fail`, or outside 25 to 33 inHg | as above | value, `bad` |
| Value outside the key's range (pitch past 90 deg, altitude past 60000 ft) | the database's limit, `bad`; logged once | the same |
| IAS below `min_ias` | written good: taxi speed is a real airspeed | written good |
| Frame rejected | nothing written | nothing written |
| No frames for the key's `tol` | `old` | `old` |
| Port cannot be opened, or fails | `fail` | `fail` |

- The sentence carries no validity for these fields. OnSpeed sends its AHRS
  and pressure readings in every frame whatever their state, including while
  the AHRS settles after power-up, so the plugin cannot flag that.
- If the database has no key for an output that is on (an edited older
  database), the plugin logs an error once and turns that output off. With no
  `BARO` key, `ALT` is turned off the same way. `AOA` is unaffected.
- `Outputs` in the status lists every key the plugin is writing.

### Source limitations to know before relying on them

- **Altitude lags in a climb or descent by about 10 seconds' worth, unless
  the box runs firmware with
  [OnSpeed-Gen2#1](https://github.com/billmallard/OnSpeed-Gen2/pull/1).**
  Firmware before that change smooths pressure altitude before sending it,
  much more heavily than anything else (`DisplaySerial.ino` at `be1481c`: an
  exponential filter with `alpha = 2/(20+1)/10` per 100 ms frame, a time
  constant of about 10.4 s, on top of its Kalman filter). At a steady
  1000 fpm the sent altitude is about 173 ft behind; after levelling off the
  error is about 107 ft at 5 s, 67 ft at 10 s and 26 ft at 20 s. VSI is not
  smoothed that way. A pilot levelling off on this altimeter would overshoot,
  and a comparator against another lane's `ALT` would see that much
  disagreement in every climb.
  OnSpeed-Gen2#1 sends the Kalman altitude unsmoothed in the ONSPEED format
  (the G3X format keeps the smoothing). Its host replay
  (`Software/HostTests/DisplaySerialAltLag/`, a synthetic 1000 fpm climb, not
  a flight log) puts the climb lag at 0.3 ft instead of 173.7 ft, and the
  level-off error at about 1 ft by 5 s. Level jitter rises from 0.36 to
  0.50 ft (1 sd). The lag is gone only once that firmware is flashed; the
  sentence carries no firmware version, so the plugin cannot tell which
  build it is reading. Treat `ALT` as lagged until the box is known to run
  it.
- **IAS** is the box's own pitot reading (with its CAS curve applied, if one
  is enabled), smoothed by a 15-sample median and a 10-sample mean at 50 Hz:
  about 0.25 s of delay. In a firmware build with `SPHERICAL_PROBE` defined
  (not the default), the IAS field is the airspeed OnSpeed received from the
  EFIS, so writing it back as `IAS` would be circular. Do not turn on
  `airspeed` with such a build.
- **Pitch and roll** are the AHRS output with no further smoothing, at 10 Hz:
  coarser than a typical AHRS feed. `PITCH` and `ROLL` have a 300 ms `tol`, so
  three missed frames flag them `old`.
- **Lateral G past 1 g drops whole frames** (below), and with them these
  outputs, for as long as the excursion lasts.
- **OnSpeed sends on one serial port.** The firmware writes the sentence to the
  one port named by `SERIALOUTPORT` (`Serial1`, `Serial3` or `Serial5`), so
  two gateways cannot each have their own port without a firmware change;
  share one with an isolated buffer. `Serial1` is also the boom input and
  `Serial3` the EFIS input, so on a box using both, only `Serial5` is free.

## Quality

| Condition | `AOA` | `AOA.Warn` |
|---|---|---|
| Good frame, IAS >= `min_ias` | value, flags clear | value, written only when it changes |
| Good frame, IAS < `min_ias` | value (normally 0.0) with `bad` | as above |
| Stall-warning field `+000` (box not calibrated) | as above | `None` |
| Setpoints out of order (tones-on < fast <= slow <= warning fails) | as above | `None`; counted, logged once |
| Frame rejected (checksum, length, grammar) | nothing written | nothing written |
| No frames for 300 ms | `old` (the database `tol`) | unchanged |
| Port cannot be opened, or fails | `fail` (and `old`) | unchanged |

- `bad` and the value are written in one update, so a consumer never sees
  the low-speed zero flagged good.
- `fail` clears on the first good frame after the port comes back. The plugin
  reopens the port after 1 s, doubling to at most 10 s.
- A rejected frame does not set `bad`: one corrupt frame among good ones would
  flicker an indicator. If the rejections continue, `AOA` goes `old`.
- `AOA.Warn` has no quality flags (aux values have none), so "not available"
  is written as `None`. It is written only on change because every aux write
  notifies every client, and it does change in flight, with flap position.
- On its first frame the plugin replaces any `AOA.Warn` set by an
  initialization file. It does not write `AOA.Stall`, `AOA.0g`, `AOA.Min` or
  `AOA.Max`: OnSpeed does not send them. If an ini file sets them, those
  values stay and are not OnSpeed's.

## Known source limitations

- **Lateral G past 1 g drops frames.** The lateral-G field is three
  characters wide (0.01 g). At 1.00 g or more the firmware prints a wider
  field, every later field shifts, and AOA's position becomes ambiguous. The
  plugin rejects those frames rather than guess, so in a hard skid or rough
  air `AOA` can go `old` for as long as the excursion lasts. That is the
  intended failure: visibly stale, never a wrong AOA.
- **DataMark past 99 is handled.** DataMark (a button-press counter, never
  reset) is the last field, so its overflow moves nothing else and is decoded.
  The status counts those frames.
- **A spin-recovery cue of 10 or more would be misread** as a cue of +1 and a
  wider DataMark. The firmware hardcodes the cue to 0 and its domain is
  -1/0/+1, so this is unreachable, and AOA and the setpoints are unaffected
  because they come before it.
- **A NaN AOA arrives as `+000`.** The firmware's serial path does not check
  for NaN, and the Teensy converts NaN to 0. Below `min_ias` that is flagged
  `bad` like the low-speed zero; above it, it is indistinguishable from a real
  0.0 deg and is published good. The plugin does not treat zero as bad above
  `min_ias`: a real 0.0 deg AOA at speed is legitimate. The fix belongs in
  OnSpeed.
- OAT, G onset rate and the spin cue are hardcoded zero in this firmware.
  They are decoded and never published.

## Status

`Port`, `Port Open`, the counters (`Lines Received`, `Frames Decoded`,
`Length Errors`, `Checksum Errors`, `Grammar Errors`, `Wrong Format`,
`Encoding Errors`, `Overlong Lines`, `DataMark Overflow Frames`,
`Setpoint Order Errors`, `Low-IAS Frames`), `Last Frame Age (s)`, and
`Last Frame`: every decoded field in engineering units. On the bench, compare
`Last Frame` with the OnSpeed display field by field.

The first line after the port opens is always discarded (it is usually
partial), and so is any line over 128 bytes.

## Simulator

`tools/onspeed_sim.py` writes ONSPEED sentences at 10 Hz to a pseudo-terminal
(it prints the path) or, with `--port`, to a serial port. The profile is a
few seconds of taxi under 25 kt, then for each of three flap settings an AOA
sweep from 0 past that setting's stall-warning AOA and back. The setpoints
are made up; they are in OnSpeed's order but are no aircraft's calibration.

```
PYTHONPATH=src python3 tools/onspeed_sim.py
# point connections/onspeed.yaml port: at the printed /dev/pts/N
```
