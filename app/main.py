import csv
import hashlib
import io
import os
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, model, water, weather

VERSION = "0.3.0"
STATIC = os.path.join(os.path.dirname(__file__), "static")
STARTED = time.time()

app = FastAPI(title="Fishalyzer", version=VERSION)
db.init()


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    # Phones cache aggressively; revalidate so a redeploy shows up immediately.
    resp = await call_next(request)
    resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


OPEN_API = {"/api/auth/login", "/api/auth/setup", "/api/health"}


def _is_https(request):
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _set_cookie(resp, request, token):
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=_is_https(request), path="/")


@app.middleware("http")
async def require_login(request, call_next):
    # The page and its scripts load freely; every bit of data sits behind /api and needs a session.
    path = request.url.path
    if not path.startswith("/api/") or path in OPEN_API:
        return await call_next(request)
    token = request.cookies.get(auth.COOKIE)
    with db.db() as conn:
        user, refresh = auth.session_user(conn, token)
    if not user:
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    request.state.user = user
    resp = await call_next(request)
    if refresh:  # slide the cookie's expiry so active users never get logged out
        _set_cookie(resp, request, token)
    return resp


def _client_ip(request):
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _admin(request):
    if not request.state.user["admin"]:
        raise HTTPException(403, "Only admins can manage users.")


# ---------- schemas ----------
class Spot(BaseModel):
    name: str
    lat: float
    lon: float
    notes: str = ""


class Count(BaseModel):
    species: str
    count: int = 0


class TopFish(BaseModel):
    species: str = "Perch"
    length_cm: Optional[float] = None
    weight_g: Optional[float] = None
    lure: str = ""
    angler: str = ""
    released: bool = True
    notes: str = ""


class Trip(BaseModel):
    """One trip = one day at one spot, logged afterwards."""
    spot_id: int
    date: str                # YYYY-MM-DD
    start_time: str          # HH:MM
    end_time: str            # HH:MM, same day
    anglers: str = ""
    notes: str = ""
    counts: List[Count] = []
    top_fish: List[TopFish] = []


def _sync_trip_weather(trip_id):
    with db.db() as conn:
        t = conn.execute('SELECT t.start, t."end", s.lat, s.lon FROM trips t JOIN spots s ON s.id=t.spot_id WHERE t.id=?',
                         (trip_id,)).fetchone()
        if not t:
            return
        try:
            weather.ensure_range(conn, t["lat"], t["lon"], datetime.fromisoformat(t["start"]), datetime.fromisoformat(t["end"]))
        except Exception:
            pass  # recorded in weather.status; retried via /api/weather/sync


def _model(species=None, min_length=None):
    with db.db() as conn:
        prior = float(db.get_setting(conn, "prior_hours", model.DEFAULT_PRIOR_HOURS))
        hours, skipped = model.dataset(conn, species or None, min_length or None)
    m = model.fit(hours, prior)
    m["skipped_hours"] = skipped
    m["prior_hours"] = prior
    m["confidence"] = model.confidence(m)
    return m


# ---------- login and users ----------
class Login(BaseModel):
    name: str
    pin: str


class PinChange(BaseModel):
    old_pin: str
    new_pin: str


class NewUser(BaseModel):
    name: str
    pin: str
    admin: bool = False


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


def _setup_allowed(conn, request):
    # First admin can only be created while there are no users, and never through the Cloudflare tunnel
    # (Cloudflare always adds cf-connecting-ip), so a stranger on the public URL can't claim it first.
    no_users = not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return no_users, no_users and "cf-connecting-ip" not in request.headers


@app.get("/api/auth/setup")
def setup_status(request: Request):
    with db.db() as conn:
        needed, allowed = _setup_allowed(conn, request)
    return {"needed": needed, "allowed": allowed}


