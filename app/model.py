"""Fishalyzer's prediction model - learned from your own logbook.

Idea in one paragraph
---------------------
Every logged trip is split into hours. For each hour we know the *effort*
(hours on the water - the group fishes the same water together, so the number
of anglers doesn't multiply it), how many fish were caught, and
the conditions (pressure trend, wind, light, moon, ...), each sorted into a bin.
For every bin we compute a catch rate and compare it to your overall catch
rate, giving a multiplier ("falling pressure: x1.6"). Bins with little effort
are pulled towards a weak "angler folklore" prior so a single lucky trip does
not dominate; as you log more hours your own data takes over. A forecast hour's
score is the (damped) product of the multipliers of the bins it falls in.

Trips are logged afterwards with a fish count rather than a time per fish,
so a trip's catch is spread over its hours in proportion to effort. Short,
precisely timed trips therefore teach the time-of-day features the most.

Blank trips matter as much as catches: hours fished without a bite are what
make "bad" conditions visible.
"""
import math
from datetime import datetime, timedelta

from . import astro, weather

# Multipliers are combined as exp(DAMP * sum(log m)). Conditions are correlated
# (e.g. falling pressure & clouds & wind), so the full product would overcount.
DAMP = 0.6
DEFAULT_PRIOR_HOURS = 8.0
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# key: (label, [(bin, label, folklore prior multiplier)])
FEATURES = {
    "pressure_trend": ("Pressure trend (3h)", [
        ("falling_fast", "Falling fast", 1.2), ("falling", "Falling", 1.3), ("steady", "Steady", 1.0),
        ("rising", "Rising", 0.85), ("rising_fast", "Rising fast", 0.75)]),
    "pressure_level": ("Air pressure", [
        ("low", "Low < 1005", 1.05), ("normal", "Normal", 1.0), ("high", "High > 1020", 0.95)]),
    "wind": ("Wind speed", [
        ("calm", "Calm < 2 m/s", 0.85), ("light", "Light 2-5", 1.1), ("moderate", "Moderate 5-9", 1.05),
        ("strong", "Strong > 9", 0.8)]),
    "wind_dir": ("Wind direction", [(s, s, 1.0) for s in SECTORS]),
    "temp_trend": ("Air temp vs 24h ago", [
        ("cooling", "Cooling > 3°", 0.9), ("stable", "Stable", 1.05), ("warming", "Warming > 3°", 1.0)]),
    "cloud": ("Cloud cover", [("clear", "Clear", 0.9), ("partly", "Partly cloudy", 1.05), ("overcast", "Overcast", 1.1)]),
    "precip": ("Precipitation", [("dry", "Dry", 1.0), ("light", "Light rain", 1.1), ("rain", "Rain > 1 mm/h", 0.9)]),
    "light": ("Light", [("dawn", "Dawn ±1h", 1.4), ("day", "Day", 0.9), ("dusk", "Dusk ±1h", 1.5), ("night", "Night", 0.5)]),
    "moon_phase": ("Moon phase", [("new", "New", 1.0), ("waxing", "Waxing", 1.0), ("full", "Full", 1.0), ("waning", "Waning", 1.0)]),
    "solunar": ("Solunar period", [("major", "Major", 1.15), ("minor", "Minor", 1.05), ("none", "None", 0.95)]),
    "season": ("Season", [("winter", "Winter", 0.9), ("spring", "Spring", 1.1), ("summer", "Summer", 0.85), ("autumn", "Autumn", 1.2)]),
    "water_temp": ("Sea surface temp", [
        ("lt4", "< 4°", 0.8), ("4_8", "4-8°", 1.0), ("8_12", "8-12°", 1.1), ("12_16", "12-16°", 1.1),
        ("16_20", "16-20°", 1.0), ("gt20", "> 20°", 0.85)]),
    "water_trend": ("Water level (3h)", [("falling", "Falling", 1.05), ("steady", "Steady", 0.95), ("rising", "Rising", 1.05)]),
}

