# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/billmallard/fix-gateway/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                             |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|----------------------------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/fixgw/\_\_init\_\_.py                                        |        1 |        0 |        0 |        0 |    100% |           |
| src/fixgw/cfg.py                                                 |      205 |        0 |      112 |        0 |    100% |           |
| src/fixgw/client/\_\_init\_\_.py                                 |       52 |        6 |       22 |        5 |     85% |35, 82, 94, 95-\>98, 99-101, 109 |
| src/fixgw/client/command.py                                      |      167 |      118 |       52 |        4 |     27% |26-38, 52-83, 89, 93-98, 102-113, 121-148, 174-186, 191-204, 208-215, 219, 223, 226 |
| src/fixgw/client/common.py                                       |       49 |       36 |       24 |        4 |     26% |28-52, 54-60, 63-\>75, 68-72 |
| src/fixgw/client/connection.py                                   |        6 |        2 |        0 |        0 |     67% |     29-30 |
| src/fixgw/client/dbItemDialog.py                                 |      149 |       75 |        6 |        2 |     49% |34-37, 47-58, 61-72, 92-108, 158-196 |
| src/fixgw/client/gui.py                                          |       24 |        8 |        2 |        1 |     65% | 36-44, 51 |
| src/fixgw/client/simulate.py                                     |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/client/statusModel.py                                  |       44 |        9 |        4 |        1 |     79% |45-\>exit, 51-57, 70, 73 |
| src/fixgw/client/table.py                                        |       66 |       56 |        4 |        0 |     14% |28-32, 37-96, 99-102 |
| src/fixgw/client/ui/\_\_init\_\_.py                              |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/client/ui/itemDialog\_ui.py                            |       40 |        0 |        0 |        0 |    100% |           |
| src/fixgw/client/ui/main\_ui.py                                  |       53 |       49 |        0 |        0 |      8% |14-60, 63-75 |
| src/fixgw/database.py                                            |      367 |        0 |       96 |        0 |    100% |           |
| src/fixgw/geo.py                                                 |      114 |        1 |       16 |        2 |     98% |120, 187-\>196 |
| src/fixgw/netfix/QtDb.py                                         |      147 |        0 |       10 |        0 |    100% |           |
| src/fixgw/netfix/\_\_init\_\_.py                                 |      250 |        0 |       70 |        0 |    100% |           |
| src/fixgw/netfix/db.py                                           |      434 |        0 |      110 |        0 |    100% |           |
| src/fixgw/nmea\_fix.py                                           |       61 |        2 |       30 |        1 |     97% |87-88, 143-\>148 |
| src/fixgw/plugin.py                                              |       44 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/X728/\_\_init\_\_.py                           |       71 |       71 |       18 |        0 |      0% |    21-109 |
| src/fixgw/plugins/\_\_init\_\_.py                                |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/annunciate.py                                  |      197 |        6 |       84 |        2 |     96% |133-134, 136-140, 257 |
| src/fixgw/plugins/avionicsduino/\_\_init\_\_.py                  |      116 |        7 |       28 |        2 |     94% |62, 76-78, 160, 171-172 |
| src/fixgw/plugins/avionicsduino/mapping.py                       |      132 |        8 |       72 |        4 |     93% |100, 112, 150, 249, 299-302 |
| src/fixgw/plugins/avionicsduino/protocol.py                      |       22 |        1 |        4 |        0 |     96% |       124 |
| src/fixgw/plugins/canaerospace/\_\_init\_\_.py                   |      127 |        9 |       24 |        2 |     91% |48-52, 65-67, 101-\>109, 169-\>176, 174-175 |
| src/fixgw/plugins/canaerospace/mapping.py                        |      176 |       15 |      102 |       10 |     90% |119, 121, 141, 189, 237, 240, 254, 274, 285, 324-\>333, 362-365, 383-384 |
| src/fixgw/plugins/canaerospace/protocol.py                       |       98 |        1 |       18 |        1 |     98% |       237 |
| src/fixgw/plugins/canfix/\_\_init\_\_.py                         |      115 |        0 |       30 |        0 |    100% |           |
| src/fixgw/plugins/canfix/mapping.py                              |      268 |        0 |      124 |        0 |    100% |           |
| src/fixgw/plugins/command.py                                     |      125 |        0 |       30 |        0 |    100% |           |
| src/fixgw/plugins/compute.py                                     |      731 |        8 |      412 |        8 |     99% |847, 849, 886, 888, 906, 908, 923, 927 |
| src/fixgw/plugins/data\_playback/\_\_init\_\_.py                 |       75 |        0 |       24 |        0 |    100% |           |
| src/fixgw/plugins/data\_recorder/\_\_init\_\_.py                 |       95 |        0 |       30 |        0 |    100% |           |
| src/fixgw/plugins/db\_persister/\_\_init\_\_.py                  |       59 |        0 |        8 |        0 |    100% |           |
| src/fixgw/plugins/demo/\_\_init\_\_.py                           |      288 |       19 |      106 |       12 |     92% |81-82, 121-122, 163, 168, 170-171, 238-239, 259, 262, 306-307, 320-\>322, 327, 362, 415, 417, 420 |
| src/fixgw/plugins/demo/engine.py                                 |      135 |        0 |       48 |        0 |    100% |           |
| src/fixgw/plugins/demo/traffic.py                                |      110 |        2 |       16 |        0 |     98% |  158, 161 |
| src/fixgw/plugins/dimmer.py                                      |       10 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/dronecan/\_\_init\_\_.py                       |      179 |        7 |       40 |        1 |     96% |119-122, 186-\>188, 203-204, 251-252 |
| src/fixgw/plugins/dronecan/driver.py                             |       33 |        1 |       10 |        1 |     95% |        46 |
| src/fixgw/plugins/dronecan/mapping.py                            |      248 |        3 |      120 |        4 |     98% |182, 184, 371, 477-\>exit |
| src/fixgw/plugins/dynon/\_\_init\_\_.py                          |       71 |        0 |       16 |        0 |    100% |           |
| src/fixgw/plugins/fgfs/\_\_init\_\_.py                           |      254 |       20 |       80 |       10 |     91% |103, 141, 172, 182-183, 278-\>276, 281-283, 291-\>297, 306-307, 316-\>318, 320-321, 329-330, 342-343, 347-349, 350-\>353, 409-\>411, 412, 419 |
| src/fixgw/plugins/flightplan/\_\_init\_\_.py                     |      309 |       15 |       68 |       10 |     93% |172, 263-\>exit, 272, 275, 303, 337-\>341, 348, 408, 411-413, 464-466, 518-519, 534, 551-\>553 |
| src/fixgw/plugins/flightplan/engine.py                           |     1090 |       24 |      398 |       20 |     97% |274, 276, 278-281, 288, 884, 928, 1112-\>1114, 1160, 1195-1196, 1238, 1300-\>1293, 1318, 1324, 1369, 1602, 1667-1668, 1691-1692, 1697-\>1699, 1701, 1745, 1759 |
| src/fixgw/plugins/garmin\_gnx375/\_\_init\_\_.py                 |      112 |        0 |       36 |        0 |    100% |           |
| src/fixgw/plugins/gpsd/\_\_init\_\_.py                           |       55 |        0 |       10 |        0 |    100% |           |
| src/fixgw/plugins/grand\_rapids\_eis/\_\_init\_\_.py             |      154 |        0 |       20 |        1 |     99% |   94-\>43 |
| src/fixgw/plugins/ifly/\_\_init\_\_.py                           |       64 |        0 |       24 |        0 |    100% |           |
| src/fixgw/plugins/knobster/\_\_init\_\_.py                       |      213 |       46 |       62 |        6 |     79% |53-54, 59-86, 89-92, 95-102, 105-112, 149-\>151, 159-\>161, 170-\>exit, 250-\>254, 256-\>248, 291 |
| src/fixgw/plugins/knobster/protocol.py                           |       57 |        1 |       10 |        1 |     97% |       106 |
| src/fixgw/plugins/mavlink/Mav.py                                 |      323 |        0 |      164 |       30 |     94% |115-\>118, 118-\>125, 125-\>127, 127-\>130, 196-\>202, 202-\>208, 208-\>exit, 225-\>exit, 230-\>exit, 249-\>259, 259-\>exit, 279-\>292, 292-\>exit, 298-\>exit, 397-\>exit, 418-\>exit, 491-\>493, 493-\>503, 500-\>503, 506-\>511, 520-\>exit, 538-\>549, 541-\>538, 549-\>exit, 556-\>exit, 574-\>581, 581-\>584, 589-\>592, 592-\>exit, 597-\>exit |
| src/fixgw/plugins/mavlink/\_\_init\_\_.py                        |       84 |        0 |       12 |        0 |    100% |           |
| src/fixgw/plugins/megasquirt/\_\_init\_\_.py                     |       35 |        0 |        6 |        0 |    100% |           |
| src/fixgw/plugins/megasquirt/megasquirt.py                       |       49 |        0 |       18 |        1 |     99% |   18-\>20 |
| src/fixgw/plugins/megasquirt/tables.py                           |        1 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/mgl/\_\_init\_\_.py                            |       39 |        0 |       18 |        2 |     96% |45-\>47, 49-\>53 |
| src/fixgw/plugins/mgl/rdac.py                                    |      134 |        0 |       64 |        2 |     99% |91-\>100, 169-\>175 |
| src/fixgw/plugins/mgl/tables.py                                  |        1 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/mgl\_serial/\_\_init\_\_.py                    |       89 |        0 |       28 |        0 |    100% |           |
| src/fixgw/plugins/navigator\_adapter/\_\_init\_\_.py             |       64 |       29 |       14 |        0 |     47% |66-97, 100, 120, 123-127 |
| src/fixgw/plugins/navigator\_adapter/adapters/\_\_init\_\_.py    |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/navigator\_adapter/adapters/garmin\_nmea.py    |       80 |        8 |       18 |        7 |     85% |23-24, 48, 68, 73-75, 95, 98-\>100, 102-\>104, 104-\>106, 121-\>123 |
| src/fixgw/plugins/navigator\_adapter/base.py                     |       46 |        2 |        0 |        0 |     96% |    77, 80 |
| src/fixgw/plugins/navigator\_adapter/fix\_map.py                 |       33 |        3 |       24 |        3 |     89% |52, 54, 56 |
| src/fixgw/plugins/netfix/\_\_init\_\_.py                         |      421 |        7 |      156 |        8 |     97% |147-\>149, 259-\>262, 265-\>268, 271-\>274, 277-\>281, 283-285, 338-339, 462-464, 592, 596-\>exit, 597-\>exit |
| src/fixgw/plugins/onspeed/\_\_init\_\_.py                        |      161 |        5 |       44 |        2 |     97% |115-116, 188-189, 194, 252-\>254 |
| src/fixgw/plugins/onspeed/sentence.py                            |       45 |        0 |       14 |        0 |    100% |           |
| src/fixgw/plugins/quorum/\_\_init\_\_.py                         |       52 |        0 |       14 |        0 |    100% |           |
| src/fixgw/plugins/rais/\_\_init\_\_.py                           |       47 |        0 |        8 |        0 |    100% |           |
| src/fixgw/plugins/rpi\_bmp085/Adafruit\_BMP/BMP085.py            |      139 |      139 |       12 |        0 |      0% |    22-215 |
| src/fixgw/plugins/rpi\_bmp085/Adafruit\_BMP/\_\_init\_\_.py      |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/rpi\_bmp085/\_\_init\_\_.py                    |       55 |       55 |        6 |        0 |      0% |    24-116 |
| src/fixgw/plugins/rpi\_bno055/Adafruit\_BNO055/BNO055.py         |      351 |      351 |       48 |        0 |      0% |    23-726 |
| src/fixgw/plugins/rpi\_bno055/Adafruit\_BNO055/\_\_init\_\_.py   |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/rpi\_bno055/\_\_init\_\_.py                    |       79 |       79 |       16 |        0 |      0% |    24-138 |
| src/fixgw/plugins/rpi\_button/\_\_init\_\_.py                    |       51 |       51 |       10 |        0 |      0% |    24-116 |
| src/fixgw/plugins/rpi\_mcp3008/Adafruit\_MCP3008/MCP3008.py      |       33 |       33 |        6 |        0 |      0% |     22-96 |
| src/fixgw/plugins/rpi\_mcp3008/Adafruit\_MCP3008/\_\_init\_\_.py |        1 |        1 |        0 |        0 |      0% |         1 |
| src/fixgw/plugins/rpi\_mcp3008/\_\_init\_\_.py                   |       72 |       72 |        8 |        0 |      0% |    24-178 |
| src/fixgw/plugins/rpi\_rotary\_encoder/\_\_init\_\_.py           |       67 |       14 |       16 |        2 |     76% |109, 116, 123, 135-136, 143-144, 150-155, 160 |
| src/fixgw/plugins/rpi\_virtualwire/\_\_init\_\_.py               |       75 |       75 |       12 |        0 |      0% |    24-139 |
| src/fixgw/plugins/rpi\_virtualwire/virtualwire/\_\_init\_\_.py   |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/plugins/rpi\_virtualwire/virtualwire/virtualwire.py    |      184 |      184 |       78 |        0 |      0% |    14-376 |
| src/fixgw/plugins/rtl\_433/\_\_init\_\_.py                       |      166 |        0 |       80 |        0 |    100% |           |
| src/fixgw/plugins/shadin/\_\_init\_\_.py                         |       73 |        0 |       20 |        0 |    100% |           |
| src/fixgw/plugins/skel.py                                        |       35 |        0 |        6 |        0 |    100% |           |
| src/fixgw/plugins/state\_persist/\_\_init\_\_.py                 |       90 |       40 |       22 |        1 |     47% |73-77, 82-89, 94, 102-103, 106-114, 120-128, 131, 140, 143-147, 150 |
| src/fixgw/plugins/stratux/\_\_init\_\_.py                        |      122 |        1 |       36 |        1 |     99% |       136 |
| src/fixgw/plugins/stratux/gdl90.py                               |       88 |        2 |       32 |        2 |     97% |   75, 140 |
| src/fixgw/plugins/stratux/traffic.py                             |      185 |        3 |       82 |        4 |     97% |139-\>146, 160, 165-\>exit, 169-\>exit, 264-265 |
| src/fixgw/plugins/strom\_pi/\_\_init\_\_.py                      |      116 |        0 |       16 |        3 |     98% |113-\>117, 117-\>131, 118-\>131 |
| src/fixgw/plugins/system.py                                      |       93 |        0 |       48 |        0 |    100% |           |
| src/fixgw/plugins/test.py                                        |       27 |        0 |        2 |        0 |    100% |           |
| src/fixgw/plugins/xplane/\_\_init\_\_.py                         |      256 |       45 |       88 |       11 |     83% |138, 160, 175, 297-298, 328-329, 347-350, 356-357, 360-378, 384-\>388, 395-397, 399, 400-\>402, 403, 405, 413-416, 419-420, 455, 457 |
| src/fixgw/quorum.py                                              |        5 |        0 |        0 |        0 |    100% |           |
| src/fixgw/server.py                                              |      270 |        0 |      110 |        0 |    100% |           |
| src/fixgw/sim/\_\_init\_\_.py                                    |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/sim/\_\_main\_\_.py                                    |       87 |       87 |       16 |        0 |      0% |     4-116 |
| src/fixgw/sim/aircraft.py                                        |       75 |        4 |       12 |        3 |     92% |68-70, 92, 112-\>exit |
| src/fixgw/sim/checks.py                                          |      396 |       28 |      202 |        7 |     91% |85, 253, 298-\>300, 313, 374, 430, 471-\>476, 481-508 |
| src/fixgw/sim/cifp.py                                            |      165 |       15 |       94 |       16 |     88% |37, 46, 54, 57, 123-\>118, 126, 164, 177, 192, 194, 196, 214, 233, 251, 266, 287 |
| src/fixgw/sim/fplroute.py                                        |      144 |       12 |       78 |        5 |     90% |79, 82-89, 128, 146, 214 |
| src/fixgw/sim/generator.py                                       |      220 |        7 |       98 |        6 |     96% |122-123, 239-240, 265, 276, 294-\>296, 312, 314-\>317 |
| src/fixgw/sim/live.py                                            |      119 |        4 |       32 |        4 |     95% |64, 157-158, 172-\>174, 184, 190-\>exit |
| src/fixgw/sim/profile.py                                         |      172 |       14 |      104 |       14 |     90% |70, 82, 91, 100, 111, 143, 146, 166, 181, 187, 189, 199, 208, 210 |
| src/fixgw/sim/refnav.py                                          |      480 |       14 |      174 |       18 |     95% |168-\>175, 208, 227, 231, 233, 427, 466, 495, 505, 510, 528-\>exit, 530-\>exit, 546, 602-\>605, 609-610, 680-\>683, 743, 765 |
| src/fixgw/status.py                                              |       63 |        0 |       14 |        0 |    100% |           |
| src/fixgw/tools/\_\_init\_\_.py                                  |        0 |        0 |        0 |        0 |    100% |           |
| src/fixgw/tools/avionicsduino\_sim.py                            |       68 |       32 |       16 |        2 |     52% |59-\>63, 135-180, 184 |
| src/fixgw/tools/canas\_sim.py                                    |       67 |       41 |       12 |        1 |     37% |74-79, 89-90, 118-170, 174 |
| src/fixgw/tools/dronecan\_sim.py                                 |       95 |        4 |       18 |        2 |     95% |215, 233-234, 241 |
| src/fixgw/tools/export\_param\_schema.py                         |       73 |       16 |       32 |        6 |     77% |93-\>92, 112, 118-\>117, 133-134, 156, 173-185, 189 |
| src/fixgw/version.py                                             |        2 |        0 |        0 |        0 |    100% |           |
| **TOTAL**                                                        | **14625** | **2103** | **4888** |  **278** | **86%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/billmallard/fix-gateway/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/billmallard/fix-gateway/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/billmallard/fix-gateway/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/billmallard/fix-gateway/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fbillmallard%2Ffix-gateway%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/billmallard/fix-gateway/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.