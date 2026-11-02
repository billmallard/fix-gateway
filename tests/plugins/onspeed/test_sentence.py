"""ONSPEED sentence decoder: pyEfis docs/fixgw_onspeed_in_spec.md section 7.1.

The V1-V5 vectors are the spec's, generated independently of this module
from the firmware's sprintf; they anchor the decoder to the wire format so
that encode() and parse() cannot pass by sharing a mistake.
"""

import random

import pytest

from fixgw.plugins.onspeed import sentence
from fixgw.plugins.onspeed.sentence import FIELDS, SentenceError, encode, parse

V1 = b"#1+052-01230854+04500+0031+03+1162+098-005+00-006+00+135+112+094+060+000+0035C"
V2 = b"#1-008+00000250+00812+0000+00+1000+000+000+00+000+20+135+112+094+060+000+0001E"
V3 = b"#1+052-01230854+04500+0031+03+1162+098-005+00-006+00+135+112+094+060+000+01005A"
V4 = b"#1+052-01230854+04500+0031-120+1162+098-005+00-006+00+135+112+094+060+000+0035B"
V5 = b"#1+052-01230854+04500+0031+03+1162+098-005+00-006+00+000+000+000+000+000+0033C"

# V1's raw integers in FIRMWARE_FORMAT order (FIELDS, then DataMark).
V1_RAW = [52, -123, 854, 4500, 31, 3, 11, 62, 98, -5, 0, -6, 0, 135, 112, 94, 60, 0, 0, 3]
SPIN_CUE = len(FIELDS) - 1  # the last field before DataMark


def _with_checksum(body):
    """Append the firmware checksum (over bytes 0-75) to a hand-built body."""
    return body + b"%02X" % sentence.checksum(body)


def test_firmware_format_is_the_firmware_string():
    # DisplaySerial.ino @ be1481c, ONSPEED branch, copied by hand from the
    # source.  If this fails, someone edited the format; the spec pins it.
    assert sentence.FIRMWARE_FORMAT == (
        "#1%+04i%+05i%04u%+06i%+05i%+03i%+03i%02u%+04i%+04i%+03i%+04i%+03i"
        "%+04i%+04i%+04i%+04i%+04i%+02i%02u"
    )


def test_offsets_tile_the_76_byte_body():
    # Spec 3.3: the offsets come from the sprintf and must sum to the
    # checksum loop bound.  Each field starts where the previous one ended.
    end = 2
    for _name, offset, width, _signed, _scale in FIELDS:
        assert offset == end
        end = offset + width
    assert end == sentence.FIXED_SPAN
    assert end + 2 == sentence.CHECKSUM_SPAN


@pytest.mark.parametrize("name,vector", [("V1", V1), ("V2", V2), ("V3", V3), ("V4", V4), ("V5", V5)])
def test_encode_reproduces_the_spec_vector_checksums(name, vector):
    body = vector[:-2]
    assert b"%02X" % sentence.checksum(body) == vector[-2:]


def test_encode_reproduces_v1():
    assert encode(V1_RAW).encode() == V1


# 7.1.1
def test_v1_decodes_every_field():
    f = parse(V1 + b"\r\n")
    assert f.pitch == 5.2
    assert f.roll == -12.3
    assert f.ias == 85.4
    assert f.palt == 4500
    assert f.yaw_rate == 3.1
    assert f.lateral_g == 0.03
    assert f.vertical_g == 1.1
    assert f.percent_lift == 62
    assert f.aoa == 9.8
    assert f.vsi == -50
    assert f.oat == 0
    assert f.flight_path == -0.6
    assert f.flaps == 0
    assert f.stall_warn_aoa == 13.5
    assert f.onspeed_slow_aoa == 11.2
    assert f.onspeed_fast_aoa == 9.4
    assert f.tones_on_aoa == 6.0
    assert f.g_onset_rate == 0.0
    assert f.spin_recovery_cue == 0
    assert f.datamark == 3
    assert f.datamark_overflow is False


def test_v2_taxi_frame():
    f = parse(V2)
    assert (f.pitch, f.ias, f.aoa, f.percent_lift, f.flaps) == (-0.8, 25.0, 0.0, 0, 20)


# 7.1.2
def test_golden_offsets_every_field_lands_in_its_own_slot():
    # A distinct value per field, so a value read from a neighbour's slot
    # cannot match by accident.  DataMark last.
    raws = [101, -2202, 3303, -40404, 5050, -66, 77, 88, -919, 121, -13, 141,
            -15, 161, -171, 181, -191, 202, -1, 42]
    assert len(set(abs(r) for r in raws)) == len(raws)
    frame = parse(encode(raws).encode())
    for raw, (name, _o, _w, _signed, scale) in zip(raws, FIELDS):
        expected = raw * scale if isinstance(scale, int) else round(raw * scale, 2)
        assert getattr(frame, name) == expected, name
    assert frame.datamark == 42


# 7.1.3
def test_flipped_body_byte_is_a_checksum_error():
    for i in range(2, 76):
        corrupt = bytearray(V1)
        corrupt[i] = ord("7") if corrupt[i] != ord("7") else ord("8")
        with pytest.raises(SentenceError) as err:
            parse(bytes(corrupt))
        assert err.value.reason == "checksum", i


def test_lowercase_checksum_is_accepted():
    assert V1.endswith(b"5C")
    assert parse(V1[:-2] + b"5c").aoa == 9.8


def test_non_hex_checksum_is_a_checksum_error():
    with pytest.raises(SentenceError) as err:
        parse(V1[:-2] + b"ZZ")
    assert err.value.reason == "checksum"


