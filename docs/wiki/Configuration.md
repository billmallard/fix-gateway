# Configuration

FIX-Gateway is configured entirely by YAML files under `src/fixgw/config/`.
Two questions are answered there: **which plugins run** (and how), and **what
keys exist** (and their aircraft-specific values). Getting-started walkthrough:
[`doc/getting_started.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/getting_started.rst).

## The files

| File | Answers | Edit? |
|---|---|---|
| `default.yaml` | the database file, init files, which connection files are read, logging | rarely |
| `preferences.yaml` | maps each `*_CONFIG` name to a connection file, and holds the `enabled:` on/off flags | **no** — use `preferences.yaml.custom` |
| `preferences.yaml.custom` | your overrides of `preferences.yaml` | yes |
| `connections/<plugin>.yaml` | one plugin's settings | yes, per plugin |
| `database.yaml` | the key namespace (a set of includes) | **no** — use `database/custom.yaml` |
| `database/custom.yaml` | your added/overridden keys | yes |
| `database/variables.yaml` | engine/cylinder/tank/node counts | yes |
| `init_data/*.ini` | initial + aircraft-specific values (V-speeds, bands) | yes (`custom.ini`) |

`{CONFIG}` in a path setting expands to the config directory, so paths stay
portable. The server expands it in `database file` and `initialization files`;
some plugins expand it in their own path settings (`canfix`, `canaerospace` and
`dronecan` in `mapfile`, `data_recorder` in `filepath`, `state_persist` and
`flightplan` in `state_file`).

## Turning a plugin on or off

Every shipped plugin is already wired in. Turning one on or off is a single flag
in `preferences.yaml.custom`:

```yaml
enabled:
  XPLANE: true
  DEMO: false
```

The server reads `preferences.yaml`, then merges `preferences.yaml.custom` over
it, key by key. Leave `preferences.yaml` alone so updates can replace it; it
lists every flag and its shipped value.

How the flag reaches the plugin:

**1. The connection file** `connections/<name>.yaml` describes the plugin:

```yaml
xplane:
  load: XPLANE                  # a flag name, looked up in preferences 'enabled:'
  module: fixgw.plugins.xplane  # the Python module
  # ...any plugin-specific settings (passed to the plugin as self.config)
```

`load:` may also be a plain `yes` / `no`. A flag name that is missing from
`enabled:` counts as off.

**2. `preferences.yaml`** maps a stable include name to that file, and sets the
flag:

```yaml
includes:
  XPLANE_CONFIG: connections/xplane.yaml
enabled:
  XPLANE: false
```

**3. `default.yaml`** lists which include names are read:

```yaml
connections:
  include:
    - NETFIX_CONFIG
    - COMPUTE_CONFIG
    - XPLANE_CONFIG
```

The shipped `default.yaml` lists every connection file in `connections/`
except the test suite's `test.yaml`, and `preferences.yaml` has a flag for
each, so you only ever touch the flag. The full plugin catalogue is on the
**[[Plugins]]** page.

> **One key, one writer.** Before enabling two source plugins, check they don't
> both write the same keys — e.g. `xplane` and `stratux` both publish attitude
> and will fight. Enable exactly one source per key (see
> [[Architecture]] → *One key, one writer*).

## Initial and aircraft-specific values

`init_data` files set values after the database is built — this is where an
airplane's numbers live. They use a simple `KEY[.aux] = value` syntax:

```ini
# aux values (V-speeds) on the IAS key
IAS.Vs   = 48
IAS.Vne  = 165
# a gauge band
OILP1.lowAlarm = 25
# a plain initial value
NAVSRC   = 2
```

Comments go on their own line, starting with `#`. Anything after the `=` is
the value, so a trailing comment becomes part of it, and a bad aux value stops
the gateway at startup.

`default.yaml` lists the init files in order (later overrides earlier), so a
layered setup is idiomatic:

```yaml
initialization files:
  - "{CONFIG}/init_data/default.ini"                 # sensible defaults
  - "{CONFIG}/init_data/tires.ini"
  # - "{CONFIG}/init_data/airplanes/cessna/c170b.ini"# per-airframe
  # - "{CONFIG}/init_data/engines/rotax/582.ini"     # per-engine
  - "{CONFIG}/init_data/custom.ini"                   # your edits win
```

Make your edits in `custom.ini` (and `database/custom.yaml`) so software updates
to the shipped files still reach you.

## Runtime state vs. configuration

Most values are *sensed* (written by a source every cycle). A few are *pilot
selections* — e.g. `NAVSRC` (the EFIS nav-source selector). Those aren't sensed,
so left alone they reset to their `init_data` default on every start. The
`state_persist` plugin (on in the shipped config) remembers the last value of
selected keys across restarts, so a selection survives a reboot:

```yaml
state_persist:
  load: yes
  module: fixgw.plugins.state_persist
  keys:              # keys whose last value should persist
    - NAVSRC
    - GPSSRC
  # state_file: "{CONFIG}/persist_state.json"   # optional; default shown
  # restore_delay: 2.0                           # seconds after start
```

It restores the saved values `restore_delay` seconds after start, then saves
any change to the file within a second.

## Running

```bash
./fixGw.py                        # start the server (installed: fixgw)
./fixGw.py --config-file PATH     # use a specific default.yaml
./fixGw.py --debug                # or --verbose; raise the log level
./fixGwClient.py                  # console debug client (installed: fixgwc)
./fixGwClient.py --gui            # GUI debug client
```

Without `--config-file`, the server runs from `~/makerplane/fixgw/config/`. On
every start it copies the shipped config there: missing files are copied, files
you have not edited are updated to the new version, and a file you have edited
is left alone, with the new version written beside it as `<name>.dist`. That is
why edits belong in the `custom` files: the rest keep receiving updates.

With `--config-file`, the server reads that file and the `preferences.yaml`
beside it. A file outside `~/makerplane/fixgw/config/` is never updated, which
suits a per-aircraft config directory.

Under systemd the server exits at once unless `default.yaml` has
`auto start: true` (the shipped value).
