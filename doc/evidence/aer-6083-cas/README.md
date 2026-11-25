# AER-6083 (CAS2): the cas plugin on a real server

The shipped config tree, run through `fixgw.server:main` (the service's entry
point; `-m fixgw.server` builds every plugin twice), with netfix moved to a
private port and driven over raw netfix. No bench, no live gateway touched.

## Regenerate

```sh
PYTHONPATH=src python3 doc/evidence/aer-6083-cas/drive.py 34983 | tee drive.log
```

`drive.py` copies `src/fixgw/config` to a temp dir, sets netfix's port, lists
fuel tank 3 in `connections/cas.yaml` with a 10 s grace (the shipped file
lists no tanks), and starts the gateway. Every check prints `PASS` or `FAIL`.
The script exits non-zero on any `FAIL`.

## What `drive.log` shows

The step numbers are the CAS1 brief's bench steps (section 12.2), as far as a
gateway without a display can take them:

| Brief step | Drive | Result |
|---|---|---|
| 1 | `@r` every CAS key on boot; `@qCASMSG1` | All present; the report splits into its 8 fields (no `;` in the text) |
| (C2) | CAS TEST at start | Shown 0.3 s after connect, gone after about 5 s. Since AER-6497 it is held until `CASACK` covers it, and `drive.py` now acknowledges it; `drive.log` is the AER-6083 run |
| 2 | NAV1 received, then `NAV1CDI` failed | `2\|0\|NAVFAIL\|NAV FAIL\|` 0.33 s after the fail; `NAVFAIL` true |
| 3 | `CASACK` = `CASSEQ` | The line reads acknowledged (`2\|1\|...`); `CASTOP` 0 |
| 4 | Clear the flag | Line gone |
| 17 | `NAVSRC` to NAV2, never received | `NAVFAIL` true (the HSI flag), no `NAV FAIL` line (C5) |
| 5 | `APENG` written true, then stopped | `3\|0\|APLINK\|AP LINK LOST\|`, a warning, 2 s after the last write (APENG's tol); `CASTOP` 3 |
| 18 (part) | `APENG` resumed | Line gone 5.5 s later: the 5 s clear delay |
| 7 | Tank 3 listed, never written | `FUEL QTY 3 NO DATA` after the grace |
| (C3) | `CASACK` = `CASSEQ` + 100 | Acknowledges nothing |
| (tol) | 3.5 s with no change | `CASCNT`, `CASSEQ`, `CASMSG1`, `NAVFAIL` not old: the 1 s rewrite |

The plugin status at the end shows 5 entries, none refused, no evaluation or
callback errors, and the gateway log has no `ERROR` line from cas.

The gateway exits 255 on SIGTERM because the **demo** plugin "did not
shutdown properly". Clean `dev` does the same, so that is not this change.

## On the bench

`bench.log`: the Beelink's running gateway, branch staged with
`bench-deploy.sh --ref fix-gateway=aer-6083/cas-plugin`. A raw `@rCASCNT`
answers `0;01000`: the keys are there, old because the bench's edited
`preferences.yaml` does not turn the plugin on. With CAS turned on for the
probe in `preferences.yaml.custom`, CAS TEST shows at 4 s and is gone at 8 s,
and HDG FAIL appears between 25 s and 33 s, after its 30 s grace: X-Plane was
off and the only HEAD write was the bench `custom.ini` seed, which is not a
reading. The bench was then put back on `dev`.

## Not here

- Steps 8, 9, 10, 14 and 16 are display-side (`CAS FAIL`, `GATEWAY LINK
  LOST`, `CATALOG REFUSED`, `CAS NOT SHOWN`). They belong to CAS3 in pyEfis.
- Steps 5, 6 and 18 used a netfix writer for `APENG`, not ArduPlane SITL.
  The plugin sees only the key and its flags, which is what the writer sets.
- Step 13 (C1) and step 15 (C3 restart) are unit tests that run the real
  plugin thread on the real database:
  `test_slow_failing_evaluation_does_not_slow_a_watched_writer` and
  `test_casseq_starts_above_a_stale_casack_after_restart`.
