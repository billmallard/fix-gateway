# AER-5462 (DEMO7) evidence: demo traffic on the P3b map layer

fix-gateway `aer-5462/demo-gdl90-traffic` @ `2a17ecf`, run as a real server
(`fixGw.py`) with the stock config: DEMO on, STRATUX off, COMPUTE on (it writes
`PALT`), and NETFIX moved to private port 34977. FLIGHTPLAN and STATE_PERSIST
were off, so the run wrote no state files. pyEfis `dev` @ `ab870309`.

The layer is pyEfis's own `TrafficLayer` (P3b), configured on the pyavtools
FIX client the way the map configures it, and reading the `TFC` block over
netfix. No script writes a `TFC` key. The path is: demo plugin, GDL90 frames,
the stratux plugin's `gdl90.decode_datagram` and `TrafficWriter`, the
database, netfix, then pyEfis.

| File | What it is |
|---|---|
| `demo_traffic_t0_t20.png` | the two frames side by side: left 4 s after connecting, right 20 s later |
| `demo_traffic_t0.png`, `demo_traffic_t20.png` | the frames, 800x800, north up, 10 nm from the centre to the edge |
| `render.log` | what the layer held at each frame: identity, position, altitude, VS, track, relative altitude, stale |
| `render_demo_traffic.py` | the render script |

The render script adds the grey rings at 3, 5 and 7 nm (the orbit radii) and
the ownship cross. Everything in white is the layer's own drawing.

What to see:

- Three targets, all live, and the layer reports no source failure.
- Each target carries a relative-altitude tag in hundreds of feet. DEMO1 is
  about +10 and DEMO3 about +25 at t0. Both tags go up by 3 at t20, because
  the keyframe demo descends ownship from `PALT` 1154 to 879 ft.
- DEMO2 shows a down arrow at t20 (-704 fpm, past the 500 fpm trend
  threshold) as it descends through ownship's altitude (tag `00`).
- Targets move along their orbits between the frames, and their arrowheads
  turn with the track.

The orbits are centred on ownship's position when the first frame was built.
The keyframe demo then moves ownship about 1 nm west, and the rings follow it.
That is why the targets sit slightly off the rings at t20. DEMO2 will centre
the orbits on the hold with `center_on()`.

## Reproduce

From a fix-gateway checkout of the branch, with a pyEfis `dev` checkout beside
it:

```sh
cp -r src/fixgw/config /tmp/cfg
sed -i 's/    port: 3490/    port: 34977/' /tmp/cfg/connections/netfix.yaml
printf 'enabled:\n  FLIGHTPLAN: false\n  STATE_PERSIST: false\n' > /tmp/cfg/preferences.yaml.custom
PYTHONPATH=src python3 fixGw.py --config-file /tmp/cfg/default.yaml &
cd ../pyEfis
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 \
  ../fix-gateway/doc/evidence/aer-5462-demo-traffic/render_demo_traffic.py \
  127.0.0.1 34977 demo_traffic 20
```
