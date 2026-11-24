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

The callbacks run inside the writing plugin's own write, so they only compare
flags and queue the change. Everything else happens on the cas thread. A slow
or failing evaluation does not slow the writer, and the tests show it. What
they do not cover is an evaluation that burns CPU: Python's interpreter lock
is shared by every plugin in the process. A 500 ms busy loop cut HEAD's write
rate to 62% in Safety and Compliance's probe (AER-6093). The real evaluation
is a few comparisons over about a dozen entries.

### Acknowledge

Writing `CASACK = n` acknowledges every message first published at sequence n
or earlier, on every display. A message that arrived after the display drew n
stays unacknowledged. Acknowledge stops the flash and nothing else: the line
stays while its condition holds.

Four rules keep a stale acknowledge from covering a new alert:

- `CASSEQ` starts above any `CASSEQ` or `CASACK` already on the bus. That
  covers a plugin restart under a running gateway.
- `CASSEQ` also starts above the seconds since 2026-01-01 UTC. After a full
  gateway restart the bus starts at 0 again, but a display still holds its
  `CASACK` from the last run. With a correct clock, that value is below
  anything the new run publishes.
- A `CASACK` above the current `CASSEQ` acknowledges nothing, and `CASSEQ`
  moves above it at once. That covers a box whose clock defeats the epoch,
  such as a Pi booted without NTP. Ignoring the value is not enough on its
  own: once `CASSEQ` had climbed past it, the same value re-sent on the next
  reconnect would cover messages nobody drew. After the jump it can cover
  only messages published before the display reconnected.
- `CASACKBTN` edges closer together than `ack_debounce` (0.5 s) are one press.

A `CASACK` more than 10^7 above both `CASSEQ` and the epoch clock is refused
and logged. No real sequence is that far ahead, and jumping to such a value
would pin `CASSEQ` at its maximum, where no acknowledge could reach a new
message.

**The panel button is optional, and it has a gap that touch does not.** A
button press acknowledges the `CASSEQ` the plugin has already published. It
cannot know what the pilot has seen. A message published in the time between
the plugin writing it and the pilot seeing it is covered by any press in that
window. The window is the display's redraw plus the pilot's reaction, on the
order of a second. Alerts often arrive together: a heading loss can take the
autopilot link with it, so a second alert tends to land while the pilot is
reaching for the button to clear the first. A message acknowledged that way is
drawn steady, never flashes, and does not count in `CASTOP`. Its line still
shows. Touch writes the `CASSEQ` the display actually drew, so it has no such
window. Fit the button only if that trade is acceptable (brief section 6.5).

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
| `CASTEST` | `CAS TEST` | advisory | From start until acknowledged (a touch on the window, or the panel button), or `test_backstop` seconds (600) after start. Always on |
| `APLINK` | `AP LINK LOST` | warning if `APENG` was last true, else caution | `APENG` old, bad or failed, after it has been valid in this run. Clears 5 s after the link is steady |
| `HDGFAIL` | `HDG FAIL` | caution | `HEAD` old, bad or failed. At once once it has been valid; a heading not yet valid 30 s after start also raises it |
| `NAVFAIL` | `NAV FAIL` | caution | Lateral guidance lost on a nav source that had it earlier in this run |
| `FUELQ<n>ND` | `FUEL QTY <n> NO DATA` | caution | `FUELQ<n>` not written 30 s after start, for tanks listed in `fuel_no_data_tanks` only |

`CAS TEST` proves the plugin, the bus, the display and the glass on every
start, the way a lamp test does. A window that draws nothing when quiet cannot
otherwise be told from a missing one.

The pilot taps it once at power-up. It is held until acknowledged because a
display draws later than the gateway starts: on the CAS3 bench pyEfis first
drew about 6 s after it connected, and 11 s after a gateway restart under it,
so the old fixed 5 s never reached the glass (AER-6346, AER-6497). The
backstop clears it on a panel whose display cannot acknowledge. The old
`test_time` option is ignored with a warning, since installs carry a copied
`cas.yaml` with `test_time: 5`.

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
| `never_written: KEY` | KEY has had no write `grace` seconds (default 30) after start. Its own expiry is not a write, and nor is a value from an ini file |
| `nav_lost: true` | `NAVFAIL` is true and the selected `NAVSRC` had valid guidance earlier in this run |

"Valid" and "written" mean a reading from a source: a write a plugin made, or
any write after cas started. The server's ini files write before any plugin
starts, credited to none, so a site `custom.ini` that seeds `HEAD = 20` does
not count as a heading the pilot had.

Other fields: `on_delay` (seconds the condition must hold before the line
shows; on a warning it needs an `on_delay_reason`, because it is latency on a
real alert) and `latch` (the line stays until acknowledged, for events that
clear themselves at once).

An entry that breaks a rule is refused at load and the log names it:
a `;` or `|` in its text (netfix splits on `;`, a slot on `|`), text too long
or not upper case, an unknown field, an unknown key, a duplicate id. The rest
of the catalogue still loads. Refused ids show in the plugin status.

## Turning it on

On by default: `CAS: true` and `CAS_CONFIG` in the shipped `preferences.yaml`.
An update replaces that file only while it is unedited. An install that has
edited `preferences.yaml` itself (the Beelink bench sets `XPLANE` and `DEMO`
there) keeps its own copy. The gateway still starts and the CAS keys arrive,
but the plugin stays off and the keys read old. Turn it on in
`preferences.yaml.custom`:

```yaml
includes:
  CAS_CONFIG: connections/cas.yaml
enabled:
  CAS: true
```

If `database.yaml` has been edited and lacks
`- include: database/cas.yaml`, the plugin refuses to start and the log names
the missing keys.

## Not in this slice

Sound, recall and clear, phase-of-flight re-levelling, crew-set inhibits,
and engine limits. See the brief, section 12.1.