# Plain-language explanations shown in the app. key: (short tag prefix, what it is, {bin: what that bin means})
GLOSSARY = {
    "pressure_trend": ("Pressure", "How much the air pressure changed over the last 3 hours. Anglers believe fish feed "
                       "as a front moves in (falling) and go quiet just after it passes (rising).", {
        "falling_fast": "Dropped more than 2 hPa in 3 hours - a low or front is arriving.",
        "falling": "Dropped 0.7-2 hPa in 3 hours - weather starting to change.",
        "steady": "Changed less than 0.7 hPa in 3 hours - settled, stable weather.",
        "rising": "Rose 0.7-2 hPa in 3 hours - weather clearing up.",
        "rising_fast": "Rose more than 2 hPa in 3 hours - usually right after a front has passed."}),
    "pressure_level": ("Air pressure", "The air pressure itself (at sea level). Normal is around 1013 hPa.", {
        "low": "Below 1005 hPa - unsettled, windy or rainy weather.",
        "normal": "1005-1020 hPa.",
        "high": "Above 1020 hPa - calm, often bright and clear weather."}),
    "wind": ("Wind", "Wind speed. Some wind ripples the surface, dims the light and pushes food along the shore; "
             "too much makes fishing hard.", {
        "calm": "Under 2 m/s - flat, clear water.", "light": "2-5 m/s - a light ripple.",
        "moderate": "5-9 m/s - choppy.", "strong": "Over 9 m/s - rough."}),
    "wind_dir": ("Wind from", "The direction the wind blows FROM. Wind blowing towards your shore pushes warmer "
                 "surface water and food in. Which direction is good depends on the spot - the model learns it.", {}),
    "temp_trend": ("Air temp", "Air temperature now compared with the same time yesterday. Sudden cold snaps tend to slow fish down.", {
        "cooling": "More than 3° colder than yesterday.", "stable": "Within 3° of yesterday.",
        "warming": "More than 3° warmer than yesterday."}),
    "cloud": ("Sky", "Cloud cover. Clouds dim the light, which makes perch hunt more boldly in shallow water.", {
        "clear": "Less than 25% cloud.", "partly": "25-75% cloud.", "overcast": "More than 75% cloud."}),
    "precip": ("Rain", "Rain in that hour.", {
        "dry": "No rain.", "light": "Under 1 mm per hour - drizzle or light rain.", "rain": "1 mm per hour or more."}),
    "light": ("Light", "Time of day by daylight. Perch typically hunt hardest in low light.", {
        "dawn": "Within an hour of sunrise.", "day": "Daylight, outside dawn and dusk.",
        "dusk": "Within an hour of sunset.", "night": "After dark."}),
    "moon_phase": ("Moon", "Phase of the moon. Some anglers swear by full or new moon - no assumption is built in, "
                   "your logs decide.", {
        "new": "New moon - dark nights.", "waxing": "Moon growing towards full.",
        "full": "Full moon - bright nights.", "waning": "Moon shrinking towards new."}),
    "solunar": ("Solunar", "An old angler theory (the 'solunar tables'): fish feed more when the moon is directly "
                "overhead or underfoot, and a little more around moonrise and moonset. The times are rough "
                "estimates - your logs will show whether it holds for you.", {
        "major": "Major period: within about an hour of the moon being directly overhead or underfoot.",
        "minor": "Minor period: within about 45 minutes of moonrise or moonset.",
        "none": "Outside both periods."}),
    "season": ("Season", "Time of year. Big brackish perch often gather near inlets in autumn and winter.", {
        "winter": "December-February.", "spring": "March-May.", "summer": "June-August.", "autumn": "September-November."}),
    "water_temp": ("Sea temp", "Sea surface temperature from a sea model. For inner fjords and streams it's an estimate "
                   "from the nearest open water.", {}),
    "water_trend": ("Water level", "Whether the sea level rose or fell over the last 3 hours (tide plus wind pushing water "
                    "in or out). Moving water at an inlet can trigger feeding.", {
        "falling": "Dropped more than 3 cm in 3 hours.", "steady": "Changed less than 3 cm.",
        "rising": "Rose more than 3 cm in 3 hours."}),
}


def glossary():
    return [{"key": k, "label": FEATURES[k][0], "short": s, "desc": d,
             "bins": [{"key": b, "label": bl, "help": h.get(b, "")} for b, bl, _ in FEATURES[k][1]]}
            for k, (s, d, h) in GLOSSARY.items()]


def _cut(x, edges, labels):
    for edge, label in zip(edges, labels):
        if x < edge:
            return label
    return labels[-1]


