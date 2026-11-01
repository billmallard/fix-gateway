# Navigator Adapter Plugin

**Module:** `fixgw.plugins.navigator_adapter`

A vendor-agnostic serial navigator ingest plugin. One plugin instance handles
transport (serial port) and FIX database writes; vendor-specific parsing lives
in a `ParserAdapter` class that can be dropped in without touching the core.

---

## Architecture

```
Serial port (bytes)
      │
      ▼
  MainThread
      │  raw bytes per line
      ▼
 ParserAdapter.parse_line()       ← vendor-specific, replaceable
      │  List[NormalizedNavMessage]
      ▼
   fix_map.apply_to_db()          ← canonical field → FIX key mapping
      │  db_write calls
      ▼
 FIX-Gateway database
```

---

## Configuration

```yaml
navigator:
  load: NAVIGATOR
  module: fixgw.plugins.navigator_adapter
  port: /dev/ttyUSB0
  baud: 4800
  adapter: garmin_nmea        # registered adapter name
  cdi_full_scale_nm: 5.0      # 5.0 = en-route, 0.3 = approach
```

---

## FIX Database Keys Written

| Key | Description | Source field |
|-----|-------------|--------------|
| `LAT` | Latitude (decimal degrees) | `position.lat_deg` |
| `LONG` | Longitude (decimal degrees) | `position.lon_deg` |
| `GPS_ELLIPSOID_ALT` | GPS altitude (feet MSL) | `position.alt_ft_msl` |
| `GS` | Ground speed (knots) | `ground_speed_kt` |
| `TRACK` | Track over ground (deg true) | `ground_track_deg_true` |
| `EXTCRS` | Desired track of the active leg (deg magnetic) | `desired_track_deg_mag` |
| `EXTCDI` | Course deviation [-1.0, +1.0] | derived from `cross_track_error_nm_signed` |
| `EXTTF` | TO/FROM: 0 off, 1 TO, 2 FROM | `to_from` |
| `EXTXTK` | Signed cross-track error (nm), positive = right of track | `cross_track_error_nm_signed` |
| `GPS_FIX_TYPE` | Contract scale: 0-1 no fix, 2 2D, 3 3D, 4 3D DGPS/WAAS, 5 3D RTK | `gps_fix_type` |

Only non-`None` fields are written. Fields not populated by a sentence are left
unchanged in the database.

The navigator is an **external navigator**: its guidance goes to the `EXT*`
inputs, and compute's `GPSSRC` select (`connections/compute.yaml`) routes them
into `GPSCRS`/`GPSCDI`/`GPSTF`, which its `NAVSRC` select routes into
`COURSE`/`CDI`/`TOFROM`; a second `GPSSRC` select routes `EXTXTK` into
`XTRACK`. The plugin never writes `COURSE`, `CDI` or `XTRACK`; compute is their
one writer (AER-4473). Set `GPSSRC` to 1 to fly the navigator's guidance.

---

## Built-in Adapters

### `garmin_nmea`

Parses the Garmin Aviation Output 1 NMEA 0183 sentence set. Compatible with
any Garmin GPS navigator (GNX 375, GNS 430/530, GTN 650/750, GPS 175, GNC 355)
and any NMEA 0183-compliant device outputting RMC/GGA/RMB/APB sentences.

| Sentence | Data extracted |
|----------|----------------|
| `$GPRMC` | LAT, LONG, GS, TRACK |
| `$GPGGA` | LAT, LONG, GPS_ELLIPSOID_ALT, GPS_FIX_TYPE (quality + satellites, mapped by `fixgw.nmea_fix.gps_fix_type`) |
| `$GPGSA` | 2D/3D mode, held for the next GGA's GPS_FIX_TYPE |
| `$GPRMB` | EXTXTK and EXTCDI (from the cross-track error and steer direction) |
| `$GPAPB` | EXTCRS (field 8, bearing origin to destination: the leg DTK), EXTTF (field 7, perpendicular passed = FROM; field 1 not valid = off) |

APB field 8 may be true or magnetic. A true bearing is made magnetic with the
latest `$GPRMC` magnetic variation; if the navigator sends a true bearing and
no variation, `EXTCRS` is not written rather than a true course labelled
magnetic.

