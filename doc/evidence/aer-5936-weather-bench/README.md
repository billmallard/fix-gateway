# AER-5936 bench read: the netfix weather request on the Pi 5

Hardware bench, Pi 5 (`10.110.10.199`), 2026-11-18. The branch
`aer-5936/fisb-weather-query` at `f8c34f2` was staged on the Pi's fix-gateway
checkout with `bench-deploy.sh --bench pi5 --ref fix-gateway=...`, which
restarted `fixgw.service`, and restored to `dev` afterwards with
`--restore=fix-gateway`.

## 1. The live gateway (`fixgw.service`, netfix on 127.0.0.1:3490)

The live configuration has `STRATUX: false`, so no FIS-B store runs there.

```
03:14:08Z '@xweather;KSCK'  -> b'@xweather;KSCK!005\n'
03:14:08Z '@xweather;KS;CK' -> b'@xweather;KS;CK!003\n'
03:14:08Z '@xbogus'         -> b'@xbogus!001\n'
```

`!005` is the explicit "no store" reply. `!001` now ends its line.

## 2. A private gateway with the store (netfix on 127.0.0.1:3499)

The same checkout, started from a copy of the live config tree with only
`NETFIX` and `STRATUX` enabled, `fisb: true`, and netfix moved to port 3499.
The live tree's md5 list was identical before and after.

```
03:15:33Z '@xweather;KSCK' -> b'@xweather;KSCK;{"station":"KSCK","now":"2026-11-18T03:15:33Z","products":[]}\n'
03:15:33Z '@xweather;KMKC' -> b'@xweather;KMKC;{"station":"KMKC","now":"2026-11-18T03:15:33Z","products":[]}\n'
@xstatus stratux: "FIS-B uplinks": 0, "FIS-B products": 0, "FIS-B current": 0
```

Product count **0**. The plugin logged `no GDL90 Heartbeat for 3 s`: no
GDL90 from the FlightBox (`10.110.10.169`, answering ping) reached UDP 4000 on
the Pi during the run, so this read proves the request on the real server and
socket, not FIS-B reception. GDL2's own bench run (AER-5651) found the 978
radio hears no ground station where the box sits.

## Commands

Request script (run on the Pi):

```python
import socket
s = socket.create_connection(("127.0.0.1", PORT), 3)
f = s.makefile("rb")
for req in ["@xweather;KSCK", "@xweather;KS;CK", "@xbogus"]:
    s.sendall((req + "\n").encode())
    print(repr(req), "->", repr(f.readline()))
```

Private instance: copy `~/makerplane/fixgw/config` to a scratch directory;
in the copy set `preferences.yaml.custom` to enable only `STRATUX` and
`NETFIX`, set `port: 3499` in `connections/netfix.yaml` and `fisb: true` in
`connections/stratux.yaml`; run
`~/fix-gateway/.venv/bin/fixgw --config-file <copy>/default.yaml`.