def features(t: datetime, wx: dict, lat: float, lon: float):
    """Bin the conditions for the hour starting at t. wx maps 'YYYY-MM-DDTHH:00' -> weather row."""
    key = lambda dt: dt.strftime("%Y-%m-%dT%H:00")
    now, h3, h24 = wx.get(key(t)), wx.get(key(t - timedelta(hours=3))), wx.get(key(t - timedelta(hours=24)))
    if not now or now.get("pressure") is None:
        return None
    mid = t + timedelta(minutes=30)
    f = {}
    if h3 and h3.get("pressure") is not None:
        f["pressure_trend"] = _cut(now["pressure"] - h3["pressure"], [-2, -0.7, 0.7, 2],
                                   ["falling_fast", "falling", "steady", "rising", "rising_fast"])
    f["pressure_level"] = _cut(now["pressure"], [1005, 1020], ["low", "normal", "high"])
    if now.get("wind_speed") is not None:
        f["wind"] = _cut(now["wind_speed"], [2, 5, 9], ["calm", "light", "moderate", "strong"])
    if now.get("wind_dir") is not None:
        f["wind_dir"] = SECTORS[int(((now["wind_dir"] + 22.5) % 360) // 45)]
    if h24 and h24.get("temp") is not None and now.get("temp") is not None:
        f["temp_trend"] = _cut(now["temp"] - h24["temp"], [-3, 3], ["cooling", "stable", "warming"])
    if now.get("cloud") is not None:
        f["cloud"] = _cut(now["cloud"], [25, 75], ["clear", "partly", "overcast"])
    if now.get("precip") is not None:
        f["precip"] = _cut(now["precip"], [0.1, 1], ["dry", "light", "rain"])

    rise, _, sset = astro.sun_times(mid.date(), lat, lon)
    if rise and sset:
        if abs((mid - rise).total_seconds()) <= 3600:
            f["light"] = "dawn"
        elif abs((mid - sset).total_seconds()) <= 3600:
            f["light"] = "dusk"
        else:
            f["light"] = "day" if rise < mid < sset else "night"
    ph = astro.moon_fraction(mid)
    f["moon_phase"] = "new" if ph < 0.0625 or ph >= 0.9375 else "full" if 0.4375 <= ph < 0.5625 else "waxing" if ph < 0.5 else "waning"
    major, minor = astro.solunar_distance(mid, lat, lon)
    f["solunar"] = "major" if major <= 1 else "minor" if minor <= 0.75 else "none"
    f["season"] = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer",
                   "autumn", "autumn", "autumn", "winter"][t.month - 1]
    if now.get("sst") is not None:
        f["water_temp"] = _cut(now["sst"], [4, 8, 12, 16, 20], ["lt4", "4_8", "8_12", "12_16", "16_20", "gt20"])
    if now.get("sea_level") is not None and h3 and h3.get("sea_level") is not None:
        f["water_trend"] = _cut(now["sea_level"] - h3["sea_level"], [-0.03, 0.03], ["falling", "steady", "rising"])
    return f


def _parse(s):
    return datetime.fromisoformat(s)


def dataset(conn, species=None, min_length=None):
    """Return (hours, skipped_hours) where hours = [(features, hours_fished, catches)]."""
    trips = conn.execute(
        'SELECT t.id, t.start, t."end", t.anglers, s.lat, s.lon FROM trips t JOIN spots s ON s.id = t.spot_id').fetchall()
    # Total counts per trip; with a length filter only the measured top fish can qualify.
    if min_length:
        q, args = "SELECT trip_id, COUNT(*) FROM catches WHERE length_cm >= ?", [min_length]
    else:
        q, args = "SELECT trip_id, SUM(count) FROM trip_counts WHERE 1=1", []
    if species:
        q += " AND lower(species) = lower(?)"
        args.append(species)
    caught_by_trip = dict(conn.execute(q + " GROUP BY trip_id", args).fetchall())

    hours, skipped = [], 0
    for t in trips:
        start = _parse(t["start"])
        end = _parse(t["end"]) if t["end"] else datetime.now()
        if end <= start:
            continue
        wx = weather.load(conn, t["lat"], t["lon"], start, end)
        slots = []
        h = start.replace(minute=0, second=0, microsecond=0)
        while h < end:
            overlap = (min(end, h + timedelta(hours=1)) - max(start, h)).total_seconds() / 3600
            slots.append((h, overlap))
            h += timedelta(hours=1)
        total_effort = sum(e for _, e in slots)
        total_caught = caught_by_trip.get(t["id"]) or 0
        for h, effort in slots:
            f = features(h, wx, t["lat"], t["lon"])
            if f is None:
                skipped += 1
                continue
            hours.append((f, effort, total_caught * effort / total_effort))
    return hours, skipped


def fit(hours, prior_hours=DEFAULT_PRIOR_HOURS):
    total_e = sum(e for _, e, _ in hours)
    total_c = sum(c for _, _, c in hours)
    r0 = total_c / total_e if total_e > 0 and total_c > 0 else None
    model = {"r0": r0, "effort": total_e, "catches": round(total_c, 1), "hours": len(hours), "features": {}}
    for key, (label, bins) in FEATURES.items():
        stats = {b: [0.0, 0] for b, _, _ in bins}
        for f, e, c in hours:
            if key in f:
                stats[f[key]][0] += e
                stats[f[key]][1] += c
        out = {}
        for b, blabel, prior in bins:
            e, c = stats[b]
            if not r0:
                m = prior
            elif e + prior_hours == 0:
                m = 1.0  # folklore switched off and never fished in this condition: no opinion either way
            else:
                # Floor at -95%: a few blank hours shouldn't make a condition count as "never".
                m = max(0.05, ((c + prior_hours * r0 * prior) / (e + prior_hours)) / r0)
            out[b] = {"label": blabel, "effort": round(e, 1), "catches": round(c, 1), "prior": prior, "m": m,
                      "rate": round(c / e, 3) if e else None,
                      # with no fish logged at all, every number is still pure folklore
                      "data_weight": 0.0 if not r0 else round(e / (e + prior_hours), 2) if prior_hours or e else 1.0}
        # Keep the effort-weighted average multiplier at 1 so a feature can't inflate every score.
        fe = sum(v["effort"] for v in out.values())
        if fe > 0:
            norm = sum(v["effort"] * v["m"] for v in out.values()) / fe
            for v in out.values():
                v["m"] /= norm
        imp = 0.0
        if fe > 0:
            imp = math.sqrt(sum(v["effort"] * math.log(v["m"]) ** 2 for v in out.values()) / fe)
        for v in out.values():
            v["m"] = round(v["m"], 3)
        short, desc, helps = GLOSSARY[key]
        for b, v in out.items():
            v["help"] = helps.get(b, "")
        model["features"][key] = {"label": label, "short": short, "desc": desc, "importance": round(imp, 3), "bins": out}
    return model


def score(model, f):
    """Return (score 0-100, multiplier vs. average, top reasons)."""
    contrib = []
    for key, b in f.items():
        m = model["features"][key]["bins"][b]["m"]
        contrib.append((DAMP * math.log(max(m, 1e-3)), key, b, m))
    log_s = sum(c[0] for c in contrib)
    contrib.sort(key=lambda c: -abs(c[0]))
    reasons = [{"key": k, "b": b, "feature": model["features"][k]["short"], "bin": model["features"][k]["bins"][b]["label"],
                "m": round(m, 2), "data_weight": model["features"][k]["bins"][b]["data_weight"]}
               for _, k, b, m in contrib[:3] if abs(math.log(max(m, 1e-3))) > 0.05]
    return round(100 / (1 + math.exp(-1.6 * log_s))), round(math.exp(log_s), 2), reasons


def confidence(model):
    """Rough 0-1 measure of how much the model is your data vs. the folklore prior."""
    return round(min(1.0, model["catches"] / 60) * 0.5 + min(1.0, model["effort"] / 75) * 0.5, 2)


def forecast(model, lat, lon):
    wx = weather.forecast(lat, lon)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    out = []
    for t_str in sorted(wx):
        t = _parse(t_str)
        if t < now:
            continue
        f = features(t, wx, lat, lon)
        if not f:
            continue
        s, mult, reasons = score(model, f)
        w = wx[t_str]
        out.append({"time": t_str, "score": s, "multiplier": mult, "expected": round(model["r0"] * mult, 2) if model["r0"] else None,
                    "reasons": reasons, "features": f,
                    "wx": {k: w.get(k) for k in ("temp", "pressure", "wind_speed", "wind_dir", "cloud", "precip", "sst")}})
    return out


def best_windows(hours, size=3, top=5):
    """Best non-overlapping `size`-hour windows, skipping night hours."""
    cands = []
    for i in range(len(hours) - size + 1):
        win = hours[i:i + size]
        if any(h["features"].get("light") == "night" for h in win):
            continue
        cands.append((sum(h["score"] for h in win) / size, i))
    cands.sort(reverse=True)
    picked, used = [], set()
    for avg, i in cands:
        if used & set(range(i, i + size)):
            continue
        used |= set(range(i, i + size))
        picked.append({"start": hours[i]["time"], "end": hours[i + size - 1]["time"], "score": round(avg),
                       "reasons": hours[i + size // 2]["reasons"]})
        if len(picked) == top:
            break
    return sorted(picked, key=lambda w: w["start"])
