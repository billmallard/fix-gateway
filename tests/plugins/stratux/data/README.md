# FIS-B test data

## `dump978-sample-2015.txt.gz`

dump978's `sample-data.txt.gz`, unchanged: about 1,100 demodulated UAT
messages from RF captures, 704 of them ground uplinks (lines starting `+`,
the 432-byte uplink payload in hex). The uplinks are Bay Area FIS-B ground
stations, 23 to 25 January 2015.

- Source: https://github.com/mutability/dump978, file `sample-data.txt.gz`,
  at commit `c7c4ad2f953692eadc5702aa8f059ea93fba2934`.
- Licence: GPL-2.0-or-later (dump978), compatible with this repository.

## `fisb_oracle.json`

What fisb-decode, an independent decoder written against DO-358B, makes of
every text product in that file: product 413 text, TWGO text records, and the
one segmented NOTAM-TFR joined by its Unsegmenter. `test_fisb.py` requires
fixgw's decoder to agree with it on all 704 uplinks.

To regenerate:

```sh
git clone https://github.com/rand-projects/fisb-decode
git -C fisb-decode checkout c3d3b0473429336b1a98672b2e8b276c24d49765
python3 make_fisb_oracle.py fisb-decode dump978-sample-2015.txt.gz > fisb_oracle.json
```

fisb-decode is BSD-2-Clause Plus Patent and is not a dependency; only its
output is committed.
