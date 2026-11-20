# CAS plugin (crew alerting)

`fixgw.plugins.cas` owns the crew alerting message list. A catalogue in
`connections/cas.yaml` names each alert. The plugin watches the keys those
alerts name, decides which are active, and publishes the sorted list on the
bus. Displays only draw it. The design is the CAS1 brief, maos-workspace
`makerplane/briefs/crew_alerting.md`; this is its first slice (CAS2, CAP-119).

CAS is supplementary. Every instrument flag, red X and gauge colour stays as
it is. CAS adds a line in a window; it never replaces a cue.

## Keys

All in `database/cas.yaml`. The plugin is the one writer of every key except
`CASACK` and `CASACKBTN`.

| Key | Type | Meaning |
|---|---|---|
| `CASMSG1`..`CASMSG12` | str | Slot n of the sorted list, `LEVEL\|ACK\|ID\|TEXT\|DETAIL`, `""` when unused |
| `CASCNT` | int | Active messages, including any past slot 12 |
| `CASTOP` | int | Highest unacknowledged level: 0 none, 1 advisory, 2 caution, 3 warning |
| `CASSEQ` | int | Bumped on every change to the list, written after the slots |
| `CASACK` | int | A display writes the `CASSEQ` it is showing to acknowledge |
| `CASACKBTN` | bool | A panel button; a rising edge acknowledges the current `CASSEQ` |
| `NAVFAIL` | bool | The selected source's lateral guidance is lost: the HSI's NAV flag |

A slot reads, for example, `3|0|APLINK|AP LINK LOST|`: level 3 (warning), not
acknowledged, id, text, and an empty detail. The list is sorted warnings,
cautions, advisories; within a level unacknowledged first, then newest first.

The plugin rewrites every key once a second, and each has `tol: 3000`. A
stopped or broken plugin therefore turns them old within 3 s, and a display
can say `CAS FAIL` rather than draw an old list as current. If the list cannot
be computed (an exception in the evaluation), the keys are not rewritten.

### Acknowledge

Writing `CASACK = n` acknowledges every message first published at sequence n
or earlier, on every display. A message that arrived after the display drew n
stays unacknowledged. Acknowledge stops the flash and nothing else: the line
stays while its condition holds.

Three rules keep a stale acknowledge from covering a new alert:

- `CASSEQ` starts above any `CASSEQ` or `CASACK` already on the bus, so a
  plugin restart under a running gateway cannot fall back below an old
  acknowledge.
- A `CASACK` above the current `CASSEQ` is ignored and logged. That is a
  display re-writing its last value after a gateway restart.
- `CASACKBTN` edges closer together than `ack_debounce` (0.5 s) are one press.

A button cannot know what the pilot saw, so a message that arrives in the same
instant as a press can be acknowledged unseen. Touch does not have that gap.

### NAVFAIL

`NAVFAIL` is the HSI's NAV flag rule, moved to the gateway so the HSI and CAS
read one rule: the selected source's `CDI` old, bad or failed, `CDIV` below
0.5, or `COURSE` bad or failed. That is the HSI's rule as pyEfis has it
today (`_nav_flag_shown`), so the HSI's flag does not change when it starts
reading the key. A display falls back to
its own rule when `NAVFAIL` is absent or old. The key flags an untuned VOR, as
the HSI does; the `NAV FAIL` alert does not (below).

## The shipped catalogue

| Id | Text | Level | When |
|---|---|---|---|
| `CASTEST` | `CAS TEST` | advisory | The first `test_time` seconds (5) after start. Always on |
| `APLINK` | `AP LINK LOST` | warning if `APENG` was last true, else caution | `APENG` old, bad or failed, after it has been valid in this run. Clears 5 s after the link is steady |
| `HDGFAIL` | `HDG FAIL` | caution | `HEAD` old, bad or failed. At once once it has been valid; a heading not yet valid 30 s after start also raises it |
| `NAVFAIL` | `NAV FAIL` | caution | Lateral guidance lost on a nav source that had it earlier in this run |
| `FUELQ<n>ND` | `FUEL QTY <n> NO DATA` | caution | `FUELQ<n>` not written 30 s after start, for tanks listed in `fuel_no_data_tanks` only |

`CAS TEST` proves the plugin, the bus, the display and the glass on every
start, the way a lamp test does. A window that draws nothing when quiet cannot
otherwise be told from a missing one.

`AP LINK LOST` and `NAV FAIL` alert only on losing something the pilot had.
An install without an autopilot never writes `APENG`, and a VOR with nothing
tuned was never received; neither raises a line.

`FUEL QTY <n> NO DATA` is off until the installer lists fitted tanks. The
database defines tanks that many installs do not have.

Engine limits are not in this slice. The shipped `annunciate` list would
raise every CHT and EGT on a cold start (brief section 8.2).

## Catalogue entries

```yaml
  entries:
  - id: APLINK               # 1-12 upper case letters or digits, unique
    level: caution           # warning, caution or advisory
    warning_if: APENG        # optional: warning when APENG was last true
    text: AP LINK LOST       # upper case, at most 20 characters
    detail: HANDS ON         # optional, at most 32 characters
    when: {invalid: APENG, after_valid: true}
    clear_delay: 5           # optional: stay 5 s after the condition clears
```

`when` takes exactly one of:

| Condition | Holds when |
|---|---|
| `invalid: KEY` | KEY is old, bad or failed. With `after_valid: true`, only once KEY has been written valid in this run. With `grace: s`, a KEY not yet valid holds off for s seconds after start, so a source still starting up does not alert at every power-up |
| `annunciate: KEY` | KEY's annunciate flag is set and KEY is valid. A failed value is published as 0, below every low limit, and is not an exceedance |
| `value: KEY op LITERAL` | The comparison holds and KEY is valid, e.g. `APDISC == true`. Operators `< <= == != > >=` |
| `never_written: KEY` | KEY has had no write `grace` seconds (default 30) after start. Its own expiry is not a write |
| `nav_lost: true` | `NAVFAIL` is true and the selected `NAVSRC` had valid guidance earlier in this run |

Other fields: `on_delay` (seconds the condition must hold before the line
shows; on a warning it needs an `on_delay_reason`, because it is latency on a
real alert) and `latch` (the line stays until acknowledged, for events that
clear themselves at once).

An entry that breaks a rule is refused at load and the log names it:
a `;` or `|` in its text (netfix splits on `;`, a slot on `|`), text too long
or not upper case, an unknown field, an unknown key, a duplicate id. The rest
of the catalogue still loads. Refused ids show in the plugin status.

## Turning it on

On by default (`CAS: true` in `preferences.yaml`). An install with its own
`preferences.yaml.custom` or `default.yaml` needs `CAS_CONFIG` in both, as for
any plugin. If `database.yaml` has been edited and lacks
`- include: database/cas.yaml`, the plugin refuses to start and the log names
the missing keys.

## Not in this slice

Sound, recall and clear, phase-of-flight re-levelling, crew-set inhibits,
and engine limits. See the brief, section 12.1.
