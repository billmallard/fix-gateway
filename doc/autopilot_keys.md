# Autopilot and flight director FIX keys (AFC1)

Spec: `makerplane/briefs/autopilot_control_head_plan.md` section 5.2 (the
maos-workspace repo), with Bill's rulings D1-D7 of 2026-10-26; backlog item
AFC1 (AER-5569). The keys are defined in `database/autopilot.yaml`, which the
default `database.yaml` includes.

This is a contract only. Nothing in fix-gateway writes these keys yet. The
X-Plane adapter (AFC2) is the first autopilot source, the `autopilot_panel`
instrument (AFC3) the first request writer, and the PFD annunciation and
flight director bars (AFC4, AFC5) the first readers.

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
expected for these four keys, and only for these.

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

- The state names are the modes the source is flying, not the buttons. `NAV`
  is a request, and the state it produces is `GPS`, `VOR` or `LOC`, depending
  on the navigation source. `APR` typically produces `LOC` or `GPS` lateral
  with `GS` or `GP` armed vertical.
- Armed lists are comma-separated with no spaces, for example `GS,ALT`, the
  idiom `FPLfALT` already uses for its constraints. A display shows them in
  the order given. "" means nothing is armed.
- `APLAT` and `APVERT` are "" when neither the autopilot nor the flight
  director is on.

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

## Divergence from upstream

This fork leads the autopilot contract (ORG-011). Upstream
`makerplane/FIX-Gateway` has none of these keys: no `database/autopilot.yaml`,
no `VSSEL`/`IASSEL`, no `AP*`/`FD*`/`YDENG` keys. Its only autopilot keys are
the MAVLink ones in `database/mavlink.yaml` (`MAVREQ*`, `MAVMODE`, `MAVSTATE`,
`MAVMSG`), which this fork keeps unchanged. A later MAVLink adapter (AFC7)
maps them into this contract rather than replacing them.

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