# 7.1.4
def test_v3_datamark_overflow_decodes():
    assert len(V3) == 79
    f = parse(V3)
    assert f.datamark == 100
    assert f.datamark_overflow is True
    assert f.aoa == 9.8 and f.stall_warn_aoa == 13.5
    # The checksum covers bytes 0-75 only: the overflowed DataMark's third
    # digit is outside it.
    assert sum(V3[:76]) & 0xFF == int(V3[-2:], 16)
    assert sum(V3[:77]) & 0xFF != int(V3[-2:], 16)


def test_large_datamark_overflow_decodes():
    raw = list(V1_RAW)
    raw[-1] = 987
    assert parse(encode(raw).encode()).datamark == 987


def test_datamark_with_a_leading_zero_past_two_digits_is_rejected():
    body = V1[:-2]
    with pytest.raises(SentenceError) as err:
        parse(_with_checksum(body[:74] + b"035"))
    assert err.value.reason == "grammar"


# 7.1.5
def test_v4_lateral_g_overflow_is_rejected_whole():
    with pytest.raises(SentenceError) as err:
        parse(V4)
    assert err.value.reason == "grammar"


def _overflow_value(i, rng=None):
    """A value one digit too wide for field i, as the firmware would print it."""
    _name, _o, width, signed, _s = FIELDS[i]
    digits = width - 1 if signed else width
    if rng is None:
        magnitude = 10**digits  # the first value that overflows
    else:
        magnitude = rng.randrange(10**digits, 10 ** (digits + 1))
    if signed and rng is not None and rng.random() < 0.5:
        return -magnitude
    return magnitude


@pytest.mark.parametrize("i", range(len(FIELDS)), ids=[f[0] for f in FIELDS])
def test_first_overflow_of_any_field_before_datamark_is_rejected(i):
    raw = list(V1_RAW)
    raw[i] = _overflow_value(i)
    line = encode(raw).encode()
    assert len(line) == 79
    with pytest.raises(SentenceError) as err:
        parse(line)
    assert err.value.reason == "grammar"


@pytest.mark.parametrize("i", range(SPIN_CUE), ids=[f[0] for f in FIELDS[:SPIN_CUE]])
def test_any_one_digit_overflow_upstream_of_the_spin_cue_is_rejected(i):
    # Spec 3.4/7.1.5: rule 4 is safe only if no single-field overflow
    # upstream of DataMark still matches the 74-byte grammar.
    rng = random.Random(i)
    for _ in range(2000):
        raw = list(V1_RAW)
        raw[i] = _overflow_value(i, rng)
        raw[-1] = rng.randrange(0, 1000)
        with pytest.raises(SentenceError):
            parse(encode(raw).encode())


def test_spin_cue_overflow_is_the_one_shift_rule_4_cannot_see():
    # A spin cue of +12 (its domain is -1/0/+1, and this firmware hardcodes
    # 0) prints '+12' and pushes '2' into DataMark, which then has no leading
    # zero.  The grammar cannot tell this from cue +1 with DataMark 2xx.  It
    # is harmless to what the plugin publishes: AOA and the setpoints sit at
    # fixed offsets before the shift.  Pinned here so it is a known
    # limitation, not a surprise.
    raw = list(V1_RAW)
    raw[SPIN_CUE] = 12
    raw[-1] = 34
    f = parse(encode(raw).encode())
    assert (f.spin_recovery_cue, f.datamark) == (1, 234)
    assert (f.aoa, f.stall_warn_aoa) == (9.8, 13.5)


def test_unsigned_ias_from_a_negative_value_is_rejected():
    # C's unsigned(-50) prints 4294967246: ten digits in a four-digit slot.
    body = V1[:11] + b"4294967246" + V1[15:-2]
    with pytest.raises(SentenceError):
        parse(_with_checksum(body))


# 7.1.6
def test_truncated_v1_at_every_length_raises_only_sentence_error():
    for n in range(len(V1)):
        with pytest.raises(SentenceError):
            parse(V1[:n])


# 7.1.7
def test_g3x_line_is_wrong_format():
    g3x = b"=1100000000+052-0123___0854+04500____+03+11" + b"62" + b"_" * 10 + b"A1"
    with pytest.raises(SentenceError) as err:
        parse(g3x)
    assert err.value.reason == "wrong_format"


def test_other_sentence_id_is_grammar():
    with pytest.raises(SentenceError) as err:
        parse(_with_checksum(b"#2" + V1[2:-2]))
    assert err.value.reason == "grammar"


# 7.1.8
def test_non_ascii_byte_is_encoding():
    bad = bytearray(V1)
    bad[40] = 0xB0
    with pytest.raises(SentenceError) as err:
        parse(bytes(bad))
    assert err.value.reason == "encoding"


# 7.1.9 (hypothesis is not a dev dependency: boundary table instead)
def _boundaries(width, signed):
    if signed:
        top = 10 ** (width - 1) - 1
        return [-top, -1, 0, 1, top]
    return [0, 1, 10**width - 1]


@pytest.mark.parametrize("i", range(len(FIELDS)), ids=[f[0] for f in FIELDS])
def test_boundary_values_round_trip(i):
    name, _o, width, signed, scale = FIELDS[i]
    for value in _boundaries(width, signed):
        raw = list(V1_RAW)
        raw[i] = value
        line = encode(raw).encode()
        assert len(line) == 78
        expected = value * scale if isinstance(scale, int) else round(value * scale, 2)
        assert getattr(parse(line), name) == expected, (name, value)
