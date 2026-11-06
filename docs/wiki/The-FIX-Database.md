# The FIX Database

The database is the one thing every plugin talks to. It is a namespace of
**items**, each a single aircraft data point with a typed value, quality flags,
a staleness timer, and optional side-parameters. Understanding an item is most
of understanding FIX-Gateway.

Reference: [`doc/database.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/database.rst).

## An item

Items are declared in YAML. A representative one:

```yaml
- key: IAS
  description: Indicated Airspeed
  type: float                # float | int | bool | str
  min: 0.0                   # values are clamped to [min, max]
  max: 1000.0
  units: knots
  initial: 0.0               # value at startup
  tol: 2000                  # tolerance / time-to-live, milliseconds
  aux: [Min,Max,V1,V2,Vne,Vfe,Vmc,Va,Vno,Vs,Vs0,Vx,Vy,Vgnd]
```

| Field | Meaning |
|---|---|
| `key` | the name everything references (`IAS`, `ALT`, `OILP1`, `NAV1CRS`) |
| `type` | `float`, `int`, `bool`, or `str` |
| `min` / `max` | bounds — writes outside are **clamped**, not rejected |
| `units` | for display; the value is stored in these units |
| `initial` | value set at startup |
| `tol` | **time-to-live in ms**: if not rewritten within `tol`, the item goes `old` (see below). `0`, the default, disables staleness |
| `aux` | named side-parameters attached to this key (see *Aux values*) |

## Quality flags — the honesty of a value

Every item carries flags that travel *with* the value, so a consumer always
knows whether to trust it:

| Flag | Set by | Meaning | Typical display response |
|---|---|---|---|
| `old` | **automatic** — value not written within `tol`; a source may also set it | stale; the source went quiet | grey the reading |
| `bad` | a source | value is suspect but not confirmed failed | grey / caution |
| `fail` | a source | value cannot be trusted — do not use | remove needle / red flag |
| `secfail` | redundancy logic | the *redundant* source failed | annunciate loss of backup |
| `annunciate` | the `annunciate` plugin, or a source | the value is outside its limits and should be annunciated | flash / highlight |

The `old` flag is the quiet workhorse: because `tol` marks an item stale
automatically, a display can flag a dead sensor **without any explicit
"sensor lost" message** ever being sent. A dropped X-Plane connection, an
unplugged CAN node, a hung serial link — all surface the same honest way.

*(Writing a value refreshes the timestamp and clears a `tol` expiry. An `old`
that the source set itself stays until that source clears it. A routing plugin
that keeps re-copying a value can still mask a source's staleness unless it
copies the flags too — a real consideration when writing derived keys.)*

## Aux values — the side-parameters

Beyond its value, a key can carry named **aux** slots. These are how
airframe/engine limits reach the instruments:

- `IAS` carries V-speeds: `Vs`, `Vs0`, `Vfe`, `Vno`, `Vne`, `Va`, `Vx`, `Vy`…
  → the airspeed tape derives its white/green/yellow/red arcs from them.
- a gauge like `OILP1` carries `Min`, `Max`, `lowWarn`, `highWarn`, `lowAlarm`,
  `highAlarm` → the gauge colours its caution/warning bands from them.

An **unset aux means "no band"**: an aux that is never set, or is set to
`None`, draws nothing. (In an `init_data` file, write `None`; an empty value
stops the gateway at startup.) This draws a clean boundary that matters for the
whole stack:

> **V-speeds and gauge bands are FIX-*database* values, not display-layout
> options.** The same airspeed widget shows different arcs on a Cub and an RV
> because the *data* differs, not the layout. (This is why the pyEfis panel
> editor deliberately leaves these numbers out and a separate *aircraft
> parameter profile* fills them in.)

## The database is parametric

The namespace isn't fixed — it's generated from your aircraft's shape.
`database/variables.yaml` declares counts:

```yaml
variables:
  e: 1   # engines
  c: 6   # cylinders
  t: 3   # fuel tanks
  n: 5   # CAN nodes
  # ...plus a (analogs), b (buttons), g, r (encoders), s (touchscreen
  # buttons), d (radios), w (wheels), f (flight-plan slots), k (traffic slots)
```

…and the database templates keys accordingly — `EGT11`…`EGT16`, `CHT11`,
`FUELQ1`…`FUELQ3`, per-node touchscreen-button keys, and so on. Set the counts
to match the airplane and the right keys exist.

## Customizing — without forking the files

The shipped database is a set of includes (`database/ahrs.yaml`,
`engine.yaml`, …) and is **not meant to be edited** — edits would block future
updates. Instead:

- **Add or override items** → `database/custom.yaml`, under a top-level
  `items:` key. A definition there replaces the whole item from any earlier
  include. It is read after everything except `flightplan.yaml`,
  `traffic.yaml`, `sim.yaml` and `last.yaml`, so it cannot override those.
- **Set initial / aircraft-specific values** → the `init_data/*.ini`
  files (e.g. `IAS.Vne = 160`, `OILP1.lowAlarm = 25`). Later files override
  earlier ones, so a per-airplane overlay layers cleanly over the defaults.

See **[[Configuration]]** for how these files are wired together.

## Common key domains

| Domain | Examples |
|---|---|
| Navigation | `LAT`, `LONG`, `ALT`, `IAS`, `TAS`, `GS`, `HEAD`, `TRACK`, `XTRACK` |
| AHRS | `PITCH`, `ROLL`, `YAW`, `ALAT`, `ALONG`, `ANORM` |
| Engine (per engine/cyl) | `TACH1`, `MAP1`, `EGT1x`, `CHT1x`, `OILP1`, `OILT1`, `FUELF1` |
| Radios / nav | `COMACTFREQ1`, `NAV1CRS`, `NAV1CDI`, `NAVSRC`, `COURSE`, `CDI`, `GSI` |
| Flight plan | `FPL1ID`…`FPL100ID`, `FPLCMD`, `FPLCRS`, `FPLCDI`, `WPNAME` — see [[Flight Plan Engine]] |
| Controls / trims | `CTLPTCH`, `CTLROLL`, `TRIMP`, `TRIMR`, `TRIMY` |

The full namespace is in the `database/` includes and
[`doc/appendix/database.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/appendix/database.rst).
