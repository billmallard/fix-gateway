# CERT14 bench evidence: a second host writes attitude through netfix (AER-5466)

All runs on 2026-11-12. The second host was the agent container on the NAS,
address 172.29.8.2, which is not on either bench's subnet (10.110.10.0/24).

## Before: fix-gateway `dev` e234cb8, netfix on `0.0.0.0:3490`

**Pi 5 (10.110.10.199), the hardware bench.** `python3 remote_write.py 10.110.10.199 pi_before`
under the bench lock (`flock -w 900 /tmp/pyefis-bench.lock`, provenance in
`pi_before_provenance.txt`). The script sends `PITCH;15.0`, a value sentence with its
flags omitted, four times a second for 6 s, from a second connection reads what a
subscriber receives, and reads the key back. Result (`pi_before.json`):

- `@rPITCH;0.0;01000` before (old, no attitude source on the rig)
- a subscriber received `PITCH;15.0;00000` (every flag clear, valid)
- `@rPITCH;15.0;00000` during

pyEfis on the Pi is a netfix client over loopback (`ss` in the provenance: pid 856849
to 127.0.0.1:3490), so it received the same sentence. The Pi has no screenshot of any
kind (eglfs, no root for a KMS grab, AER-2002), so the on-glass record is from the
Beelink.

**Beelink (10.110.10.241), the simulator bench.** Same code path, captured with
`ffmpeg -f x11grab` under the bench lock.

- `beelink_before_blank.png`: before any write. The attitude indicator is blank.
- `beelink_pitch_only.png`: during `remote_write.py 10.110.10.241 beelink_before`
  (PITCH only). The AI appears at 15 deg nose-up. The sky is grey because pyEfis greys
  the AI when any of its inputs (ROLL, ALAT, TAS) is old, and on this bench those were.
- `beelink_full_attitude.png`: during `remote_write_all.py 10.110.10.241`, which writes
  PITCH 15, ROLL 20, ALAT 0 and TAS 120 the same way, as a live AHRS would. The AI
  shows a full-colour attitude, 15 deg up and 20 deg right bank, from the second host.
  The readback in `beelink_all_write.txt` shows ROLL and ALAT old only because it runs
  about 0.35 s after the last write and their tol is 300 ms; PITCH has the same tol and
  read first.

After each run the keys went old again within one tol. The grabs taken 14 s and
40 s later were byte-identical to `beelink_pitch_only.png`: pyEfis keeps drawing the
last attitude, greyed, when its inputs are old. That is existing pyEfis behaviour and
not part of this change. `PITCH;0.0` and `ROLL;0.0` were written afterwards on both
benches, and `TAS;0.0` on the Beelink, to put the values back.

## After: this branch, netfix on `127.0.0.1:3490`

Staged on the Pi with
`bench-deploy.sh --bench pi5 --ref fix-gateway=aer-5466/netfix-loopback --owner AVIONICS --issue AER-5466`.
The deploy refreshed `connections/netfix.yaml` (the bench copy was unedited) and
restarted fixgw. Then `python3 remote_write.py 10.110.10.199 pi_after` under the lock,
with `pi_prov.sh` running on the box (`pi_after_provenance.txt`):

- `pi_after.json`: `ConnectionRefusedError(111)` at connect. Nothing was written.
- the listener is `127.0.0.1:3490`; pyEfis (pid 890937) is still connected over
  loopback; a loopback read on the box returns `@rPITCH;0.0;01000`, unchanged.

The Pi was restored to `dev` right after (`--restore=fix-gateway`).
