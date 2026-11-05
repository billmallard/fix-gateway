# Architecture — how it works

FIX-Gateway is deliberately simple: **one in-memory database in the middle, and
plugins around the edge.** Everything else follows from that.

```
        ┌──────────────── plugins load from the config file ────────────────┐
        │                                                                    │
   source plugins                     THE DATABASE                     consumer plugins
   (write keys) ───────────▶   key → value + quality + aux   ───────────▶  (read / subscribe)
                                    │      ▲                                  │
                                    │      └── compute plugin derives         │
                                    └────────  new keys from existing ones ───┘
```

There is no message routing to configure, no wiring between plugins. A plugin
only ever talks to **the database**; the database notifies whoever is
interested.

## 1. The database in the middle

The heart of FIX-Gateway is a single process-wide database of **items**, one per
aircraft data point (`IAS`, `ALT`, `OILP1`, `NAV1CRS`, …). Each item carries far
more than a number — see **[[The FIX Database]]** for the full model — but the
essentials are:

- a **typed, bounded value** (float / int / bool / str, clamped to min/max);
- **quality flags** — `old` (stale), `bad` (suspect), `fail` (do not use),
  `secfail` (redundant source failed);
- a **tolerance (`tol`) timer** — if nobody writes the item within its window,
  it is automatically marked **`old`**. This is how a display knows a sensor
  went quiet without any explicit "sensor lost" message;
- **aux values** — named side-parameters (e.g. `IAS` carries `Vs`, `Vno`, `Vne`;
  a gauge carries `lowWarn`/`highAlarm`) that drive instrument arcs and bands.

Writing a value updates the timestamp (clearing `old`) and **notifies every
registered callback synchronously**. That callback mechanism is the entire
event system: consumers subscribe, `compute` functions recompute, the network
plugin pushes updates to clients.

## 2. Plugins around the edge

A **plugin** is a Python class (`Plugin`, extending `plugin.PluginBase`) that the
server loads at startup based on the config. It gets a clean API to the
database and does one job:

| Role | Examples | What it does |
|---|---|---|
| **Source** | `xplane`, `gpsd`, `stratux`, `canfix`, `rpi_bno055`, `mavlink` | reads an external protocol/sensor, **writes** keys |
| **Consumer** | `netfix` (to pyEfis/clients), `canfix` (to bus nodes) | **reads/subscribes** and forwards data out |
| **Compute** | `compute` | **derives** keys from other keys (TAS, density altitude, nav-source routing…) |
| **Infrastructure** | `quorum`, `annunciate`, `data_recorder`, `state_persist` | arbitration, alerting, logging, persistence |

The lifecycle is minimal: the server calls each plugin's `run()` (which returns
immediately, spawning a thread if it needs one) and later `stop()`. See
**[[Writing a Plugin]]**. Which plugins load, and their settings, is entirely a
config decision — **[[Configuration]]**.

## 3. One key, one writer (the most important rule)

The database is a shared namespace and, by default, **has no source
arbitration**: a write simply overwrites the value and notifies. So if **two
plugins write the same key, they fight** — last-writer-wins, producing a
flickering mix.

> **Enable exactly one source per key.** Running both `xplane` and `stratux`
> attitude, for example, means they overwrite each other. This is the single
> most common integration mistake.

When you genuinely need multiple sources for one key — redundancy, voting,
failover — that is the job of the **`quorum`** plugin, which arbitrates between
candidate sources and publishes the chosen value (and sets `secfail` when a
redundant source drops). Deliberate arbitration, not accidental collision.

*(Related: a runtime SIM-vs-FLIGHT interlock and richer source-selection are
areas of ongoing work in the MakerPlane stack.)*

## 4. The compute plugin — first-class derived data

`compute` is worth calling out because a lot of avionics "logic" lives there
rather than in a source. It runs small **functions** wired in the config, each
taking input keys and writing an output key, re-running whenever an input
changes. Examples in use:

- `TAS`/density-altitude from `IAS`, `OAT`, `ALT`;
- `wrap360` for magnetic ground track (`TRACKM = TRACK + MAGVAR`);
- `select` — an **EFIS nav-source selector**: `COURSE`/`CDI`/`GSI` = the source
  chosen by `NAVSRC` (GPS / NAV1 / NAV2), so the display and autopilot read one
  canonical key regardless of which radio is active.

Because compute functions consume and produce ordinary keys, they compose freely
and can be added without touching any source or consumer. See the compute plugin
doc: [`doc/plugins/compute.rst`](https://github.com/billmallard/fix-gateway/blob/master/doc/plugins/compute.rst).

## 5. Talking to the outside — NetFIX

Consumers that live in another process (pyEfis, a moving map, a control-head, a
debug client) connect over the **NetFIX ASCII protocol** — a line-based TCP
protocol on port **3490**. A client can read a key, write a key, subscribe to
updates, and query metadata, all over one socket. The server abstracts away
whether a value came from a simulator, a real sensor, or a manual test. Full
details: **[[NetFIX Protocol]]**.

## 6. Two contracts, kept separate

FIX-Gateway is the **runtime flight-data bus** — the values that change *in
flight*. It deliberately does **not** manage *reference* data (terrain, charts,
panel layouts, aircraft-parameter profiles) — that changes on a *cycle*, at the
hangar, and is handled by the separate
[makerplane-data](https://github.com/billmallard/makerplane-data) system. Keeping
the two apart means the thing that updates over WiFi is never in the path of the
thing that runs in the air.

## Where to go next

- **[[The FIX Database]]** — the parameter model in depth
- **[[Configuration]]** — enable plugins, customize for your aircraft
- **[[Plugins]]** — the full ecosystem
- **[[NetFIX Protocol]]** — the network interface
- **[[Writing a Plugin]]** — add your own source or feature
