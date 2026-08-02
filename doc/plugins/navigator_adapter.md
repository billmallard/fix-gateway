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
| `COURSE` | Bearing to active waypoint (deg true) | `bearing_to_wp_deg` |
| `XTRACK` | Signed cross-track error (nm) | `cross_track_error_nm_signed` |
| `CDI` | Course deviation indicator [-1.0, +1.0] | derived from `XTRACK` |
| `GPS_FIX_TYPE` | Fix quality (0=none, 1=GPS, 2=DGPS/WAAS) | `gps_fix_type` |

Only non-`None` fields are written. Fields not populated by a sentence are left
unchanged in the database.

---

## Built-in Adapters

### `garmin_nmea`

Parses the Garmin Aviation Output 1 NMEA 0183 sentence set. Compatible with
any Garmin GPS navigator (GNX 375, GNS 430/530, GTN 650/750, GPS 175, GNC 355)
and any NMEA 0183-compliant device outputting RMC/GGA/RMB/APB sentences.

| Sentence | Data extracted |
|----------|----------------|
| `$GPRMC` | LAT, LONG, GS, TRACK |
| `$GPGGA` | LAT, LONG, GPS_ELLIPSOID_ALT, GPS_FIX_TYPE |
| `$GPRMB` | XTRACK, CDI |
| `$GPAPB` | COURSE |

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
Positive XTRACK → aircraft is RIGHT of desired course → CDI needle deflects LEFT (negative CDI)
Negative XTRACK → aircraft is LEFT of desired course  → CDI needle deflects RIGHT (positive CDI)
```

This matches the FIX-Gateway `compute.py` XTE function and the Garmin RMB steer-direction encoding:
- `'L'` (steer left to return) = aircraft is RIGHT of track = **positive XTRACK**
- `'R'` (steer right to return) = aircraft is LEFT of track = **negative XTRACK**

CDI scaling: `CDI = clamp(-xtrack / cdi_full_scale_nm, -1.0, 1.0)`
