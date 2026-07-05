#!/usr/bin/env python3
#  SPDX-License-Identifier: GPL-2.0-or-later
"""Export the aircraft-parameters schema for the web configurator.

fix-gateway is the source of truth for FIX keys and their aux slots
(``config/database/*.yaml``). This tool joins those definitions with a
small curation table (grouping, labels, multiplicity, engine-provider
types) and the CAN-FIX id map, and emits
``aircraft_params_schema.json`` — the file the configurator's Aircraft
Parameters page renders its forms from
(makerplane-data ``docs/canfix_configurator.md``).

Usage:
    python -m fixgw.tools.export_param_schema            # stdout
    python -m fixgw.tools.export_param_schema -o out.json

Only stdlib + PyYAML. Replicated keys keep their TEMPLATE form
("TACHe", "CHTec", "FUELQt"); the UI instantiates them from the
profile's engine/cylinder/tank counts (e.g. CHTec -> CHT11..CHT14).
"""

import argparse
import datetime
import json
from pathlib import Path

import yaml

SCHEMA_VERSION = 1

_CONFIG = Path(__file__).resolve().parent.parent / "config"

# Curation: group -> (label, repeat, provider, [key templates]).
# ``repeat``: none | engine | engine_cylinder | tank — which profile
# count(s) instantiate the key template's e/c/t placeholders.
# ``provider``: engine groups belong to an engine-type provider
# (piston today; turbine/electric/hybrid reserve the seam — see the
# spec, section 6a).
_GROUPS = [
    {"id": "speeds", "label": "Airspeeds", "repeat": None,
     "keys": ["IAS"]},
    {"id": "engine", "label": "Engine", "repeat": "engine",
     "provider": "piston",
     "keys": ["TACHe", "MAPe", "OILPe", "OILTe", "FUELFe", "FUELPe"]},
    {"id": "cylinders", "label": "Cylinders", "repeat": "engine_cylinder",
     "provider": "piston",
     "keys": ["CHTec", "EGTec", "CHTMAXe"]},
    {"id": "fuel", "label": "Fuel tanks", "repeat": "tank",
     "keys": ["FUELQt"]},
    {"id": "electrical", "label": "Electrical", "repeat": None,
     "keys": ["VOLT", "CURRNT"]},
    {"id": "air", "label": "Air / environment", "repeat": None,
     "keys": ["OAT", "CAT"]},
    # Placeholder per the spec decision: values firm up with the
    # OnSpeed/AOA integration.
    {"id": "aoa", "label": "AOA (calibration placeholder)",
     "repeat": None, "keys": ["AOA"]},
]

# Aux slots that are per-aircraft configuration. Everything else on a
# key (initial live value, tol) is runtime, not profile.
_BAND_AUX = ["Min", "Max", "lowWarn", "highWarn", "lowAlarm", "highAlarm"]


def _load_defs(config_dir: Path) -> dict:
    defs = {}
    for f in sorted((config_dir / "database").glob("*.yaml")):
        if f.name == "variables.yaml":
            continue
        doc = yaml.safe_load(f.read_text()) or {}
        for item in doc.get("items", []) or []:
            if isinstance(item, dict) and "key" in item:
                defs[item["key"]] = item
    return defs


def _load_variables(config_dir: Path) -> dict:
    doc = yaml.safe_load(
        (config_dir / "database" / "variables.yaml").read_text()) or {}
    v = doc.get("variables", {}) or {}
    return {k: v[k] for k in ("e", "c", "t") if k in v}


def _load_canfix_ids(config_dir: Path) -> dict:
    """fixid -> canid from the canfix plugin map (best-effort: the map
    keys real bus traffic; template keys like TACHe are mapped per
    instance there, so template keys may have no single id)."""
    path = config_dir / "canfix" / "map.yaml"
    ids: dict = {}
    if not path.is_file():
        return ids
    doc = yaml.safe_load(path.read_text()) or {}
    for section in doc.values():
        if not isinstance(section, list):
            continue
        for row in section:
            if isinstance(row, dict) and "fixid" in row and "canid" in row:
                ids.setdefault(str(row["fixid"]), row["canid"])
    return ids


def build_schema(config_dir: Path = _CONFIG) -> dict:
    defs = _load_defs(config_dir)
    canfix_ids = _load_canfix_ids(config_dir)
    groups = []
    missing = []
    for g in _GROUPS:
        keys = []
        for tmpl in g["keys"]:
            d = defs.get(tmpl)
            if d is None:
                missing.append(tmpl)
                continue
            aux = list(d.get("aux") or [])
            keys.append({
                "key": tmpl,
                "label": d.get("description", tmpl),
                "type": d.get("type", "float"),
                "units": d.get("units", ""),
                # sanity range for form validation = the database range
                "min": d.get("min"),
                "max": d.get("max"),
                "aux": aux,
                "band_aux": [a for a in aux if a in _BAND_AUX],
                "canfix_id": canfix_ids.get(tmpl),
            })
        out = {k: g[k] for k in ("id", "label", "repeat") }
        if "provider" in g:
            out["provider"] = g["provider"]
        out["keys"] = keys
        groups.append(out)
    if missing:
        raise SystemExit(
            f"curated keys not found in database defs: {missing}")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated": datetime.date.today().isoformat(),
        "source": "fix-gateway config/database",
        "default_counts": _load_variables(config_dir),
        # Reserved provider seam (spec 6a): parameter sets per engine
        # type. Only piston is populated today; turbine/electric/hybrid
        # land here without configurator changes.
        "engine_providers": {"piston": {"label": "Piston",
                                        "groups": ["engine", "cylinders"]}},
        "groups": groups,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", help="write JSON here (default stdout)")
    ap.add_argument("--config", default=str(_CONFIG),
                    help="fixgw config dir (default: in-repo)")
    args = ap.parse_args(argv)
    schema = build_schema(Path(args.config))
    text = json.dumps(schema, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output} ({sum(len(g['keys']) for g in schema['groups'])} keys)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
