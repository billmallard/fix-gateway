# X-Plane

The `xplane` plugin feeds the database from X-Plane 11 or 12 over UDP, so a
panel can be flown on the bench before it is in an airplane. It reads X-Plane's
Data Output rows and named datarefs, and can write datarefs back.

Plugin reference:
[`doc/plugins/xplane.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/xplane.rst).
It is off in the shipped config: set `XPLANE: true` and turn `DEMO` off, since
both write attitude (see [[Configuration]]). The plugin refuses to start while
`demo` is loaded.

## X-Plane settings

- **Data Output:** send the rows below by UDP to the gateway machine's address,
  port `udp_in` (49001).
- **Network:** the gateway sends to `udp_out` (49000), X-Plane's legacy receive
  port. The newer port (49010) does not accept dataref requests.
- The host firewall on the X-Plane machine must allow inbound UDP on 49000, or
  the dataref requests are dropped silently while Data Output still arrives.

## Settings

| Option | Default | Meaning |
|---|---|---|
| `ipaddress` | `127.0.0.1` | X-Plane's address, where requests and writes are sent |
| `udp_in` | `49001` | local port the gateway listens on |
| `udp_out` | `49000` | X-Plane's port |
| `recv` | `{}` | Data Output rows to keys |
| `datarefs` | `{}` | named datarefs to keys |
| `dataref_hz` | `10` | dataref update rate requested |
| `rref_resubscribe` | `5.0` s | how often the dataref requests are resent, so they survive an X-Plane restart |
| `dataref_writes` | `{}` | keys written back to datarefs |
| `send`, `send_interval` | `{}`, `0.1` s | keys sent as Data rows (commented out in the shipped file) |
| `autopilot` | off (`enabled: true` in the shipped file) | X-Plane as the autopilot source, below |

## Data Output rows (`recv`)

Each row index maps its eight values to keys; `_` skips a value. The shipped
file, laid out for X-Plane 12:

```yaml
recv:
  3:  [IAS, _, TAS, GS, _, _, _, _]
  17: [PITCH, ROLL, _, _, HEAD, MAGVAR, _, _]
  20: [LAT, LONG, ALT, AGL, _, _, _, _]
```

It also maps rows 4 (`VS`, `ALAT`), 16 (`ROT`) and 18 (`AOA`, `TRACK`, `VPATH`).
Values of -999 (X-Plane's "no data") are dropped.

## Named datarefs (`datarefs`)

`KEY: dataref`, `KEY: [dataref, scale]`, or `KEY: [dataref, {code: value}]` for
a dataref whose codes differ from the key's. With a map, the rounded dataref
value is looked up, and a code not in the map writes 0:

```yaml
datarefs:
  EXTCRS:  sim/cockpit/radios/gps_course_degtm
  EXTCDI:  [sim/cockpit/radios/gps_hdef_dot, 0.4]
  NAV1TYPE: ["sim/cockpit/radios/nav_type[0]", {3: 1, 4: 2, 5: 2}]
```

The shipped file subscribes the X-Plane GPS as the external navigator
(`EXTCRS`, `EXTCDI`, `EXTTF`, and its glidepath `EXTGSI`/`EXTGSF`; select it
with `GPSSRC` = 1, see
[[Flight Plan Engine]]), its OBS state and course (`EXTSEQ`: 0 normal, 1 OBS,
2 suspended; `EXTOBSCRS`; X-Plane 12.1 or later, read only, for comparing with
the engine), both NAV radios' deviation, glideslope,
TO/FROM and tuned navaid type (`NAV1TYPE`/`NAV2TYPE`: 1 VOR, 2 localizer, 0
anything else), and the VOR bearings. The OBS courses (`NAV1CRS`, `NAV2CRS`)
and `HEADBUG` are not read here: the autopilot block keeps them in step with
the sim. With that block off, add them back here as plain reads, one way only.

## Writing back (`dataref_writes`)

```yaml
dataref_writes:
  XPHSISRC: sim/cockpit2/radios/actuators/HSI_source_select_pilot
```

A write is sent only when the key changes. In the shipped entry `XPHSISRC` is
`NAVSRC` (through `compute`), so X-Plane's pilot HSI source follows the source
selected on the panel, and the sim's own autopilot tracks it.

## Autopilot (`autopilot`)

```yaml
autopilot:
  enabled: true
```

With this block on, X-Plane is the autopilot source. A control head writes a
request to `APCMD` and then changes `APCMDSEQ`; the plugin presses the matching
X-Plane autopilot command. It writes what X-Plane reports into the autopilot
keys: `APENG`, `FDENG`, `YDENG`, the lateral and vertical modes (`APLAT`,
`APLATARM`, `APVERT`, `APVERTARM`), `APALTCAP`, `APALTREF`, the flight director
(`FDPITCH`, `FDROLL`), the requests this aircraft's autopilot supports
(`APCAPS`), `APMSG` and `APDISC`. A request is never replayed: only a change of
`APCMDSEQ` after start counts.

- The mode keys change only when X-Plane reports the new mode. A request it
  cannot honour sends nothing and gives the reason on `APMSG`.
- State is written only while every dataref the adapter subscribes has been
  heard within 1 s. When X-Plane goes quiet, nothing is written and the keys go `old`.
- `ALTSEL`, `VSSEL`, `IASSEL`, `HEADBUG`, `NAV1CRS`, `NAV2CRS` and `BARO` are
  kept in step with the sim's pilot-side dials, both ways. A change on the bus
  is written to the dial; a dial that has not followed within 2 s keeps the
  sim's value.
- `HEADBUG` is moved with X-Plane's own heading knob commands, one degree at a
  time the short way round, never written directly. The walk stops within
  half a degree, and the bus keeps the pilot's value.
- The adapter subscribes its own datarefs. A row for one of the keys above
  under `datarefs:` or `dataref_writes:` is ignored, with a warning.
- It stays off, and logs why, when the database has no autopilot keys
  (`database/autopilot.yaml`).
- `raw_log: <path>` appends every change of its datarefs to a file, as JSON
  lines.

The key contract is in
[`doc/autopilot_keys.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/autopilot_keys.md).

## Staleness

The plugin has no timeout of its own. When X-Plane stops sending, keys go `old`
through their `tol` (`PITCH` 300 ms, `IAS` 2 s, `EXTCRS` 5 s), and the panel
flags them.
