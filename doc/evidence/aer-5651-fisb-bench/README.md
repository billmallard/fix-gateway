# FIS-B bench run, 2026-11-15 (GDL2, AER-5651)

**Result: 0 uplinks, 0 products.** The bench FlightBox has UAT enabled and a
978 MHz SDR running, but at the bench it receives no UAT signal at all, so it
sends no 0x07 Uplink messages.

## The FlightBox's own state

- `/getSettings`: `UAT_Enabled: true`, `ES_Enabled: true`.
- Its log (`http://10.110.10.169/logs/stratux.log`) shows the 978 SDR
  (`UAT Device Serial: stratux:978`, R820T tuner) opened at 17:52 and again at
  18:24 UTC. Every 30 s status line since then reads
  `UAT/min 0/34 [maxSS=0.00%]`: 220 lines, about 110 minutes with no signal.
  Over the same lines 1090ES runs at up to 2,119 messages a minute.
- `/getStatus` at 20:28 UTC: `UAT_messages_last_minute: 0`, and
  `UAT_METAR_total`, `UAT_TAF_total`, `UAT_PIREP_total`, `UAT_NOTAM_total`,
  `UAT_SIGMET_total` and `UAT_OTHER_total` all 0.

## The capture

`capture_uplink.py` (this directory) ran on the Pi 5 (10.110.10.199). The
fixgw stratux plugin was off and nothing else held UDP 4000.

    python3 capture_uplink.py 150 /tmp/aer5651-uplinks.txt

Output:

    utc_start_end 2026-11-15T20:26:16Z 2026-11-15T20:28:46Z
    datagrams 4679 senders ['10.110.10.169']
    message ids {'0x65': 891, '0x4C': 2970, '0x00': 148, '0xCC': 148, '0x53': 148, '0x0A': 148, '0x0B': 148, '0x1F': 10, '0x1E': 8, '0x14': 77}
    uplinks (0x07) 0

So the GDL90 path from the FlightBox to the Pi works (AHRS, ownship,
heartbeats and traffic arrive), and no uplink is sent. A product count above
zero needs the 978 radio to hear a FIS-B ground station: an antenna or a
place with a view of one. The decoder itself is proven on 704 real RF
uplinks (`tests/plugins/stratux/data/`).
