# AFC2 bench log per mode: X-Plane session bf3a33402db4

X-Plane 12.4.3, Laminar C172 G1000 (GFC-700), en route on V27 near KSBA, 2026-11-18 05:03-05:08Z.
A private fixgw on the Beelink (fix-gateway 5a5d9f6, udp_in 49101) ran the adapter; the live gateway was untouched.
Times are seconds after the adapter's first raw dataref row. **Bus** rows are what the gateway published
(value changes, with the old flag shown as `old`). **Sim** rows are the raw datarefs the adapter received.

## Driver couples the autopilot (ROL, HDG, then NAV to GPS)

Sequencer:

-  -62.51  waiting for the driver to couple (APENG, APLAT GPS)
-   24.90  PASS AP engaged on GPS  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-   24.90  caps  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]

| t | Bus key | value |
|---|---|---|
| 0.00 | APENG | False |
| 0.00 | FDENG | False |
| 0.00 | APLAT | "" |
| 0.00 | APLATARM | "" |
| 0.00 | APVERT | "" |
| 0.00 | APVERTARM | "" |
| 0.00 | APMSG | "" |
| 0.00 | APDISC | False |
| 0.00 | ALTSEL | 7900 |
| 0.00 | VSSEL | 0 |
| 0.00 | IASSEL | 105.0 |
| 8.25 | ALTSEL | 7500 |
| 8.87 | APENG | True |
| 8.87 | FDENG | True |
| 8.87 | APLAT | ROL |
| 8.87 | APVERT | PIT |
| 9.13 | APLAT | HDG |
| 9.68 | APVERT | ALT |
| 9.84 | APLAT | GPS |
| 24.90 | APCMD | HDG |

| t | Sim dataref | value |
|---|---|---|
| 0.00 | servos_on | 0 |
| 0.00 | fd_mode | 0 |
| 0.00 | heading_status | 0 |
| 0.00 | nav_status | 0 |
| 0.00 | roll_status | 2 |
| 0.00 | attitude_status | 2 |
| 0.00 | pitch_status | 2 |
| 0.00 | vvi_status | 0 |
| 0.00 | speed_status | 0 |
| 0.00 | altitude_hold_status | 0 |
| 0.00 | alt_dial | 7900 |
| 0.00 | vvi_dial | 0 |
| 0.00 | ias_dial | 105 |
| 0.00 | hsi_source | 0 |
| 0.00 | hsi_signal | 0 |
| 8.25 | alt_dial | 7500 |
| 8.87 | servos_on | 1 |
| 8.87 | fd_mode | 2 |
| 9.13 | heading_status | 2 |
| 9.13 | roll_status | 0 |
| 9.13 | attitude_status | 0 |
| 9.68 | pitch_status | 0 |
| 9.68 | altitude_hold_status | 2 |
| 9.76 | hsi_source | 2 |
| 9.76 | hsi_signal | 1 |
| 9.84 | heading_status | 0 |
| 9.84 | nav_status | 2 |

## HDG (head request)

Sequencer:

-   24.90  pressed (verb HDG, seq 1)
-   25.01  PASS APLAT HDG  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]

| t | Bus key | value |
|---|---|---|
| 24.47 | APLAT | GPS |
| 24.47 | APVERT | ALT |
| 24.66 | APENG | True |
| 24.66 | FDENG | True |
| 24.66 | APLATARM | "" |
| 24.66 | APVERTARM | "" |
| 24.66 | APMSG | "" |
| 24.66 | APDISC | False |
| 24.90 | APCMD | HDG |
| 24.90 | APCMDSEQ | 1 |
| 24.96 | APLAT | HDG |

| t | Sim dataref | value |
|---|---|---|
| 24.96 | heading_status | 2 |
| 24.96 | nav_status | 0 |

## Negative test: NAV with the HSI on a silent radio

Sequencer:

-   30.03  PASS NAV2DH no signal for 5 s  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-   30.03  HSI source
-   33.03  pressed (verb NAV, seq 2)
-   33.14  PASS APMSG refusal  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG=NAV refused: no signal on NAV2 APDISC=False]
-   38.17  PASS APLAT HDG, nothing armed  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG=NAV refused: no signal on NAV2 APDISC=False]
-   38.17  HSI source back to GPS

