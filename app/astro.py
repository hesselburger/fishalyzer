"""Sun and moon maths, computed locally so no extra API is needed.

Accuracy is a few minutes for the sun and ~1 hour for moon transit, which is
fine for bucketing conditions into "dawn", "solunar major period" etc.
"""
import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")
SYNODIC = 29.530588853
KNOWN_NEW_MOON_JD = 2451550.1  # 2000-01-06 18:14 UTC


def _jd_to_local(jd):
    return datetime.fromtimestamp((jd - 2440587.5) * 86400, tz=timezone.utc).astimezone(TZ).replace(tzinfo=None)


@lru_cache(maxsize=4096)
def sun_times(d: date, lat: float, lon: float):
    """Return (sunrise, solar_noon, sunset) as naive local datetimes (sunrise/sunset None in polar cases)."""
    jd_noon = 2440587.5 + datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp() / 86400
    n = round(jd_noon - 2451545.0 + 0.0008)
    j_star = n - lon / 360
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    decl = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(decl)) / (math.cos(phi) * math.cos(decl))
    noon = _jd_to_local(j_transit)
    if abs(cos_w) > 1:
        return None, noon, None
    w = math.degrees(math.acos(cos_w))
    return _jd_to_local(j_transit - w / 360), noon, _jd_to_local(j_transit + w / 360)


def moon_fraction(dt: datetime) -> float:
    """Phase as fraction of the synodic month: 0 = new, 0.5 = full."""
    jd = 2440587.5 + dt.replace(tzinfo=TZ).timestamp() / 86400
    return ((jd - KNOWN_NEW_MOON_JD) / SYNODIC) % 1.0


def moon_illumination(dt: datetime) -> float:
    return (1 - math.cos(2 * math.pi * moon_fraction(dt))) / 2


def solunar_distance(dt: datetime, lat: float, lon: float):
    """Hours to the nearest major (moon over/underfoot) and minor (approx. moonrise/set) period."""
    _, noon, _ = sun_times(dt.date(), lat, lon)
    # The moon falls behind the sun by ~24h per synodic month, so transit ~= solar noon + f*24h.
    transit = noon + timedelta(hours=moon_fraction(dt) * 24)
    hours = (dt - transit).total_seconds() / 3600

    def dist(period_offset):
        x = (hours - period_offset) % 12.42  # majors repeat every half lunar day
        return min(x, 12.42 - x)

    return dist(0), dist(6.21)
