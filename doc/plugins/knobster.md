# Knobster Plugin

Drives FIX keys from a Sim Innovations Knobster V2, a dual concentric encoder
with a push button on USB (`16d0:0e8a`). It talks to the device directly over
libusb. No Sim Innovations software is needed, and it runs on Linux.

On Linux no kernel driver binds to the Knobster, so it produces no keyboard,
evdev or hidraw events. It stays silent until a host sends its startup sequence
and keeps re-sending a config cycle about every 0.9 s. This plugin does both.
The protocol was recovered from the vendor library's USB traffic. The capture and
notes are in maos-workspace `makerplane/captures/knobster/`.

## Install

1. pyusb and libusb: `pip install pyusb` (or `pip install fixgw[knobster]`),
   plus the system `libusb-1.0-0` package.
2. Access without sudo: install `extras/udev/70-knobster.rules`, as its header
   describes, then replug the Knobster. The fixgw user must be in `plugdev`.
   Without the rule the plugin logs `permission denied opening the Knobster`
   and keeps retrying.
3. Enable it in `preferences.yaml.custom`:

   ```yaml
   enabled:
     KNOBSTER: true
   ```

## What it writes

| Action | Default key | Value written |
|---|---|---|
| Inner ring, one detent | `ENC3` | `+inner_step` clockwise, `-inner_step` counter-clockwise |
| Outer ring, one detent | `ENC4` | `+outer_step` / `-outer_step` |
| Button press / release | `BTN3` | `True` / `False` |
| Long press (optional) | `long_press_key` | `True` then `False`, on release |

Encoder writes are deltas: each write is the change, not a position. That is
how pyEfis reads its `encoder` key (`hmi/encoder_input.yaml`, `ENC3` and
`BTN3` by default). So the inner ring and button drive pyEfis with no pyEfis
change. pyEfis uses one encoder today, so the outer ring goes to its own key by
default. Set `outer_key: ENC3` with a larger `outer_step` for a coarse step on
the same key, or set `outer_key: ""` to ignore the outer ring.

The device has no long-press event. With `long_press_key` set, a press held
at least `long_press_time` seconds pulses that key on release. The press
itself has already gone to `button_key` by then.

## Unplug and replug

If the device goes away the plugin releases a held button (writes `BTN3
False`), closes it, and looks for it again every `reconnect_interval`
seconds. When it comes back the plugin runs the full startup again. `fixgwc
status` shows `Connected`, `Initialized`, a count of connections, events, and
the last error.

## Options

| Option | Default | |
|---|---|---|
| `inner_key`, `inner_step` | `ENC3`, `1` | inner (minor) ring |
| `outer_key`, `outer_step` | `ENC4`, `1` | outer (major) ring; empty key ignores it |
| `button_key` | `BTN3` | |
| `long_press_key`, `long_press_time` | unset, `1.0` | |
| `reconnect_interval` | `2.0` | seconds between searches for the device |
| `config_interval` | `0.9` | seconds between config cycles |
| `hello_retry`, `hello_attempts` | `1.0`, `3` | if the device never answers hello, it is initialized anyway |

Only one Knobster is supported, and it is the first one found.
