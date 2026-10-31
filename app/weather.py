"""Weather from Open-Meteo (free, no API key). Historical hours are cached in SQLite."""
import time
from datetime import date, datetime, timedelta

import httpx

TZ_NAME = "Europe/Copenhagen"
HOURLY = "temperature_2m,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover,precipitation"
FIELD_MAP = {
    "temperature_2m": "temp", "pressure_msl": "pressure", "wind_speed_10m": "wind_speed",
    "wind_direction_10m": "wind_dir", "wind_gusts_10m": "gusts", "cloud_cover": "cloud", "precipitation": "precip",
}
MARINE_MAP = {"sea_surface_temperature": "sst", "sea_level_height_msl": "sea_level"}
COLS = ["temp", "pressure", "wind_speed", "wind_dir", "gusts", "cloud", "precip", "sst", "sea_level"]

status = {"last_ok": None, "last_error": None, "last_error_at": None, "requests": 0}
_forecast_cache = {}


def grid(lat, lon):
    return round(lat, 2), round(lon, 2)


def _get(url, params):
    status["requests"] += 1
    try:
        r = httpx.get(url, params=params, timeout=20)
        r.raise_for_status()
        status["last_ok"] = datetime.now().isoformat(timespec="seconds")
        return r.json()
    except Exception as e:  # network down, API hiccup - the app keeps working without weather
        status["last_error"] = f"{url.split('/')[2]}: {e}"
        status["last_error_at"] = datetime.now().isoformat(timespec="seconds")
        raise


def _merge(target, payload, mapping):
    hourly = payload.get("hourly", {})
    for i, t in enumerate(hourly.get("time", [])):
        row = target.setdefault(t, {})
        for src, dst in mapping.items():
            vals = hourly.get(src)
            if vals is not None and vals[i] is not None:
                row[dst] = vals[i]


def fetch(lat, lon, start: date = None, end: date = None, past_days=None, forecast_days=None):
    """Return {local_hour_iso: {col: value}} merged from the weather and (best effort) marine APIs."""
    base = {"latitude": lat, "longitude": lon, "timezone": TZ_NAME}
    if start:
        base |= {"start_date": start.isoformat(), "end_date": end.isoformat()}
    else:
        base |= {"past_days": past_days, "forecast_days": forecast_days}

    # The forecast endpoint covers roughly the last 3 months; older dates come from the ERA5 archive.
    old = start is not None and start < date.today() - timedelta(days=80)
    url = "https://archive-api.open-meteo.com/v1/archive" if old else "https://api.open-meteo.com/v1/forecast"
    out = {}
    _merge(out, _get(url, base | {"hourly": HOURLY, "wind_speed_unit": "ms"}), FIELD_MAP)
    try:
        # Sea temperature / water level are coarse for inner fjords and may be null - that's fine.
        _merge(out, _get("https://marine-api.open-meteo.com/v1/marine",
                         base | {"hourly": ",".join(MARINE_MAP), "cell_selection": "sea"}), MARINE_MAP)
    except Exception:
        pass
    return out


def store(conn, lat, lon, data):
    glat, glon = grid(lat, lon)
    conn.executemany(
        f"INSERT OR REPLACE INTO weather(lat, lon, time, {', '.join(COLS)}) VALUES (?,?,?{',?' * len(COLS)})",
        [(glat, glon, t, *[v.get(c) for c in COLS]) for t, v in data.items()],
    )


def ensure_range(conn, lat, lon, start: datetime, end: datetime):
    """Make sure hourly weather exists from 24h before start (for trends) to end. Returns True if complete."""
    glat, glon = grid(lat, lon)
    s = (start - timedelta(hours=24)).replace(minute=0)
    e = min(end, datetime.now())
    expected = int((e - s).total_seconds() // 3600) + 1
    have = conn.execute(
        "SELECT COUNT(*) FROM weather WHERE lat=? AND lon=? AND time BETWEEN ? AND ? AND pressure IS NOT NULL",
        (glat, glon, s.strftime("%Y-%m-%dT%H:00"), e.strftime("%Y-%m-%dT%H:00")),
    ).fetchone()[0]
    if have >= expected:
        return True
    store(conn, lat, lon, fetch(glat, glon, s.date(), e.date()))
    return False


def forecast(lat, lon):
    """Hourly forecast incl. 2 past days (needed for pressure/temperature trends). Cached 1 hour in memory."""
    key = grid(lat, lon)
    hit = _forecast_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    data = fetch(*key, past_days=2, forecast_days=7)
    _forecast_cache[key] = (time.time(), data)
    return data


def load(conn, lat, lon, start: datetime, end: datetime):
    glat, glon = grid(lat, lon)
    cur = conn.execute(
        "SELECT * FROM weather WHERE lat=? AND lon=? AND time BETWEEN ? AND ?",
        (glat, glon, (start - timedelta(hours=25)).strftime("%Y-%m-%dT%H:00"), end.strftime("%Y-%m-%dT%H:00")),
    )
    return {r["time"]: dict(r) for r in cur}