@app.post("/api/auth/setup")
def setup(body: Login, request: Request):
    with db.db() as conn:
        _, allowed = _setup_allowed(conn, request)
        if not allowed:
            raise HTTPException(403, "Setup is only possible on a fresh install, from your home network or Tailscale.")
        try:
            uid = auth.add_user(conn, body.name, body.pin, admin=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        token = auth.new_session(conn, uid)
    resp = JSONResponse({"id": uid, "name": body.name.strip(), "admin": True})
    _set_cookie(resp, request, token)
    return resp


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    with db.db() as conn:
        user, err = auth.login(conn, body.name, body.pin, _client_ip(request))
        token = auth.new_session(conn, user["id"]) if user else None
    if not user:  # raised outside the block so the failed-attempt counter is committed
        raise HTTPException(429 if "locked" in err or "Too many" in err else 401, err)
    resp = JSONResponse({"id": user["id"], "name": user["name"], "admin": bool(user["admin"])})
    _set_cookie(resp, request, token)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    with db.db() as conn:
        auth.end_session(conn, request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    return request.state.user


@app.put("/api/auth/pin")
def change_pin(body: PinChange, request: Request):
    me = request.state.user
    with db.db() as conn:
        row = conn.execute("SELECT pin_hash FROM users WHERE id=?", (me["id"],)).fetchone()
        if not auth.check_pin(body.old_pin, row["pin_hash"]):
            raise HTTPException(400, "Current PIN is wrong.")
        try:
            auth.set_pin(conn, me["id"], body.new_pin, auth.token_hash(request.cookies.get(auth.COOKIE)))
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/users")
def list_users(request: Request):
    _admin(request)
    with db.db() as conn:
        return db.rows(conn.execute("""SELECT u.id, u.name, u.admin, u.failed, u.locked_until, u.created_at,
                                       (SELECT MAX(last_seen) FROM sessions s WHERE s.user_id=u.id) AS last_seen
                                       FROM users u ORDER BY u.name"""))


@app.post("/api/users")
def create_user(body: NewUser, request: Request):
    _admin(request)
    with db.db() as conn:
        try:
            return {"id": auth.add_user(conn, body.name, body.pin, body.admin)}
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.put("/api/users/{uid}")
def update_user(uid: int, body: dict, request: Request):
    """Admin: {"pin": "1234"} sets a new PIN (unlocks, logs out everywhere); {"admin": true/false}."""
    _admin(request)
    with db.db() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise HTTPException(404, "Unknown user")
        if "admin" in body:
            if uid == request.state.user["id"] and not body["admin"]:
                raise HTTPException(400, "You can't remove your own admin rights.")
            conn.execute("UPDATE users SET admin=? WHERE id=?", (int(bool(body["admin"])), uid))
        if "pin" in body:
            try:
                keep = auth.token_hash(request.cookies.get(auth.COOKIE)) if uid == request.state.user["id"] else None
                auth.set_pin(conn, uid, str(body["pin"]), keep)
            except ValueError as e:
                raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/users/{uid}")
def delete_user(uid: int, request: Request):
    _admin(request)
    if uid == request.state.user["id"]:
        raise HTTPException(400, "You can't delete yourself.")
    with db.db() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


# ---------- spots ----------
@app.get("/api/spots")
def list_spots():
    with db.db() as conn:
        return db.rows(conn.execute("SELECT * FROM spots ORDER BY name"))


@app.post("/api/spots")
def add_spot(s: Spot):
    with db.db() as conn:
        cur = conn.execute("INSERT INTO spots(name, lat, lon, notes) VALUES (?,?,?,?)", (s.name, s.lat, s.lon, s.notes))
        return {"id": cur.lastrowid}


@app.get("/api/water-name")
def water_name(lat: float, lon: float):
    """Name and type of the water at a point, e.g. {"name": "Tubæk", "kind": "river", "place": "Præstø"}."""
    try:
        return water.name_at(lat, lon) or {}
    except Exception as e:
        raise HTTPException(502, f"Name lookup unavailable: {e}")


@app.put("/api/spots/{sid}")
def update_spot(sid: int, s: Spot):
    with db.db() as conn:
        conn.execute("UPDATE spots SET name=?, lat=?, lon=?, notes=? WHERE id=?", (s.name, s.lat, s.lon, s.notes, sid))
    return {"ok": True}


@app.delete("/api/spots/{sid}")
def delete_spot(sid: int):
    with db.db() as conn:
        if conn.execute("SELECT COUNT(*) FROM trips WHERE spot_id=?", (sid,)).fetchone()[0]:
            raise HTTPException(400, "Spot has trips logged - edit it instead of deleting.")
        conn.execute("DELETE FROM spots WHERE id=?", (sid,))
    return {"ok": True}


# ---------- trips (with their counts and top fish) ----------
TRIP_SELECT = 'SELECT t.*, s.name AS spot_name, ' \
              '(SELECT COALESCE(SUM(count), 0) FROM trip_counts c WHERE c.trip_id=t.id) AS n_fish ' \
              'FROM trips t LEFT JOIN spots s ON s.id=t.spot_id'


def _full_trip(conn, t):
    t = dict(t)
    t["date"], t["start_time"], t["end_time"] = t["start"][:10], t["start"][11:16], t["end"][11:16]
    t["counts"] = db.rows(conn.execute("SELECT species, count FROM trip_counts WHERE trip_id=? ORDER BY count DESC", (t["id"],)))
    t["top_fish"] = db.rows(conn.execute("SELECT * FROM catches WHERE trip_id=? ORDER BY length_cm DESC", (t["id"],)))
    return t


def _save_trip(conn, tid, t: Trip):
    try:
        d = date.fromisoformat(t.date)
        start = datetime.fromisoformat(f"{t.date}T{t.start_time}")
        end = datetime.fromisoformat(f"{t.date}T{t.end_time}")
    except ValueError:
        raise HTTPException(400, "Invalid date or time.")
    if d > date.today():
        raise HTTPException(400, "Log trips after they happen - that date is in the future.")
    if end <= start:
        raise HTTPException(400, "End time must be after start time (trips are within one day).")
    fmt = "%Y-%m-%dT%H:%M"
    if tid is None:
        tid = conn.execute('INSERT INTO trips(spot_id, start, "end", anglers, notes) VALUES (?,?,?,?,?)',
                           (t.spot_id, start.strftime(fmt), end.strftime(fmt), t.anglers, t.notes)).lastrowid
    else:
        conn.execute('UPDATE trips SET spot_id=?, start=?, "end"=?, anglers=?, notes=? WHERE id=?',
                     (t.spot_id, start.strftime(fmt), end.strftime(fmt), t.anglers, t.notes, tid))
        conn.execute("DELETE FROM trip_counts WHERE trip_id=?", (tid,))
        conn.execute("DELETE FROM catches WHERE trip_id=?", (tid,))

    counts = {}
    for c in t.counts:
        sp = c.species.strip()
        if sp and c.count > 0:
            counts[sp] = counts.get(sp, 0) + c.count
    top = [f for f in t.top_fish if f.species.strip()]
    for f in top:  # a measured top fish is also a caught fish
        sp = f.species.strip()
        counts[sp] = max(counts.get(sp, 0), sum(1 for x in top if x.species.strip() == sp))
    conn.executemany("INSERT INTO trip_counts(trip_id, species, count) VALUES (?,?,?)",
                     [(tid, sp, n) for sp, n in counts.items()])
    conn.executemany(
        "INSERT INTO catches(trip_id, time, species, length_cm, weight_g, lure, angler, released, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        [(tid, start.strftime(fmt), f.species.strip(), f.length_cm, f.weight_g, f.lure, f.angler, int(f.released), f.notes)
         for f in top])
    return tid


@app.get("/api/trips")
def list_trips(start: Optional[str] = None, end: Optional[str] = None, limit: int = 200):
    with db.db() as conn:
        return db.rows(conn.execute(TRIP_SELECT + " WHERE t.start >= ? AND t.start < ? ORDER BY t.start DESC LIMIT ?",
                                    (start or "0000", end or "9999", limit)))


@app.get("/api/trips/{tid}")
def get_trip(tid: int):
    with db.db() as conn:
        t = conn.execute(TRIP_SELECT + " WHERE t.id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Unknown trip")
        return _full_trip(conn, t)


@app.post("/api/trips")
def add_trip(t: Trip, bg: BackgroundTasks):
    with db.db() as conn:
        tid = _save_trip(conn, None, t)
    bg.add_task(_sync_trip_weather, tid)
    return {"id": tid}


@app.put("/api/trips/{tid}")
def update_trip(tid: int, t: Trip, bg: BackgroundTasks):
    with db.db() as conn:
        _save_trip(conn, tid, t)
    bg.add_task(_sync_trip_weather, tid)
    return {"ok": True}


@app.delete("/api/trips/{tid}")
def delete_trip(tid: int):
    with db.db() as conn:
        conn.execute("DELETE FROM trips WHERE id=?", (tid,))
    return {"ok": True}


@app.get("/api/species")
def species():
    with db.db() as conn:
        return [r[0] for r in conn.execute("SELECT species FROM trip_counts GROUP BY species ORDER BY SUM(count) DESC")]


@app.get("/api/anglers")
def anglers():
    with db.db() as conn:
        names = {n.strip() for (a,) in conn.execute("SELECT anglers FROM trips") for n in (a or "").split(",")}
    return sorted(n for n in names if n)


# ---------- calendar / day ----------
@app.get("/api/calendar")
def calendar(month: str):
    """Per-day summary for 'YYYY-MM'."""
    first = date.fromisoformat(month + "-01")
    nxt = (first + timedelta(days=32)).replace(day=1)
    days = {}
    with db.db() as conn:
        for r in conn.execute("""SELECT substr(t.start,1,10) d, COUNT(DISTINCT t.id) trips,
                                        (SELECT COALESCE(SUM(c.count),0) FROM trip_counts c JOIN trips t2 ON t2.id=c.trip_id
                                         WHERE substr(t2.start,1,10)=substr(t.start,1,10)) fish,
                                        (SELECT MAX(length_cm) FROM catches k JOIN trips t3 ON t3.id=k.trip_id
                                         WHERE substr(t3.start,1,10)=substr(t.start,1,10)) biggest
                                 FROM trips t WHERE t.start>=? AND t.start<? GROUP BY d""",
                              (first.isoformat(), nxt.isoformat())):
            days[r["d"]] = {"trips": r["trips"], "catches": r["fish"], "biggest": r["biggest"]}
    return days


@app.get("/api/day/{d}")
def day(d: str):
    nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    with db.db() as conn:
        trips = [_full_trip(conn, t) for t in conn.execute(TRIP_SELECT + " WHERE t.start>=? AND t.start<? ORDER BY t.start", (d, nxt))]
        for t in trips:
            s = conn.execute("SELECT lat, lon FROM spots WHERE id=?", (t["spot_id"],)).fetchone()
            if s:
                start, end = datetime.fromisoformat(t["start"]), datetime.fromisoformat(t["end"])
                wx = weather.load(conn, s["lat"], s["lon"], start, end)
                t["weather"] = [v for k, v in sorted(wx.items()) if start.strftime("%Y-%m-%dT%H") <= k[:13] <= end.strftime("%Y-%m-%dT%H")]
                mid = start + (end - start) / 2
                t["conditions"] = model.features(mid.replace(minute=0), wx, s["lat"], s["lon"])
    return {"date": d, "trips": trips}


# ---------- model ----------
@app.get("/api/forecast")
def forecast(spot_id: int, species: str = "", min_length: float = 0):
    with db.db() as conn:
        s = conn.execute("SELECT * FROM spots WHERE id=?", (spot_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Unknown spot")
    m = _model(species, min_length)
    try:
        hours = model.forecast(m, s["lat"], s["lon"])
    except Exception as e:
        raise HTTPException(502, f"Weather service unavailable: {e}")
    daily = {}
    for h in hours:
        d = daily.setdefault(h["time"][:10], {"date": h["time"][:10], "best": 0, "best_time": None})
        if h["features"].get("light") != "night" and h["score"] > d["best"]:
            d.update(best=h["score"], best_time=h["time"][11:16])
    for d in daily.values():  # e.g. today after sunset: fall back to the best night hour
        if d["best_time"] is None:
            top = max((h for h in hours if h["time"].startswith(d["date"])), key=lambda h: h["score"])
            d.update(best=top["score"], best_time=top["time"][11:16])
    return {"spot": dict(s), "confidence": m["confidence"], "r0": m["r0"], "hours": hours,
            "daily": list(daily.values()), "windows": model.best_windows(hours)}


@app.get("/api/insights")
def insights(species: str = "", min_length: float = 0):
    m = _model(species, min_length)
    feats = sorted(({"key": k, **v} for k, v in m["features"].items()), key=lambda f: -f["importance"])
    return {k: m[k] for k in ("r0", "effort", "catches", "hours", "skipped_hours", "prior_hours", "confidence")} | {"features": feats}


class Feedback(BaseModel):
    text: str
    page: str = ""


@app.get("/api/feedback")
def list_feedback():
    with db.db() as conn:
        return db.rows(conn.execute("SELECT * FROM feedback ORDER BY done, id DESC"))


@app.post("/api/feedback")
def add_feedback(f: Feedback, request: Request):
    if not f.text.strip():
        raise HTTPException(400, "Write something first.")
    with db.db() as conn:
        cur = conn.execute("INSERT INTO feedback(author, page, text) VALUES (?,?,?)",
                           (request.state.user["name"], f.page, f.text.strip()))
        return {"id": cur.lastrowid}


@app.put("/api/feedback/{fid}")
def mark_feedback(fid: int, body: dict):
    with db.db() as conn:
        conn.execute("UPDATE feedback SET done=? WHERE id=?", (int(bool(body.get("done"))), fid))
    return {"ok": True}


@app.delete("/api/feedback/{fid}")
def delete_feedback(fid: int):
    with db.db() as conn:
        conn.execute("DELETE FROM feedback WHERE id=?", (fid,))
    return {"ok": True}


@app.get("/api/glossary")
def glossary():
    return model.glossary()


@app.get("/api/settings")
def get_settings():
    with db.db() as conn:
        return {"prior_hours": float(db.get_setting(conn, "prior_hours", model.DEFAULT_PRIOR_HOURS))}


@app.put("/api/settings")
def put_settings(body: dict):
    with db.db() as conn:
        if "prior_hours" in body:
            db.set_setting(conn, "prior_hours", max(0.0, float(body["prior_hours"])))
    return get_settings()


@app.post("/api/weather/sync")
def weather_sync():
    """Backfill weather for every trip that is missing it."""
    with db.db() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM trips")]
    for tid in ids:
        _sync_trip_weather(tid)
    return {"trips": len(ids), "status": weather.status}


@app.get("/api/status")
def status():
    with db.db() as conn:
        counts = {k: conn.execute(f"SELECT COUNT(*) FROM {k}").fetchone()[0] for k in ("spots", "trips", "weather")}
        counts["fish"] = conn.execute("SELECT COALESCE(SUM(count), 0) FROM trip_counts").fetchone()[0]
        prior = float(db.get_setting(conn, "prior_hours", model.DEFAULT_PRIOR_HOURS))
        hours, skipped = model.dataset(conn)
    m = model.fit(hours, prior)
    return {"version": VERSION, "uptime_s": int(time.time() - STARTED), "db": counts,
            "weather": weather.status, "model": {"hours": round(m["effort"], 1), "catches": m["catches"],
                                                 "hours_missing_weather": skipped, "confidence": model.confidence(m)}}


@app.get("/api/export.csv")
def export_csv():
    """One row per trip with fish counts, top fish and the conditions mid-trip - for your own spreadsheet digging."""
    buf = io.StringIO()
    keys = list(model.FEATURES)
    w = csv.writer(buf)
    w.writerow(["date", "start", "end", "spot", "anglers", "fish_total", "counts", "top_fish", "notes", *keys])
    with db.db() as conn:
        for t in conn.execute(TRIP_SELECT + " ORDER BY t.start"):
            full = _full_trip(conn, t)
            s = conn.execute("SELECT lat, lon FROM spots WHERE id=?", (t["spot_id"],)).fetchone()
            f = {}
            if s:
                start, end = datetime.fromisoformat(t["start"]), datetime.fromisoformat(t["end"])
                mid = (start + (end - start) / 2).replace(minute=0)
                f = model.features(mid, weather.load(conn, s["lat"], s["lon"], mid, mid), s["lat"], s["lon"]) or {}
            w.writerow([full["date"], full["start_time"], full["end_time"], t["spot_name"], t["anglers"], t["n_fish"],
                        "; ".join(f"{c['species']} {c['count']}" for c in full["counts"]),
                        "; ".join(f"{x['species']} {x['length_cm'] or '?'}cm/{x['weight_g'] or '?'}g" for x in full["top_fish"]),
                        t["notes"], *[f.get(k, "") for k in keys]])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=fishalyzer-trips.csv"})


# ---------- frontend ----------
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/sw.js")
def sw():
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="application/javascript")


def _fingerprinted_index():
    # Cloudflare overrides our no-cache on .js/.css with a 4 h browser cache, so the page asks for
    # app.js?v=<content hash>: every deploy changes the URL and no cache can serve old code.
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    for name in ("app.js", "style.css"):
        with open(os.path.join(STATIC, name), "rb") as f:
            v = hashlib.sha256(f.read()).hexdigest()[:10]
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={v}\"")
    return html


INDEX_HTML = _fingerprinted_index()


@app.get("/")
def index():
    return HTMLResponse(INDEX_HTML)
