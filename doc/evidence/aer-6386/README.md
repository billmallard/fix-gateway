# netfix: a stale client flag sent the writer its previous value (AER-6386)

The AFC2b glass check (AER-5856) on the Beelink, against live X-Plane (the
xplane-bench driver, KSBA to KSMX on V27, autopilot coupled), with pyEfis
`dev` 519a6e80 showing the AFC3 autopilot panel in place of the PFD's map
tabs. `glass.py` is the check as AER-5856 ran it. `probe_full.py` runs it
unchanged and adds a netfix subscriber on the seven dial keys, which logs every
update the gateway publishes, plus the time of every xdotool call.
`profile.yaml` is the session profile.

| Run | fix-gateway | Session | ALT SEL, 5 notches up from 7500 |
|---|---|---|---|
| before | `dev` 193b91d | `f5e7a3dd48e8` | 7900 (FAIL) |
| after | this branch f442b1d, staged with `bench-deploy.sh --ref fix-gateway=...` | `af701930d8c6` | 8000 (PASS) |

`*-steps.jsonl` is every PASS/FAIL line of each run. `*-altsel-window.txt`
holds the bus updates for BARO and ALTSEL and the xdotool calls, from the BARO
step's start until 3 s after the ALT SEL tap. It also holds X-Plane's own
`altitude_dial_ft`. Times are seconds from the ALTSEL step's start.

## What the before run shows

```
  -1.922 ALTSEL;7500;10000           the BARO restore moved ALT: the alerter sets ALTSEL's flag
   0.759 XDO click --repeat 5 --delay 150 4
   0.831 ALTSEL;7600;00000           notch 1 (new target: the alerter clears, inside the write)
   0.831 ALTSEL;7600;00000
   0.981 ALTSEL;7600;00000           notch 2's stale flag, applied before its value:
   0.981 ALTSEL;7600;00000             the old value, published to pyEfis too
   0.981 ALTSEL;7700;00000           notch 2's value
   1.091 ALTSEL;7700;00000           notch 3: pyEfis's copy was 7600, so 7700 again
   1.254 ALTSEL;7800;00000
   1.388 ALTSEL;7900;00000           notch 5: ends one notch short
```

Every other key in the same run shows one bus update per notch, at about
0.79, 0.94, 1.09, 1.24 and 1.39 s. X-Plane's dial is slow between 7700 and
7800 (0.242 s, against 0.169 s and 0.182 s either side). Both AER-5856
sessions showed the same slow middle step, 0.299 s and 0.309 s.

The after run starts from the same state (`ALTSEL;7500;10000` at -1.647 s). It
shows 7600 (twice, the value and the alerter's clear), then 7700, 7800, 7900,
8000, one per notch.

## Controls that did not fail

- pyEfis alone, gateway adapter off, same screen and xdotool sequence, 3 trials:
  5 of 5 (the AFC3 panel takes focus from the baro box correctly).
- Live X-Plane, a baro edit of 0.05 inHg, then ALT SEL, 4 trials, plus 2 with
  no baro edit: 5 of 5. A 0.05 inHg change moves ALT about 50 ft, so the
  alerter never set the flag. That flag is the precondition.

## Reproduce

On the Beelink, during a sim-bench session, with the AER-5856 `glass_on.sh`
and `glass_off.sh` hooks pointed at `probe_full.py`:

```
python3 probe_full.py <dir> --port 3490 --xp <x-plane ip> --xp-port 49000 --truth-port 49111
```

The sandbox test `tests/plugins/netfix/test_write_flags_netfix.py` reproduces
the same sequence against the real netfix server, database and alerter without
X-Plane.
