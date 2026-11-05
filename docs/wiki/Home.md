# FIX-Gateway

**FIX-Gateway is the avionics data broker for the [MakerPlane](https://www.makerplane.org/)
open-source aircraft.** It is a small Python daemon that holds one real-time
database of named flight parameters, and moves data between every source and
every consumer through a plugin architecture — so a display never has to know
*where* its data came from, and a sensor never has to know *who* will read it.

> **FIX** = *Flight Information eXchange* — an open, vendor-neutral protocol
> family for Experimental Amateur-Built (E-AB) avionics. FIX-Gateway is the
> hub that speaks it (and translates everything else into it).

## The mental model

Everything flows through one central database of named keys (`IAS`, `ALT`,
`OILP1`, `NAV1CRS`, …):

```
  SOURCES  ──write──▶   ┌─────────────────────┐   ──read / subscribe──▶  CONSUMERS
  X-Plane / FlightGear  │   the FIX database   │   pyEfis EFIS display
  GPS, Stratux ADS-B    │  named, typed keys   │   pyAvMap moving map
  CAN-FIX bus           │  + quality flags     │   NetFIX network clients
  MAVLink autopilot     │  + staleness timers  │   CAN-FIX output nodes
  RPi IMU / baro / ADC  └─────────────────────┘   autopilot
  MGL / Dynon / GRT         ▲          │
  Compute (derived) ────────┘          └── every box is a plugin
```

A **plugin** is loaded for each connection you need. Source plugins *write*
keys; consumer plugins *read* or *subscribe* to them; a few plugins do both, and
others (like `compute`) derive new keys from existing ones. Add a sensor by
writing/enabling a plugin — nothing else in the system changes.

## Start here

- **New to the project?** → **[[Architecture]]** — how the broker, the database,
  and plugins fit together (read this first).
- **Setting it up for your aircraft?** → **[[Configuration]]**
- **Understanding the parameters?** → **[[The FIX Database]]**
- **Connecting a display / writing a client?** → **[[NetFIX Protocol]]**
- **Adding a data source or feature?** → **[[Plugins]]** and **[[Writing a Plugin]]**

## Where it fits

FIX-Gateway is the **runtime data hub** of the MakerPlane stack — the live
flight-data bus in the air:

- **[pyEfis](https://github.com/makerplane/pyEfis)** — the EFIS display; connects
  over the [[NetFIX Protocol]] (TCP) and reads the keys FIX-Gateway publishes.
- **CAN-FIX** — the in-aircraft hardware bus; the `canfix` plugin bridges it to
  the database in both directions.
- **Reference data** (terrain, charts, panel layouts, aircraft parameter
  profiles) is a *separate* concern handled by
  [makerplane-data](https://github.com/billmallard/makerplane-data) — that
  updates at the hangar; FIX-Gateway carries what changes in flight.

## Quick facts

| | |
|---|---|
| **Language** | Python 3, plugin-based daemon |
| **License** | GPL v2 |
| **Network** | NetFIX ASCII over TCP (default port **3490**) |
| **Config** | `src/fixgw/config/` — `default.yaml` (plugins), `database.yaml` (keys) |
| **Run** | `./fixGw.py` (server) · `./fixGwClient.py` (debug client) |
| **Use** | Experimental Amateur-Built aircraft **only** — not FAA-approved avionics |

## Deeper reference

This wiki explains **how the system works and how to use it**. The
[repository](https://github.com/billmallard/fix-gateway) carries the canonical
detail:

- [`README.md`](https://github.com/billmallard/fix-gateway/blob/master/README.md)
  — project overview + full plugin list
- [`doc/`](https://github.com/billmallard/fix-gateway/tree/master/doc) — the
  reference manual (Sphinx): [intro](https://github.com/billmallard/fix-gateway/blob/master/doc/intro.rst),
  [getting started](https://github.com/billmallard/fix-gateway/blob/master/doc/getting_started.rst),
  [database](https://github.com/billmallard/fix-gateway/blob/master/doc/database.rst),
  [plugin development](https://github.com/billmallard/fix-gateway/blob/master/doc/plugin.rst),
  and per-plugin pages under `doc/plugins/`.
