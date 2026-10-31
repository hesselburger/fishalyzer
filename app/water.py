"""Name the water at a point (e.g. "Tubæk", river) via OpenStreetMap's Nominatim reverse geocoder.

Nominatim's usage policy: max 1 request/second and an identifying User-Agent,
so calls are serialised and results cached in memory.
"""
import threading
import time

import httpx

URL = "https://nominatim.openstreetmap.org/reverse"
KINDS = {
    "river": "river", "stream": "stream", "canal": "canal", "ditch": "ditch", "drain": "drain",
    "bay": "bay", "strait": "strait", "coastline": "coast", "water": "lake", "wetland": "wetland",
}
_cache = {}
_lock = threading.Lock()
_last = [0.0]


def name_at(lat, lon):
    key = (round(lat, 4), round(lon, 4))
    if key in _cache:
        return _cache[key]
    with _lock:
        wait = 1.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        r = httpx.get(URL, timeout=15, headers={"User-Agent": "Fishalyzer/0.2 (hobby fishing log)"}, params={
            "lat": lat, "lon": lon, "format": "jsonv2", "layer": "natural", "zoom": 14,
            "addressdetails": 1, "accept-language": "da,en"})
    r.raise_for_status()
    d = r.json()
    hit = None
    # The "natural" layer can also return woods or hills - only keep water features.
    if d.get("name") and (d.get("category") in ("waterway", "water") or d.get("type") in KINDS):
        a = d.get("address", {})
        hit = {"name": d["name"], "kind": KINDS.get(d.get("type"), d.get("type")),
               "place": a.get("town") or a.get("village") or a.get("city") or a.get("hamlet") or ""}
    _cache[key] = hit
    return hit
