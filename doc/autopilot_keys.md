# Autopilot and flight director FIX keys (AFC1)

Spec: `makerplane/briefs/autopilot_control_head_plan.md` section 5.2 (the
maos-workspace repo), with Bill's rulings D1-D7 of 2026-10-26; backlog item
AFC1 (AER-5569). The keys are defined in `database/autopilot.yaml`, which the
default `database.yaml` includes.

Two sources write these keys. The X-Plane adapter (AFC2, "The X-Plane
source (AFC2)" below) is the bench source. The MAVLink plugin writes them for
an ArduPlane flight controller (AFC7, "The MAVLink (ArduPlane) source" at the
end of this file). The `autopilot_panel` instrument (AFC3) is the first
request writer, and the PFD annunciation and flight director bars (AFC4, AFC5)
the first readers. Turn on one autopilot source at a time: the state keys
have one writer.

## The rule: the head asks, the autopilot decides

The control head never decides which mode is active. A button press writes a
**request**. The autopilot source (a simulator, an adapter for a real
autopilot, or a future guidance computer) writes the **state** it actually
entered. Buttons and annunciators always show the reported state, never "I was
pressed". Real autopilots refuse modes all the time: NAV with no valid nav
source, AP with an attitude failure, APR with no approach loaded. An
annunciator that showed the press would tell the pilot a mode is flying when
it is not.

So there are two kinds of key, and their writers never overlap:

- **Targets and requests** are the pilot's. Any pilot interface may write
  them: the control head, the PFD boxes, a hardware knob.
- **State, flight director and health** are the autopilot source's. Exactly
  one source writes them. No display, and no other plugin, ever writes one.

The MAVLink plugin's request/state split (`MAVREQ*` written by buttons,
`MAVMODE`/`MAVSTATE` reported by the autopilot) is the same pattern. This
contract generalises it to GA modes.

## Targets

| Key | Type | Units | Range | Initial | tol (ms) | Writer | Meaning |
|---|---|---|---|---|---|---|---|
| `VSSEL` | int | ft/min | -30000 to 30000 | 0 | 0 | pilot | Vertical speed target for VS mode. Mirrors CAN-FIX 459 Selected Vertical Speed |
| `IASSEL` | float | knots | 0.0 to 999.9 | 0.0 | 0 | pilot | Airspeed target for IAS mode. 0 means not set. Mirrors CAN-FIX 460 Selected Airspeed |

The other two targets already exist, and this contract uses them as they are:

