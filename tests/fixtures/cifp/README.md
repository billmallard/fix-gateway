<!-- SPDX-License-Identifier: CC0-1.0 -->
# `FAACIFP18` golden-procedure fixture (AER-1600 / PA1, mirrored for PA13)

This is the exact same 156-record excerpt of the live FAA CIFP cycle **2609**
(effective 2026-09-03) that `makerplane-data` committed at
`tests/fixtures/cifp/FAACIFP18` for AER-1600 (PA1) -- "the golden-procedure
fixture set every other PA item tests against"
(`makerplane/briefs/procedures_and_airways_plan.md` section 7). FAA CIFP data
is a US Government work and is public domain; see that repo's
`docs/LICENSE-AUDIT.md` for the audit.

Copied here verbatim rather than referenced across repos because fix-gateway
does not depend on makerplane-data (per the architecture: every box talks
through the FIX bus contract, never directly to another repo) -- see that
repo's own `tests/fixtures/cifp/README.md` for the full selection rationale
(which airports/procedures/leg types and why).

## Why this exists in fix-gateway

`tests/plugins/flightplan/test_engine_golden_procedures.py` decodes real
records straight from this file -- lat/lon, path terminator, course, arc
centre/radius, turn direction, altitude constraint -- and flies them through
the actual `Engine`, so PA13's Tier-2 leg types (`RF`/`AF` arcs, `CA`
altitude-terminated legs, `HM` holds, `PI` procedure turns) are proven
against real published ARINC 424 coding, not just synthetic geometry built
around a round-number centre. This answers the DoD gap INTEGRATOR flagged on
fix-gateway#32 (fix-gateway#29's first acceptance bullet): "each Tier-2 type
tested against the PA1 golden fixtures, including at least one real published
procedure per type flown end to end."

Real procedures exercised (see the test file's module docstring for the exact
CIFP line/column derivation of every decoded value):

| Type | Procedure | What it proves |
|---|---|---|
| `RF` arc | KABQ RNAV (GPS) Y RWY 21, `FOXRR` transition | two chained real arcs, same centre (`CFDXH`), ~2.8nm radius |
| `AF` arc | 09J VOR-A, `TOMDY` transition | a navaid-centred DME arc (SSI VOR, 7.0nm) |
| `CA` altitude-terminated | KSBA ILS RWY 07, missed approach | real climb course (074.6 deg) and altitude (700 ft) |
| `HM` hold | KSBA ILS RWY 07, missed approach (GOLET) | real inbound course (307 deg) and turn direction |
| `PI` procedure turn | 09J VOR-A, `SSI` transition | real outbound course (172.1 deg) and leg length (10.0nm) |

## Regenerating or extending

Not scripted here either -- see makerplane-data's own fixture README for how
the excerpt was pulled from a live CIFP cycle. If that fixture set is ever
extended (a new procedure, a new leg type), copy the updated file here too so
both repos keep testing against the identical golden set, per the brief's
"nobody re-derives them."