Typical serial settings: 9600 baud, 8N1.

---

## Adding a New Vendor Adapter

### Step 1 — Capture logs

Connect to the device and capture representative serial output:

```bash
cat /dev/ttyUSB0 > vendor_capture.log
```

Include normal operation, signal-loss events, and edge-case messages.

### Step 2 — Implement the adapter

Create `src/fixgw/plugins/navigator_adapter/adapters/<vendor>.py`:

```python
from typing import List
from ..base import AdapterHealth, NormalizedNavMessage, ParserAdapter, Position

class MyVendorAdapter(ParserAdapter):
    def __init__(self):
        self._parse_errors = 0

    def name(self) -> str:
        return "my_vendor"

    def supported_protocols(self) -> List[str]:
        return ["My Vendor proprietary binary protocol v2"]

    def parse_line(self, raw: bytes) -> List[NormalizedNavMessage]:
        try:
            # Parse raw bytes, return one or more NormalizedNavMessage objects.
            # Set only the fields this sentence provides; leave others as None.
            msg = NormalizedNavMessage(
                position=Position(lat_deg=..., lon_deg=...),
                ground_speed_kt=...,
            )
            return [msg]
        except Exception:
            self._parse_errors += 1
            return []

    def health(self) -> AdapterHealth:
        return AdapterHealth(parse_errors=self._parse_errors)

    def reset(self, reason: str = "") -> None:
        self._parse_errors = 0
```

Rules:
- `parse_line()` **must never raise**. Catch all exceptions, increment counters, return `[]`.
- Return only the fields the sentence actually provides. Leave everything else `None`.
- One call to `parse_line()` may return multiple `NormalizedNavMessage` objects if one
  input line carries multiple logical updates (rare but allowed).

### Step 3 — Register the adapter

In `src/fixgw/plugins/navigator_adapter/__init__.py`, add to `_ADAPTERS`:

```python
from .adapters.my_vendor import MyVendorAdapter

_ADAPTERS: dict[str, type[ParserAdapter]] = {
    "garmin_nmea": GarminNmeaAdapter,
    "my_vendor": MyVendorAdapter,   # ← add this
}
```

### Step 4 — Add unit tests

Create `tests/plugins/navigator_adapter/test_my_vendor_adapter.py`. At minimum, test:

- [ ] `name()` and `supported_protocols()` return expected values
- [ ] A representative valid sentence produces the correct `NormalizedNavMessage` fields
- [ ] A void/invalid-status sentence returns `[]`
- [ ] A corrupt/truncated frame returns `[]` and increments `health().parse_errors`
- [ ] `reset()` clears the error counter
- [ ] Sign conventions for any directional fields (e.g., XTE, glideslope)

Run with:

```bash
PYTHONPATH=src python -m unittest tests.plugins.navigator_adapter.test_my_vendor_adapter -v
```

### Step 5 — Validate stale and fail behavior

- Disconnect the serial cable while running; verify the plugin logs the error and does not crash.
- Reconnect; verify data resumes writing to the DB.
- If the adapter tracks stale state, verify `health().stale` is set after the configured timeout.

### Step 6 — Bench-test with a FIX client

Start fix-gateway with the `navigator_adapter.yaml` connection config (with `adapter: my_vendor`)
and use `fixGwClient.py` or pyEfis to observe the live database writes before flight integration.

---

## XTE / CDI Sign Convention

```
Positive XTE → aircraft is RIGHT of desired course → CDI needle deflects LEFT (negative EXTCDI)
Negative XTE → aircraft is LEFT of desired course  → CDI needle deflects RIGHT (positive EXTCDI)
```

This matches `FPLXTK`/`XTRACK` and the Garmin RMB steer-direction encoding:
- `'L'` (steer left to return) = aircraft is RIGHT of track = **positive XTE**
- `'R'` (steer right to return) = aircraft is LEFT of track = **negative XTE**

CDI scaling: `EXTCDI = clamp(-xte / cdi_full_scale_nm, -1.0, 1.0)`, the `FPLCDI`
convention (`fixgw.nmea_fix.cdi_from_cross_track`).
