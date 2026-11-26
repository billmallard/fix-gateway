# AER-6704 (MENU1b): the Tessera MENU gear opens pyEfis's system menu

2026-11-26, Beelink simulator bench. The real panel is TESS2's board 1
(GUITION JC1060P470C, ESP32-P4 v3.2) running Tessera firmware 1.2.12 on
`/dev/tessera`. fix-gateway `aer-6704/menu1b-gear` be1f484 was staged with
`bench-deploy.sh --ref fix-gateway=aer-6704/menu1b-gear`, and the deploy
refreshed the active `tessera/page.yaml` from the checkout. pyEfis was on
`dev` ed6d0ba4, which has MENU1 (pyEfis #509, AER-5892). X-Plane was not
running, so the flight instruments show their failed-data state. That is the
same in every frame and is not part of this test.

## What was run

`menu_probe.py`, on the bench as `pyefis`, under the bench lock:

```
flock -w 900 /tmp/pyefis-bench.lock python3 menu_probe.py
```

It writes `AD_PRESS 18` to the panel's port from a second writer, as
`../aer-6622-tessera-bench/press_probe.py` does. `AD_PRESS` is the firmware's
own synthetic press: it queues the same `record_event` a touch on the key does
(`main/deck_model.c`, `main/ui/ui.cpp` at v1.2.12). The panel sends the
`deck_event` to fixgw's `tessera` plugin, and the plugin pulses `SYSMENU`. The
probe logs every `SYSMENU` update netfix sends, and grabs the glass with
x11grab. It does not use a SIGUSR1 grab, which blanks the GL attitude panel.

## Result (`probe_run.txt`)

| Time (s) | Event |
|---|---|
| 0.176 | `m0_before.png`; first `AD_PRESS 18` |
| 0.177 | netfix: `SYSMENU;True`, then `SYSMENU;False` |
| 1.848 | `m1_open.png`: the system menu (Data Status, About, Screens, Close) is open over the map |
| 3.851 | second `AD_PRESS 18`, with the menu open; netfix: `SYSMENU;True`, then `SYSMENU;False` |
| 5.520 | second grab, byte-identical to `m1_open.png` (not committed) |
| 14.706 | `m3_after_idle_close.png`, byte-identical to `m0_before.png` |

pyEfis's journal for the same run:

```
21:47:57 pyefis.system_menu - system menu: opened by SYSMENU
21:48:07 pyefis.system_menu - system menu: closed after 10 s idle
```

The second press, made while the menu was open, neither reopened it nor kept it
open: one "opened" line, and it closed 10 s after the first press. That is
MENU1's rule that nothing on the bus extends an open menu. The plugin's
status before the run read `Acks 2, Invalid 0`: the panel accepted the page
with the gear key.

An earlier run of the same probe opened the menu too (journal 21:47:41,
"opened by SYSMENU"). The probe then stopped on a socket-timeout bug of its
own, fixed in the committed version.

## Not shown here

- A finger on the gear. `AD_PRESS` enters the firmware after the touch
  controller, so the touch scan and the finger lift are not in this test.
- The gear as the panel's own glass draws it. The panel accepted the layout;
  `doc/plugins/images/tessera/spike_page.png` is the firmware's own renderer
  (0.9.0 source, the same `fallback_icon` list as 1.2.12) drawing the page.
