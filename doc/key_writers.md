# Declaring who writes a FIX key (ARCHG1)

Every box talks to every other box through the FIX database, so a key is a
contract: one meaning, one unit, one datum, and one writer at a time. ARCH0
(AER-6340) found 53 keys with more than one live writer and nothing to stop the
54th. `ALT` alone has six writers, and among them they put at least three
different altitudes on the bus: indicated, MSL and pressure. This check stops that count from rising, and makes it fall as each key is
fixed.

Backlog item ARCHG1 (AER-6350). Policy: ORG-018 (maos-workspace).

## The three records

| File | What it holds |
|---|---|
| `src/fixgw/database/writers.yaml` | One line per key that some module writes: its units, the datum of an altitude or position key, its writers by kind, and its selector if it has one. It is a design record: the gateway never loads it, and `database/*.yaml` is still the runtime truth for a key. |
| `tests/arch/key_writers_baseline.yaml` | The keys allowed more than one live writer today, each with its writers. It can only shrink. |
| `tests/arch/concept_names.yaml` | The files allowed to define a class named `Waypoint`, `Point`, `Position`, `Leg`, `Plan` or `Route`. |

`tools/check_key_writers.py` scans the tree and checks it against all three.
CI runs it inside the pytest step (`tests/arch/test_key_writers.py`). A failure
is also printed in the job summary. To run it locally:

```sh
python tools/check_key_writers.py      # prints each failure; exit 1 if any
```

## What counts as a writer

The scan is static. It imports nothing from `plugins/` and finds a write when:

- a module calls `db_write`, `write`, `set_value` or `setValue` with a key:
  a literal, a module constant (`KEY = "ALT"`), or an f-string whose expansions
  are keys (`f"QVOTE{n}"`);
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
  file (`fix_fgfs.xml`).

A key written under a computed name (`db_write(self.key, v)`) is not seen. Write
the key as a literal, a module constant or one of the tables above, so the
record can name it. Unseen today, among others: `rais` (keys from the RAIS server's own
configuration), the config-named keys of `rpi_bmp085`, `rpi_button`,
`rpi_mcp3008` and `rpi_rotary_encoder`, and `knobster`, `megasquirt`, `mgl`,
`rtl_433` and `tessera`.

Files under `tools/`, `examples/` and the tests are not writers: they write to a
bench, not to an aircraft.

The register sorts each module into one of these fields:

- **`writers`**: fix-gateway plugins and compute modules. These are live writers.
- **`pyefis`**: pyEfis instruments, such as the altimeter knob on `BARO` and the
  HSI on `NAVSRC`. These are live writers too. fix-gateway cannot scan pyEfis,
  so these are declared by hand. ARCHG2 checks them from pyEfis's side.
- **`sim`**: X-Plane, FlightGear, demo, replay and test plugins. They stand in
  for a live writer, so they are declared but never count as a second writer.
- **`relay`**: netfix, command and the persisters. They pass on another
  client's write.

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

**`ALT: an altitude or position key needs a datum`.**
Say what the number is measured from: MSL, ellipsoid (HAE), indicated (baro
corrected), pressure (29.92), or height above home or above ground, and for a
position, the horizontal datum. Where writers differ, give a mapping per writer,
as `ALT` and `GPS_ELLIPSOID_ALT` do. Those differences are the bugs. Write
`unrecorded` only if nobody has read the writer's code yet.

**`class Waypoint: defined in src/.../model.py, and concept_names.yaml allows it only in ...`.**
ARCH0 found seven shapes of a waypoint. Use one of the existing ones, or give
the new shape a name that says what it is. Adding a file to
`tests/arch/concept_names.yaml` is a one-line edit, and it is an architecture
change: under ORG-018, INTEGRATOR holds a PR that makes it for review.

## Regenerating

```sh
python tools/check_key_writers.py --generate --pyefis ../pyEfis
```

This rewrites `writers.yaml` and the baseline from the scan. It keeps the
datums, and it takes the pyEfis writers from a pyEfis checkout when one is
given, or from the existing register when not. Use it to fill in the line for a
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
