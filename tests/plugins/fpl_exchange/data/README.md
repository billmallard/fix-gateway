# fpl_exchange test data

- `FlightPlanv1.xsd`: Garmin's published FlightPlan v1 schema, unchanged,
  from https://www8.garmin.com/xmlschemas/FlightPlanv1.xsd (fetched
  2026-11-22, 12237 bytes, sha256
  `40e94cfae5885ddd0303ffa471bc45a080206f450da33ec73927c45fff83f35e`). The
  tests validate against it with lxml. To refresh and check it:

      curl -sS -o FlightPlanv1.xsd https://www8.garmin.com/xmlschemas/FlightPlanv1.xsd
      sha256sum FlightPlanv1.xsd

- `handmade_full.fpl`: written by hand from the schema to use every optional
  element (author, link, created, elevation, comment, extensions,
  `xsi:schemaLocation`, `INT-VRP`, a user waypoint with an empty country code,
  a table entry the route does not use). It is **not** a ForeFlight export.

- `foreflight/`: `.fpl` files exported from ForeFlight (*Send To > Share FPL
  file > Save to Files*), one per route, named for the route, for example
  `ksba_v27_ksmx.fpl`. Only Bill can make these. `test_fpl.py` reads, schema-checks
  and round-trips every file here. With none, that test is skipped by name.
