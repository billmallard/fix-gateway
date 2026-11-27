"""YUMA parsing and almanac orbit propagation (RAIM2, AER-6756).

The fixture is the USCG NAVCEN YUMA almanac for GPS week 2345 (mod 1024:
297), toa 405504 s, as archived by CelesTrak
(celestrak.org/GPS/almanac/Yuma/2024/almanac.yuma.week0297.405504.txt).
"""

import datetime as dt
import pathlib

import numpy as np
import pytest

from fixgw.raim import almanac

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "raim"
YUMA_2024_12 = FIXTURE / "2024_almanac.yuma.week0297.405504.txt"
NEAR = dt.datetime(2024, 12, 19, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture(scope="module")
def entries():
    return almanac.parse_yuma(YUMA_2024_12.read_text(), NEAR)


def test_parses_every_record(entries):
    assert len(entries) == 31  # PRN 1 (SVN80, launched JDAY 352) is not in it yet
    first = entries[0]
    assert first.prn == 2
    assert first.week == 2345  # 297 + 2 * 1024
    assert first.toa == 405504.0
    assert first.sqrt_a == pytest.approx(5153.6, abs=1.0)


def test_week_rollover_resolves_to_the_nearest_epoch():
    gps_2024 = almanac.unix_to_gps(NEAR)
    assert almanac.resolve_week(297, gps_2024) == 2345
    gps_2016 = almanac.unix_to_gps(dt.datetime(2016, 8, 19, tzinfo=dt.timezone.utc).timestamp())
    assert almanac.resolve_week(894, gps_2016) == 1918


def test_orbit_radius_is_gps_altitude(entries):
    pos = almanac.satellite_ecef(entries, [entries[0].toa_gps_seconds + 3600])[0]
    radius_km = np.linalg.norm(pos, axis=1) / 1000.0
    assert np.all((radius_km > 25500) & (radius_km < 27700))


def test_two_independent_almanacs_agree_on_positions(entries):
    """A sign or epoch error in the Table 20-IV terms (Earth rotation, toa)
    makes almanacs uploaded on different days disagree by thousands of km;
    correct propagation agrees to the almanac's own few-km precision.
    The second almanac is the same constellation's next-day upload,
    trimmed here to the six satellites the check uses."""
    next_day = almanac.parse_yuma((FIXTURE / "2024_almanac_week0297_503808_six.txt").read_text(), NEAR)
    by_prn = {e.prn: e for e in entries}
    t = [next_day[0].toa_gps_seconds + 86400]
    a = almanac.satellite_ecef([by_prn[e.prn] for e in next_day], t)[0]
    b = almanac.satellite_ecef(next_day, t)[0]
    assert np.max(np.linalg.norm(a - b, axis=1)) < 10_000.0


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda s: s.replace("Eccentricity:", "Eccentricty:", 1), "unknown YUMA field"),
        (lambda s: s.replace("week:", "wek:", 1), "unknown YUMA field"),
        (lambda s: s.replace("ID:                         02", "ID:                         x2", 1), "bad number"),
        (lambda s: s.replace("ID:                         03", "ID:                         02", 1), "twice"),
        (lambda s: "", "no almanac records"),
    ],
)
def test_malformed_files_are_refused(mutation, message):
    with pytest.raises(almanac.AlmanacError, match=message):
        almanac.parse_yuma(mutation(YUMA_2024_12.read_text()), NEAR)


def test_a_truncated_record_is_refused():
    text = YUMA_2024_12.read_text()
    cut = text[: text.index("Af1(s/s)", text.index("PRN-03"))]
    with pytest.raises(almanac.AlmanacError, match="missing"):
        almanac.parse_yuma(cut, NEAR)


def test_almanac_age(entries):
    toa_unix = entries[0].toa_gps_seconds + almanac.GPS_EPOCH_UNIX - almanac.GPS_UTC_LEAP_SECONDS
    assert almanac.almanac_age_days(entries, toa_unix + 3 * 86400) == pytest.approx(3.0)
