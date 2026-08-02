#!/usr/bin/env python3
"""Dump the FIX database key catalog as JSON for the configurator's type-ahead
dbkey field.

Reuses the real database loader (fixgw.database.init), so templated keys
(CHTec, EGTec, ANLGa, ...) are expanded into their runtime form (CHT1, EGT1,
ANLG1, ...) exactly as the running gateway sees them.

Usage:
    python tools/dump_fix_keys.py [-c CONFIG] [-o OUTPUT]

Defaults: -c src/fixgw/config/database.yaml  -o fix_keys.json
"""
import argparse
import json
import logging
import os
import sys

# Make the package importable when run from the repo root without install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def build_catalog(config_path):
    from fixgw import database
    logging.basicConfig(level=logging.ERROR)
    database.init(config_path)
    try:
        database._stop_update_thread()
    except Exception:
        pass
    keys = []
    for key in sorted(database.listkeys()):
        item = database.get_raw_item(key)
        keys.append({
            "key": key,
            "description": getattr(item, "description", "") or "",
            "type": getattr(item, "typestring", "") or "",
            "units": getattr(item, "units", "") or "",
        })
    return keys


def main():
    here = os.path.dirname(__file__)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config",
                    default=os.path.join(here, "..", "src", "fixgw", "config",
                                         "database.yaml"))
    ap.add_argument("-o", "--output", default="fix_keys.json")
    args = ap.parse_args()

    keys = build_catalog(args.config)
    with open(args.output, "w") as fh:
        json.dump({"keys": keys}, fh, indent=2)
    print("wrote {} ({} FIX keys)".format(args.output, len(keys)))


if __name__ == "__main__":
    main()
