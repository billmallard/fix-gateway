# Declaring who writes a FIX key (ARCHG1)

Every box talks to every other box through the FIX database, so a key is a
contract: one meaning, one unit, one datum, and one writer at a time. ARCH0
(AER-6340) found 53 keys with more than one live writer and nothing to stop the
54th. `ALT` alone has six writers, and among them they put at least three
different altitudes on the bus: indicated, MSL and pressure. This check stops that count from rising, and makes it fall as each key is
fixed.

Backlog item ARCHG1 (AER-6350). Policy: ORG-018 (maos-workspace).

## The records

| File | What it holds |
|---|---|
| `src/fixgw/database/writers.yaml` | One line per key that some module writes: its units, the datum of an altitude or position key, its writers by kind, and its selector if it has one. For the source keys, each writer also declares its datum, quality and failure mode (below). It is a design record: the gateway never loads it, and `database/*.yaml` is still the runtime truth for a key. |
| `tests/arch/key_writers_baseline.yaml` | The keys allowed more than one live writer today, each with its writers. It can only shrink. |
| `tests/arch/datum_baseline.yaml` | The keys whose writers declare more than one datum today, each writer with its datum. It can only shrink. |
| `tests/arch/concept_names.yaml` | The files allowed to define a class named `Waypoint`, `Point`, `Position`, `Leg`, `Plan` or `Route`. |

`tools/check_key_writers.py` scans the tree and checks it against all of them.
CI runs it inside the pytest step (`tests/arch/test_key_writers.py`). A failure
is also printed in the job summary. To run it locally:

```sh
python tools/check_key_writers.py      # prints each failure; exit 1 if any
```

## What counts as a writer

The scan is static. It imports nothing from `plugins/` and finds a write when:

- a module calls `db_write`, `write`, `set_value` or `setValue` with a key:
  a literal, a module constant (`KEY = "ALT"`), or a name built from literal
  text whose expansions are keys: an f-string (`f"QVOTE{n}"`), a `%` format
  (`"FPLIMP%dID" % i`) or `.format` (`"FPL{}ID".format(i)`). A placeholder bound
  by a loop over literals (`for field in ("ID", "LAT")`) stands for those
  strings, and a `range()` or `enumerate()` counter, or a `%d`, for digits;
- a module writes in a loop over literal keys
  (`for key, value in (("FPLSEQ", n), ...): write(key, value)`);
- a module calls one of its own functions that hands a parameter on to a write
  (`_setval(key, v)`, two levels deep);
- a module sets `.value` on, or calls `.set()` on, an item it got with
  `get_item("KEY")`;
- shipped YAML names a key under a write path: a canfix map's `inputs` (bus to
  database), and elsewhere an `output` or `outputs`, such as a compute function;
- a module that writes in a loop over a key table declares the table as a
  module constant named `SOURCE_KEYS`, `ENGINE_KEYS` or `OUTPUT_GROUPS` (demo,
  onspeed), and every key in it is a write;
- a simulator's key map names the key: the X-Plane connection's `recv` rows
  and `datarefs` map, and the `<output>` chunk names of the FlightGear protocol
  file (`fix_fgfs.xml`);
- demo streams the rows the `fixgw.sim` generator builds, so a key a row is
  given in `sim/generator.py` or `sim/fplroute.py` (`row["FPLCMD"] = c`, a
  `(key, value)` pair on a `writes` list) is a demo write, except the `REF*`
  keys. `ROW_STREAMS` in the scanner names those files.

A key written under a computed name (`db_write(self.key, v)`) is not seen. Write
the key as a literal, a module constant or one of the tables above, so the
record can name it. Unseen today, among others: `rais` (keys from the RAIS server's own
configuration), the config-named keys of `rpi_bmp085`, `rpi_button`,
`rpi_mcp3008` and `rpi_rotary_encoder`, and `knobster`, `megasquirt`, `mgl`,
`rtl_433` and `tessera`.

Files under `tools/`, `examples/` and the tests are not writers: they write to a
bench, not to an aircraft.

Two shapes reach a key without naming it at the write (OUT1, AER-6521):