| t | Bus key | value |
|---|---|---|
| 29.62 | APLAT | HDG |
| 29.66 | APVERT | ALT |
| 29.89 | APENG | True |
| 29.89 | FDENG | True |
| 29.89 | APLATARM | "" |
| 29.89 | APVERTARM | "" |
| 29.89 | APMSG | "" |
| 29.89 | APDISC | False |
| 30.03 | XPHSISRC | 1.0 |
| 33.03 | APCMD | NAV |
| 33.03 | APCMDSEQ | 2 |
| 33.05 | APMSG | NAV refused: no signal on NAV2 |
| 38.17 | XPHSISRC | 2.0 |
| 41.18 | ALTSEL | 8000 |

| t | Sim dataref | value |
|---|---|---|
| 30.08 | hsi_source | 1 |
| 30.08 | hsi_signal | 0 |
| 38.22 | hsi_source | 2 |
| 38.22 | hsi_signal | 1 |

## VS to ALT capture at ALTSEL

Sequencer:

-   41.18  targets
-   42.68  pressed (verb VS, seq 3)
-   42.78  PASS APVERT VS, ALT armed  [APLAT=HDG APLATARM= APVERT=VS APVERTARM=ALT APMSG=NAV refused: no signal on NAV2 APDISC=False]
-   42.78  PASS VSSEL still 500 after the press  [APLAT=HDG APLATARM= APVERT=VS APVERTARM=ALT APMSG=NAV refused: no signal on NAV2 APDISC=False]
-   96.85  PASS APVERT ALT (captured)  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-   96.85  captured

| t | Bus key | value |
|---|---|---|
| 40.81 | APENG | True |
| 40.81 | FDENG | True |
| 40.81 | APLATARM | "" |
| 40.81 | APVERTARM | "" |
| 40.81 | APDISC | False |
| 40.86 | APMSG | NAV refused: no signal on NAV2 |
| 40.94 | APLAT | HDG |
| 41.16 | APVERT | ALT |
| 41.18 | ALTSEL | 8000 |
| 41.18 | VSSEL | 500 |
| 42.68 | APCMD | VS |
| 42.68 | APCMDSEQ | 3 |
| 42.74 | APVERT | VS |
| 42.74 | APVERTARM | ALT |
| 43.07 | APMSG | "" |
| 96.81 | APVERT | ALT |
| 96.81 | APVERTARM | "" |
| 96.81 | VSSEL | 0 |
| 106.85 | ALTSEL | 8500 |

| t | Sim dataref | value |
|---|---|---|
| 41.25 | alt_dial | 8000 |
| 41.25 | vvi_dial | 500 |
| 42.74 | vvi_status | 2 |
| 42.74 | altitude_hold_status | 1 |
| 42.74 | vvi_dial | 0 |
| 43.26 | vvi_dial | 500 |
| 96.81 | vvi_status | 0 |
| 96.81 | altitude_hold_status | 2 |
| 96.81 | vvi_dial | 0 |

## IAS climb to ALTSEL

Sequencer:

-  106.85  targets
-  108.35  pressed (verb IAS, seq 4)
-  108.45  PASS APVERT IAS, ALT armed  [APLAT=HDG APLATARM= APVERT=IAS APVERTARM=ALT APMSG= APDISC=False]
-  129.27  PASS IAS within 5 kt of 80  [APLAT=HDG APLATARM= APVERT=IAS APVERTARM=ALT APMSG= APDISC=False]
-  136.21  PASS APVERT ALT (captured)  [APLAT=HDG APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-  136.21  captured

| t | Bus key | value |
|---|---|---|
| 106.38 | APENG | True |
| 106.38 | FDENG | True |
| 106.38 | APLATARM | "" |
| 106.38 | APDISC | False |
| 106.56 | APLAT | HDG |
| 106.56 | APVERT | ALT |
| 106.56 | APVERTARM | "" |
| 106.56 | APMSG | "" |
| 106.85 | ALTSEL | 8500 |
| 106.85 | IASSEL | 80.0 |
| 108.35 | APCMD | IAS |
| 108.35 | APCMDSEQ | 4 |
| 108.42 | APVERT | IAS |
| 108.42 | APVERTARM | ALT |
| 136.11 | APVERT | ALT |
| 136.11 | APVERTARM | "" |

| t | Sim dataref | value |
|---|---|---|
| 106.90 | alt_dial | 8500 |
| 106.90 | ias_dial | 80 |
| 108.42 | speed_status | 2 |
| 108.42 | altitude_hold_status | 1 |
| 108.42 | ias_dial | 95.7186 |
| 108.96 | ias_dial | 80 |
| 136.11 | speed_status | 0 |
| 136.11 | altitude_hold_status | 2 |

## HDG to NAV capture

Sequencer:

-  146.21  pressed (verb NAV, seq 5)
-  146.31  PASS GPS armed or captured  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-  146.31  PASS APLAT GPS (captured)  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]

