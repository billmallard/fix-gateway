# AER-6497 (CAS2b): `CAS TEST` held until acknowledged

`CAS TEST` used to clear 5 s after the cas plugin started. On the CAS3 bench
(pyEfis `docs/aer-6346-evidence/`) no display ever drew it: pyEfis first draws
about 6 s after it connects, and 11 s after a gateway restart under it. Bill's
ruling (AER-6409): hold it until a display acknowledges it, with a 10-minute
backstop. This directory is the bench run of that change.

## The frames

Placement A, 1024 x 600, software GL (llvmpipe) on a private Xvfb, from
`bench-branch.log`:

| File | What it shows |
|---|---|
| `1-held-at-boot.png` | 20 s after the gateway started: the white `CAS TEST` line, top left of the PFD |
| `2-after-touch.png` | The same panel after one touch on the line: the window draws nothing |
| `3-held-after-gateway-restart.png` | 20 s after a gateway restart under the running display: `CAS TEST` again |

## Results

The CAS3 bench, with the gateway on this branch (`bench-branch.log`) and on
`dev` at 4ce3d59 (`bench-dev.log`). pyEfis is `dev` at b30b785b plus the bench
change in the pyEfis PR for AER-6497 (step 14 now waits up to 60 s for the
draw, checks the line is still there 20 s on, and touches it).

| Check | Branch | dev |
|---|---|---|
| Step 14, boot: `CAS TEST` drawn | PASS, 13.9 s after the gateway started | FAIL, never drawn in 60 s |
| Step 14, boot: still drawn 20 s after the gateway started | PASS | (not reached) |
| Step 1: window empty after the touch | PASS, 0.06 s | FAIL |
| Step 14, restart: `CAS TEST` drawn | PASS, 8.2 s after the restart | PASS, 13.8 s (a slow restart; it cleared soon after) |
| Step 14, restart: still drawn 20 s after the restart | PASS | FAIL |
| Step 14, restart: the touch takes it off | PASS, 0.18 s | PASS, but it had already cleared |

The one other failure in both runs is step 9's end-to-end 2.5 s on software
GL, which also failed in the AER-6346 run; that leg belongs to CERT1.

`drive-aer6083.log` is CAS2's real-server driver (`../aer-6083-cas/drive.py`),
updated to acknowledge `CAS TEST` instead of waiting for it to clear: every
check passes.

## Regenerate

```sh
# fix-gateway: the real-server test (a client connects 15 s after the plugin
# starts, then again 12 s after a full gateway restart)
PYTHONPATH=src python3 -m pytest -o addopts="" tests/plugins/cas/test_cas_server.py
PYTHONPATH=src python3 doc/evidence/aer-6083-cas/drive.py 34983
# pyEfis checkout, with an X server that has XTEST and GLX on $DISPLAY and
# QT_QPA_PLATFORM=xcb (Xvfb :61 -screen 0 1024x600x24 +extension GLX)
python3 docs/aer-6346-evidence/bench.py --fgw ../fix-gateway --out out \
    --placement A --size 1024x600
```

The frames are copied from `out/` (`14a-cas-test-held-at-boot.png`,
`01-quiet.png`, `14a-cas-test-held-after-gateway-restart.png`).