- an item a module gets from one of its own functions that returns the item
  for a key parameter (pyEfis's `self._item(ACK_KEY)`), then writes with
  `.value =`, `.set()` or `.output_value()`. `output_value()` is how pyEfis
  sends an item's value to the gateway;
- a loop over a module-level table of keys
  (`for key, value in zip(SLOT_KEYS, slots): db_write(key, value)`, and
  `enumerate`), where the table is a list of key literals, a comprehension
  over `"CASMSG{}".format(n)` or an f-string, or a `+` of such tables.

The register sorts each module into one of these fields:

- **`writers`**: fix-gateway plugins and compute modules. These are live writers.
- **`pyefis`**: pyEfis instruments, such as the altimeter knob on `BARO` and the
  HSI on `NAVSRC`. These are live writers too. fix-gateway cannot scan pyEfis,
  so these are declared by hand. ARCHG2 checks them from pyEfis's side. The
  flight-plan editor writes the route block, `FPLSEQ`, `DTO*` and `FPLCMD`
  through `fixbridge._set`, which the pyEfis scan cannot follow: those entries
  are declared by reading the code, not by a scan.
- **`sim`**: X-Plane, FlightGear, demo, replay and test plugins. They stand in
  for a live writer, so they are declared but never count as a second writer.
- **`relay`**: netfix, command and the persisters. They pass on another
  client's write.

## The provider declaration (SRC4)

The writer list says who writes a key. It does not say what the number means
when each of them writes it, and ARCH2 (AER-6349) found that they disagree:
`GPS_ELLIPSOID_ALT` in two datums, `ALT` in four, `GPS_ACCURACY_HORIZ` as gpsd's
`epx`, MAVLink's `h_acc` or a constant. Consumers then rebuild what is missing:
the flight plan engine guesses that an install has no fix-type source because
the key was never written (AER-4217), and gdl90_out needs a per-install
`geo_altitude_datum` setting.

So every writer of a source key declares, in its key's line:

| Column | What it says | Tokens |
|---|---|---|
| `datum` | what the number is measured from; for an accuracy key, its statistic | altitude: `indicated`, `pressure`, `msl`, `ellipsoid`, `above_home`, `above_ground`, `density`; position: `wgs84`; heading and track: `magnetic`, `true`; accuracy: `p95`, `one_sigma`, `assumed`, `unstated`; and `unrecorded` |
| `quality` | how the writer tells a reader the value is good | `bad`, `fail` (flags it sets), `passthrough` (copies its source's flags), the FIX keys it writes that qualify this one (`GPS_FIX_TYPE`, `GPS_ACCURACY_HORIZ`), or `none` |
| `on_loss` | what the key does when the writer loses its input | `silence` (goes old after its tol), `fail`, `holds_last` (stays fresh on a dead source), `writes_zero`, `on_change` (a setting or an engine output), `follows` (a compute select) |
| `rate_hz` | how often it writes, where the code or its doc says | a number; leave the writer out when it is not known |

Each cell is `"<token>[, <token>]: note"`. The note says where the value comes
from, and what is wrong with it if anything is:

```yaml
GPS_ELLIPSOID_ALT: {units: ft, datum: {garmin_gnx375: 'msl: NMEA GGA altitude, above the geoid (GALT1)', ...}, quality: {garmin_gnx375: 'GPS_FIX_TYPE: no accuracy key', ...}, on_loss: {...}, writers: [...]}
```

The source keys are ARCH2 section 3.2's, listed as `PROVIDER_KEYS` in the tool:
position, GPS altitude and quality, `ALT`, attitude, accelerations and rate of
turn, airspeed, `BARO`, the active waypoint, and the navigation guidance from
`EXT*` to `COURSE`/`CDI`. `datum` applies to the altitude, position, heading,
track and accuracy keys among them. Simulators declare too: a sim that puts MSL
in `ALT` puts the wrong number in front of the pilot on the bench.

The record states today's truth, wrong parts included. It was seeded from
ARCH2 section 3.1 and read again against `dev` at `66dc8eb`. That tree has SRC1,
SRC2 and MAVALT1. Where the code could not settle a cell, it says `unrecorded`
or `unstated`. The gateway does not read this file, and nothing consumes the
columns yet: pointing the engine's integrity check and gdl90_out at them is a
follow-on.

## The checks, and what to do when one fails

**`ALT: newplugin writes it (src/...:42) and writers.yaml does not list newplugin`.**
Your module writes a key that the register does not list for it. This is the
step this change exists for: decide whether your module should write the key at
all.

- If the key is new, or your module is its first writer, add the module to
  the key's line in `writers.yaml`. A new key also needs its block in roadmap
  section 9.1 (MAOS-DESIGN `docs/AVIONICS_STACK_ROADMAP.md`). That block says
  what the key means, its units and datum, and who writes it.
- If the key already has a writer, read the next failure, which you will hit
  next.

**`VS: 2 live writers (a, b), and it is not on the baseline; add a selector`.**
A second live writer is not allowed. Use the bearing-pointer pattern (roadmap
9.1, `BRG1`/`BRG2`):

1. Each source writes a key of its own, such as `NAV1CDI` and `GPSCDI`.
2. A pilot-set or configured key chooses between them, such as `NAVSRC`.
3. One compute `select` in `config/connections/compute.yaml` writes the shared
   key (`CDI`), so the shared key has exactly one writer.

The scan reads the `select`s from `compute.yaml` and records each one as the
key's `selector`. A key with a selector passes only when the selector is its
only live writer.

**`HEAD: gained live writer(s) c; the baseline allows only a, b`.**
A key that already has several writers cannot gain another. The fix is the
same selector. Adding the module to the baseline is the change a reviewer is
told to refuse.

**`HEAD: now has 1 live writer(s) and is still on the baseline`** and
**`HEAD: lost writer(s) c`.**
Good news: a key got fewer writers. Delete the entry, or the module, from
`tests/arch/key_writers_baseline.yaml`. The baseline only shrinks, and this
check is what makes it shrink.

**`VS: writers.yaml lists gone under writers, and the scan finds no write from it`.**
The register must state today's truth, so remove the stale writer.

**`FPL1ALT: writers.yaml gives a datum for a str key`.**
A datum describes a number. An altitude constraint string or a path terminator
has none, so remove it; `--generate` drops it too.

**`HEAD: writers.yaml lists no writer; remove the entry, or say why it has none in no_writer`.**
A key the database declares for a writer that nothing shipped provides yet
has no writer to list. Say why in `no_writer`, as `CASACKBTN` does: the
`rpi_button` plugin writes whatever key its `btnkey` names, and no shipped
config names that one. When something starts writing the key, the check
fails until `no_writer` is removed, so the note cannot go stale.

**`ALT: an altitude or position key needs a datum`.**
Say what the number is measured from: MSL, ellipsoid (HAE), indicated (baro
corrected), pressure (29.92), or height above home or above ground, and for a
position, the horizontal datum. Where writers differ, give a mapping per writer,
as `ALT` and `GPS_ELLIPSOID_ALT` do. Those differences are the bugs. Write
`unrecorded` only if nobody has read the writer's code yet.

**`HEAD: bbb writes it and declares no `quality` in writers.yaml`.**
A source key's writer must declare `quality` and `on_loss`, and its `datum`
where that applies. Read the writer's code and fill the cell: what it sets on
the key, and what it does when the device goes quiet. If you cannot tell, say
so in the note; do not leave it out.

**`ALT: writers.yaml `datum` for bbb is 'baro'; a datum is one of ...`.**
A cell's first word comes from its column's list above. A `quality` key must
be one the same writer writes.

**`LAT: its writers declare more than one datum (...)`.**
A key has one datum. A writer that puts a different one there needs its own
key (GALT1 adds `GPS_MSL_ALT`), or its conversion fixed (MAVALT1 made mavlink's
`ALT` indicated). `unrecorded` and `unstated` count as different from
everything, so read the code rather than leaving them on a shared key. The keys
that disagree today are on `tests/arch/datum_baseline.yaml`. A listed key may
not gain a writer or change a datum, and when its writers agree it must come off
(`its writers now agree on one datum and it is still on the datum baseline`).

**`class Waypoint: defined in src/.../model.py, and concept_names.yaml allows it only in ...`.**
ARCH0 found seven shapes of a waypoint. Use one of the existing ones, or give
the new shape a name that says what it is. Adding a file to
`tests/arch/concept_names.yaml` is a one-line edit, and it is an architecture
change: under ORG-018, INTEGRATOR holds a PR that makes it for review.

## Regenerating

```sh
python tools/check_key_writers.py --generate --pyefis ../pyEfis
```

This rewrites `writers.yaml` and both baselines from the scan. It keeps the
provider columns and the declared pyEfis writers, and adds any a pyEfis checkout
shows when one is given. It never drops a pyEfis writer; take a stale one out
by hand when ARCHG2 reports it. Use it to fill in the line for a
new key. Do not use it to make the ratchet pass: a regenerated baseline that
grew is the thing a reviewer looks for. `test_generate_reproduces_the_committed_records`
fails if the committed files are not exactly what the scan states.

The first version was generated on 2026-11-22 from fix-gateway `dev` `cf26588`
and pyEfis `dev` `ab272bf`. It reproduces ARCH0's table B exactly: 53 keys with
more than one proven live writer, with the same writers. The datums were then
read from each writer's code. The record includes what is wrong today:

- `mavlink` puts MSL in `ALT`, and puts height above home in `AGL`.
- Two of the four `GPS_ELLIPSOID_ALT` writers put MSL there.

GALT1, MAVALT1 and ARCH1 to ARCH3 fix those keys. This change only records them.