| t | Bus key | value |
|---|---|---|
| 145.73 | APLAT | HDG |
| 145.73 | APMSG | "" |
| 145.84 | APENG | True |
| 145.84 | FDENG | True |
| 145.84 | APLATARM | "" |
| 145.84 | APDISC | False |
| 145.96 | APVERT | ALT |
| 145.96 | APVERTARM | "" |
| 146.21 | APCMD | NAV |
| 146.21 | APCMDSEQ | 5 |
| 146.27 | APLAT | GPS |
| 156.31 | APCMD | LVL |

| t | Sim dataref | value |
|---|---|---|
| 146.27 | heading_status | 0 |
| 146.27 | nav_status | 2 |

## LVL, then NAV

Sequencer:

-  156.31  pressed (verb LVL, seq 6)
-  156.41  PASS lateral mode left GPS  [APLAT=ROL APLATARM= APVERT=PIT APVERTARM=ALT APMSG= APDISC=False]
-  166.42  after LVL  [APLAT=ROL APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]
-  166.42  pressed (verb NAV, seq 7)
-  166.52  PASS APLAT GPS again  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=False]

| t | Bus key | value |
|---|---|---|
| 156.15 | APLAT | GPS |
| 156.15 | APMSG | "" |
| 156.24 | APENG | True |
| 156.24 | FDENG | True |
| 156.24 | APLATARM | "" |
| 156.24 | APDISC | False |
| 156.31 | APCMD | LVL |
| 156.31 | APCMDSEQ | 6 |
| 156.32 | APVERT | ALT |
| 156.32 | APVERTARM | "" |
| 156.35 | APLAT | ROL |
| 156.35 | APVERT | PIT |
| 156.35 | APVERTARM | ALT |
| 157.84 | APVERT | ALT |
| 157.84 | APVERTARM | "" |
| 166.42 | APCMD | NAV |
| 166.42 | APCMDSEQ | 7 |
| 166.46 | APLAT | GPS |

| t | Sim dataref | value |
|---|---|---|
| 156.35 | nav_status | 0 |
| 156.35 | roll_status | 2 |
| 156.35 | attitude_status | 2 |
| 156.35 | pitch_status | 2 |
| 156.35 | altitude_hold_status | 1 |
| 157.84 | attitude_status | 0 |
| 157.84 | pitch_status | 0 |
| 157.84 | altitude_hold_status | 2 |
| 166.46 | nav_status | 2 |
| 166.46 | roll_status | 0 |

## AP disconnect, then the acknowledgement

Sequencer:

-  176.52  pressed (verb DISC, seq 8)
-  176.62  PASS APENG False, APDISC True  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=True]
-  179.65  PASS APDISC stays latched  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=True]
-  179.65  pressed (verb DISC, seq 9)
-  182.69  FAIL APDISC acknowledged  [APLAT=GPS APLATARM= APVERT=ALT APVERTARM= APMSG= APDISC=True]

| t | Bus key | value |
|---|---|---|
| 176.15 | APENG | True |
| 176.15 | FDENG | True |
| 176.15 | APLATARM | "" |
| 176.15 | APVERT | ALT |
| 176.15 | APVERTARM | "" |
| 176.15 | APDISC | False |
| 176.34 | APLAT | GPS |
| 176.52 | APCMD | DISC |
| 176.52 | APCMDSEQ | 8 |
| 176.53 | APMSG | "" |
| 176.57 | APENG | False |
| 176.57 | APDISC | True |
| 179.51 | APENG | False old |
| 179.51 | FDENG | True old |
| 179.51 | APLAT | GPS old |
| 179.51 | APLATARM |  old |
| 179.51 | APVERT | ALT old |
| 179.51 | APVERTARM |  old |
| 179.51 | APDISC | True old |
| 179.65 | APCMDSEQ | 9 |
| 180.51 | APMSG |  old |

| t | Sim dataref | value |
|---|---|---|
| 176.57 | servos_on | 0 |
| 176.57 | fd_mode | 1 |

## X-Plane stops sending

Sequencer:

-  182.69  logging until the stop hook; the bus log shows the flags
-  203.70  stopped

| t | Bus key | value |
|---|---|---|

| t | Sim dataref | value |
|---|---|---|