- `HEADBUG` (`database/ahrs.yaml`): the heading target for HDG mode.
- `ALTSEL` (`database/gcu.yaml`; it moved there from `custom.yaml` in
  fix-gateway#42): the selected altitude, which ALT capture levels at.

All four are pilot settings, written on change, so their tol is 0. A tol would
flag a good setting old seconds after every edit
(`docs/primary_flight_key_tol.md`). VS and IAS each have their own target and
both are always editable (D2), so the pilot can set one before engaging its
mode. Engaging IAS with `IASSEL` still 0 is the source's call: it may refuse
the request, saying why in `APMSG`, or sync to the current airspeed.

Several plugins may write a target, as with `HEADBUG` today (netfix for
pyEfis, the X-Plane connection from the sim's panel). The one-writer check
(AER-4476) logs a warning the first time a second plugin writes one. That is
expected for these four keys. It is also expected for the three pilot settings
the X-Plane source keeps in step the same way (AFC2b, "Targets" under "The
X-Plane source (AFC2)" below): `NAV1CRS`, `NAV2CRS` and `BARO`. It is expected
for these seven keys, and only for these.

## Requests

| Key | Type | Units | Range | Initial | tol (ms) | Writer | Meaning |
|---|---|---|---|---|---|---|---|
| `APCMD` | str | | | "" | 0 | pilot | The request: one of `AP`, `FD`, `YD`, `LVL`, `HDG`, `TRK`, `NAV`, `APR`, `IAS`, `VNAV`, `VS`, `ALT`, `DISC` |
| `APCMDSEQ` | int | | 0 to 2147483647 | 0 | 0 | pilot | Request counter, bumped after every `APCMD` write |

One command key plus a sequence counter (D3). The writer writes `APCMD`, then
writes `APCMDSEQ` as its current value plus one. The source acts only when
`APCMDSEQ` changes, and then reads `APCMD`. This is the "write, then bump the
counter" idea `FPLSEQ` uses, and it is why two presses of the same button are
two commands. netfix processes one connection's writes in order, so the
command is in place when the counter lands.

- **A press toggles.** The source treats `HDG` while in HDG as "cancel HDG",
  `AP` while engaged as a disconnect, and so on. The request names the button,
  not the outcome. That is what keeps the outcome the source's.
- **`DISC`** disconnects the autopilot. It is the on-screen convenience in the
  brief's section 5.7 (item 5), never the direct disconnect, which must not
  pass through fix-gateway at all. `DISC` while `APDISC` is set acknowledges
  the alert.
- **When the source starts or reconnects**, it takes the current `APCMDSEQ` as
  its baseline and does not act on it. A restarted adapter must never replay
  the last request: that request may be an `AP` from an hour ago. The source
  acts on any later change, not only an increase, so a counter that went back
  to 0 when the gateway restarted is still a new request.
- **An unknown or unsupported request** is refused, never silently dropped:
  the state does not change, and the source says why in `APMSG`.
- There is no acknowledgement key. The answer to a request is the state keys.
  A head that sees no state change about 2 s after a press shows that the
  request was not taken (brief section 5.4).

## State

| Key | Type | Units | Range | Initial | tol (ms) | Writer | Meaning |
|---|---|---|---|---|---|---|---|
| `APENG` | bool | | | false | 2000 | autopilot source | Autopilot engaged (servos coupled) |
| `FDENG` | bool | | | false | 2000 | autopilot source | Flight director on |
| `YDENG` | bool | | | false | 2000 | autopilot source | Yaw damper engaged |
| `APLAT` | str | | | "" | 2000 | autopilot source | Active lateral mode: `ROL`, `HDG`, `TRK`, `GPS`, `VOR`, `LOC`, `BC`, `LVL`, or "" for none |
| `APLATARM` | str | | | "" | 2000 | autopilot source | Armed lateral modes, comma-separated, from the `APLAT` names |
| `APVERT` | str | | | "" | 2000 | autopilot source | Active vertical mode: `PIT`, `ALT`, `VS`, `IAS`, `VNAV`, `GS`, `GP`, `LVL`, or "" for none |
| `APVERTARM` | str | | | "" | 2000 | autopilot source | Armed vertical modes, comma-separated, from the `APVERT` names |
| `APALTCAP` | bool | | | false | 2000 | autopilot source | Capturing `ALTSEL`: the transition between a climb or descent and ALT hold |
| `APALTREF` | int | ft | -1000 to 60000 | 0 | 2000 | autopilot source | The altitude ALT hold is holding. Not `ALTSEL`: the pilot may preselect the next altitude while holding this one |

- The state names are the modes the source is flying, not the buttons. `NAV`
  is a request, and the state it produces is `GPS`, `VOR` or `LOC`, depending
  on the navigation source. `APR` typically produces `LOC` or `GPS` lateral
  with `GS` or `GP` armed vertical.
- Armed lists are comma-separated with no spaces, for example `GS,ALT`, the
  idiom `FPLfALT` already uses for its constraints. A display shows them in
  the order given. "" means nothing is armed.
- `APLAT` and `APVERT` are "" when neither the autopilot nor the flight
  director is on.
- `APALTREF` means something only while `APVERT` is `ALT`; a display shows it
  then and ignores it otherwise. A source with no held-altitude value never
  writes it, so it stays old and a display shows `ALT` with no number. It is
  never filled in from `ALTSEL` (Bill, AER-5808, 2026-11-16: showing the
  preselect as the held altitude is the shortcoming this key fixes).

## Flight director

| Key | Type | Units | Range | Initial | tol (ms) | Writer | Meaning |
|---|---|---|---|---|---|---|---|
| `FDPITCH` | float | deg | -90.0 to 90.0 | 0.0 | 1000 | autopilot source | Commanded pitch attitude |
| `FDROLL` | float | deg | -180.0 to 180.0 | 0.0 | 1000 | autopilot source | Commanded roll attitude |

The commanded attitude is absolute, not an error, and has the same sense and
range as `PITCH` and `ROLL`: nose up and right wing down are positive. The
display draws the bars from the difference between these and the aircraft's
own attitude. It draws them only while `FDENG` is true and both keys are
fresh (brief section 5.5).

## Health

| Key | Type | Units | Range | Initial | tol (ms) | Writer | Meaning |
|---|---|---|---|---|---|---|---|
| `APCAPS` | str | | | "" | 2000 | autopilot source | The requests this source supports, comma-separated `APCMD` names, for example `AP,FD,HDG,NAV,ALT,VS,DISC` |
| `APMSG` | str | | | "" | 2000 | autopilot source | A message for the pilot, such as why a request was refused, or "" for none. 64 characters at most |
| `APDISC` | bool | | | false | 2000 | autopilot source | Disconnect alert. Set on every autopilot disconnect, whatever caused it, and held until the pilot acknowledges it with `APCMD` `DISC` |

- `APCAPS` is how the control head greys out a button whose mode this
  autopilot cannot fly. The button is drawn disabled, never hidden (brief
  section 5.7, "partial is annunciated as partial").
- `APDISC` is latched by the source, not the display, so every display shows
  the same alert and an acknowledgement on one clears it on all of them.

## Stale is removed, not frozen

Every state, flight director and health key has a nonzero tol, and the source
rewrites each of them at least once per tol even when nothing has changed. A
key that is not rewritten in time goes old, and so does a key the source has
never written. A display treats an old key as "no autopilot data": it removes
the FD bars, blanks the annunciation and draws the buttons as unavailable. It
never keeps showing the last value (brief section 5.7). A frozen FD bar is a
hazard, not a cosmetic bug.

The FD keys go old after 1 s and the rest after 2 s. The X-Plane connection
streams datarefs several times a second, so either margin covers many missed
updates. `tests/test_autopilot_keys.py` fails if any of these keys loses its
tol.

Targets and requests keep tol 0. They are written when the pilot acts, so a tol
would flag them old between edits.

## The X-Plane source (AFC2)

`fixgw.plugins.xplane.autopilot`, turned on by `autopilot: enabled: true` in
`connections/xplane.yaml` (on in the shipped file; the XPLANE plugin itself is
off by default). It subscribes its own datarefs over RREF, so nothing has to
be added under `datarefs:`. Every dataref and command name is in X-Plane 12's
own `DataRefs.txt` and `Commands.txt`. If the running sim does not answer one
of them, the adapter publishes nothing and logs the silent names once, after
10 s.

**Requests.** Each request presses one X-Plane command, and that is all it
does. The state keys change only when X-Plane reports that its mode changed.

| Request | X-Plane command | Refused (nothing sent, reason on `APMSG`) when |
|---|---|---|
| `AP` | `sim/autopilot/servos_on`, or `servos_off_any` when engaged | |
| `FD` | `sim/autopilot/fdir_on`, or `servos_fdir_off` when on | the autopilot is engaged and FD is on |
| `YD` | `sim/systems/yaw_damper_toggle` | |
| `LVL` | `sim/autopilot/return_to_level` | |
| `HDG` | `sim/autopilot/heading` | |
| `TRK` | `sim/autopilot/track` | |
| `NAV` | `sim/autopilot/NAV` | the HSI source has no lateral signal, unless NAV is already armed or active (that press cancels) |
| `APR` | `sim/autopilot/approach` | the same, for approach |
| `IAS` | `sim/autopilot/level_change`, then `IASSEL` to the speed dial 0.5 s later, unless `IASSEL` is 0 | |
| `VNAV` | `sim/autopilot/vnav` | |
| `VS` | `sim/autopilot/alt_vs` (VS at the current rate, ALT armed), then `VSSEL` to the VS dial 0.5 s later | |
| `ALT` | `sim/autopilot/altitude_hold` | |
| `DISC` | `sim/autopilot/servos_off_any` when engaged; when `APDISC` is set, it only acknowledges | |

Every request is also refused when it is not in `APCAPS`, or when the
adapter has had no complete, fresh set of datarefs for 1 s.

**State.** Read from `sim/cockpit2/autopilot/`, where each mode's `*_status`
is 0 off, 1 armed, 2 captured:

| Key | From |
|---|---|
| `APENG` | `servos_on` |
| `FDENG` | `flight_director_mode` 1 or 2 |
| `YDENG` | `sim/cockpit2/switches/yaw_damper_on` |
| `APLAT` | the first captured of `backcourse_status` (BC), `nav_status`, `gpss_status` (GPS), `heading_status` or `heading_hold_status` (HDG), `track_status` (TRK), `roll_status`, `rate_status` or `attitude_status` (ROL) |
| `APLATARM` | `backcourse_status`, `nav_status` and `gpss_status` that are armed |
| `APVERT` | the first captured of `glideslope_status` (GS, or GP when the HSI is on GPS), `vnav_status`, `altitude_hold_status` (ALT), `speed_status` (IAS), `vvi_status` (VS), `pitch_status` or `attitude_status` (PIT) |
| `APVERTARM` | `altitude_hold_status`, `vnav_status` and `glideslope_status` that are armed, in that order |
| `APALTREF` | `sim/cockpit2/autopilot/altitude_hold_ft` ("Altitude hold commanded in feet indicated"), rounded to the foot |
| `FDPITCH`, `FDROLL` | `flight_director_pitch_deg`, `flight_director_roll_deg` |
| `APCAPS` | the aircraft's `sim/aircraft/autopilot/preconfigured_ap_type`, less ALT, VS, IAS and VNAV for a single-axis autopilot, plus YD when it has a yaw damper switch |
| `APDISC` | latched by the adapter whenever `servos_on` goes from 1 to 0 |

NAV's state is named by what the HSI follows
(`HSI_source_select_pilot`): GPS on GPS1 or GPS2; on NAV1 or NAV2, LOC when
that radio's `nav_type` is a localizer (4 or 5) and VOR otherwise.

What X-Plane does not report, and the adapter therefore never claims:

- **No LVL state.** X-Plane's "return to level" flies its roll and pitch hold
  modes, so after `LVL` the keys show the modes it reports, not `LVL`.
- **No capture phase.** `altitude_hold_status` goes from armed straight to
  captured, so `APALTCAP` is always false from this source.

**Targets.** Each of these keys follows its sim dial, and a pilot change on
the bus is written to the dial (DREF):

| Key | X-Plane dataref (all writable in `DataRefs.txt` 1210) |
|---|---|
| `ALTSEL` | `sim/cockpit2/autopilot/altitude_dial_ft` |
| `VSSEL` | `sim/cockpit2/autopilot/vvi_dial_fpm` |
| `IASSEL` | `sim/cockpit2/autopilot/airspeed_dial_kts` |
| `HEADBUG` | `sim/cockpit2/autopilot/heading_dial_deg_mag_pilot` |
| `NAV1CRS` | `sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot` |
| `NAV2CRS` | `sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot` |
| `BARO` | `sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot` |

Until the dial follows, for up to 2 s, the dial is not copied back, so the
pilot's value does not flicker, and a reply already in flight cannot put the
old value back. A pilot change that lands at the same moment as a dial
movement wins: it is sent before the dial is read. A value the sim will not
take settles to the sim's. The heading bug and the courses are compared
around the circle, so 359 to 0 is a 1 degree change, and a dial at 360 reads
0. `BARO` is the pilot-side altimeter, so X-Plane's altitude capture and the
glass use one setting. The GPS course is not a target: it is the active leg
(`GPSCRS`), which the pilot does not set.

The shipped `xplane.yaml` reads none of these keys under `datarefs:`. A row
for one of them under `datarefs:` or `dataref_writes:` (an older, seeded
copy of the file) is ignored with a warning while the adapter is on, so each
key has one path. With the adapter off, the commented rows in `xplane.yaml`
put `HEADBUG`, `NAV1CRS` and `NAV2CRS` back as plain reads, sim to bus only.

**Staleness.** State is written only from fresh replies: every one of the
adapter's datarefs heard within 1 s. An unchanged key is rewritten every
0.5 s. When X-Plane stops sending, the adapter writes nothing, and the keys go
old by their own tol.

## Divergence from upstream

This fork leads the autopilot contract (ORG-011). Upstream
`makerplane/FIX-Gateway` has none of these keys: no `database/autopilot.yaml`,
no `VSSEL`/`IASSEL`, no `AP*`/`FD*`/`YDENG` keys. Its only autopilot keys are
the MAVLink ones in `database/mavlink.yaml` (`MAVREQ*`, `MAVMODE`, `MAVSTATE`,
`MAVMSG`), which this fork keeps unchanged. The MAVLink adapter (AFC7,
below) maps them into this contract rather than replacing them.

On the CAN-FIX side:

- `VSSEL` and `IASSEL` mirror the existing CAN-FIX parameters 459 and 460, but
  `canfix/map.yaml` does not map them yet. `ALTSEL` is mapped, on 461.
- CAN-FIX has nothing for the rest of this contract. Parameter 268 (autopilot
  commands) has only engage, disconnect, VS, altitude hold, altitude select and
  heading step. Parameter 1240 (autopilot/FD mode) has an index but no value
  definitions. There is no selected heading, no FD command, and no armed or
  engaged status. The proposal to add them comes after the bench proves these
  keys (D7, backlog AFC6), drafted in the `billmallard/canfix-spec` fork.
  Proposing it upstream needs Bill's authorization.

Nothing here is relicensed or closed. It is public in the fork under the
existing licence and documented in this file.

## The MAVLink (ArduPlane) source (AFC7)

`fixgw.plugins.mavlink.autopilot`, run by the MAVLink plugin's thread. It is
on whenever the plugin is (the MAVLINK plugin is off by default), and
`autopilot: false` under `options:` in `connections/mavlink.yaml` turns it
off. If the database has no `autopilot.yaml`, it logs that once and stays off.

ArduPlane's modes are drone modes, not GA modes (brief section 5.6a), so this
source offers little, and says so: `APCAPS` is `AP,NAV,DISC`, and the head
greys every other button.

**Requests.** A request writes the existing `MAVREQ*` key, the same key the
`buttons/auto-pilot-*.yaml` buttons write, and `Mav.checkMode` sends the mode
change. The state keys change only when ArduPlane's heartbeat reports the new
mode. Only the quorum leader acts on a request.

| Request | `MAVREQ*` written | Refused (nothing sent, reason on `APMSG`) when |
|---|---|---|
| `AP` | `MAVREQCRUISE`, or `MAVREQTRIM` when engaged | the plugin's status is not `ARMED` (it arms by itself once the pre-arm checks pass) |
| `NAV` | `MAVREQGUIDED`, or `MAVREQCRUISE` when in GUIDED | the autopilot is not engaged; there is no valid waypoint (`MAVWPVALID`) |
| `DISC` | `MAVREQTRIM` when engaged; when `APDISC` is set, it only acknowledges | the plugin's status is not `ARMED` |
| any other | nothing | always: "HDG is not available on this autopilot" |

Every request is also refused when no fixed-wing heartbeat has arrived for
1.5 s. `NAV` with the autopilot off is refused rather than engaging it: there
is no flight director to arm, so NAV would couple the servos by itself.

**State.** From the custom mode in each fixed-wing (`MAV_TYPE_FIXED_WING`)
heartbeat:

| ArduPlane mode | `APENG` | `APLAT` | `APVERT` | What it flies |
|---|---|---|---|---|
| MANUAL (`MAVMODE` `TRIM`) | false | "" | "" | the pilot's trims drive the servos |
| CRUISE | true | `TRK` | `ALT` | holds the ground track and altitude it had when engaged; not `HEADBUG` |
| GUIDED | true | `GPS` | `ALT` | direct to `WPLAT`/`WPLON` at the altitude it had when commanded, then circles it; not along the leg |
| AUTOTUNE | true | `ROL` | `PIT` | holds the attitude the sticks give it, while it tunes |
| any other (FBWA, TAKEOFF, RTL, AUTO, ...) | true | "" | "" | named on `APMSG`, for example "ArduPlane mode FBWA" |
| INITIALISING | false | "" | "" | |

- `APENG` follows the mode, not arming. A disarmed ArduPlane still drives its
  control surfaces in every mode and holds only the throttle (measured in
  ArduPlane 4.7.2 SITL: full right stick gave SERVO1 1900 us disarmed in
  MANUAL, FBWA and CRUISE).
- `FDENG`, `YDENG` and `APALTCAP` are always false and the armed lists always
  "": ArduPlane has no flight director and no separate yaw damper switch, and
  reports no armed modes and no capture phase. `FDPITCH` and `FDROLL` are
  never written, so they stay old and no display draws bars.
- `APALTREF` is not written yet, so it stays old and a display shows `ALT`
  with no number.
- `APDISC` is latched on any change from an engaged mode to MANUAL, whatever
  caused it: the head, the transmitter's mode switch or a failsafe.
- The mode keys `MAVMODE`, `MAVSTATE` and `MAVMSG`, and the buttons that read
  them, are unchanged.

**Staleness.** Every state key is rewritten on each heartbeat (1 Hz) and
every 0.5 s while the last heartbeat is at most 1.5 s old, so one missed
heartbeat does not flicker the keys. When the link stops, nothing is written,
and the keys go old by their own 2 s tol: at most 3.5 s after the last
heartbeat. `MAVMSG` still goes old 3 s after it (CERT2d, AER-5320).
