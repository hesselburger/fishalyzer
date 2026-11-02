"""v0.4 self-check: the v0.3 -> v0.4 migration, and that no account can reach another logbook's spots or trips.
Run from the project folder: python tests/test_logbooks.py (needs requirements.txt installed; no network)."""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cryptography.fernet import Fernet  # noqa: E402

TMP = tempfile.mkdtemp()
DB = os.path.join(TMP, "fishalyzer.db")
os.environ.update(FISHALYZER_DB=DB, FISHALYZER_KEY=Fernet.generate_key().decode())

# A v0.3 database (schema as it was), with two accounts and Sune's data.
V03 = """
CREATE TABLE spots (id INTEGER PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL, notes TEXT DEFAULT '');
CREATE TABLE trips (id INTEGER PRIMARY KEY, spot_id INTEGER REFERENCES spots(id) ON DELETE SET NULL, start TEXT NOT NULL,
    "end" TEXT, anglers TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE trip_counts (trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE, species TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (trip_id, species));
CREATE TABLE catches (id INTEGER PRIMARY KEY, trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE, time TEXT NOT NULL,
    species TEXT NOT NULL DEFAULT 'Perch', length_cm REAL, weight_g REAL, lure TEXT DEFAULT '', angler TEXT DEFAULT '',
    released INTEGER DEFAULT 1, notes TEXT DEFAULT '');
CREATE TABLE weather (lat REAL NOT NULL, lon REAL NOT NULL, time TEXT NOT NULL, temp REAL, pressure REAL, wind_speed REAL,
    wind_dir REAL, gusts REAL, cloud REAL, precip REAL, sst REAL, sea_level REAL, PRIMARY KEY (lat, lon, time));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE feedback (id INTEGER PRIMARY KEY, created_at TEXT, author TEXT DEFAULT '', page TEXT DEFAULT '', text TEXT NOT NULL,
    done INTEGER DEFAULT 0);
CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, pin_hash TEXT NOT NULL, admin INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0, locked_until TEXT, created_at TEXT);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, last_seen TEXT NOT NULL);
INSERT INTO users(id, name, pin_hash, admin) VALUES (1, 'Sune', 'x$y', 1), (2, 'Test1234', 'x$y', 0);
INSERT INTO spots(id, name, lat, lon, notes) VALUES (4, 'Secret Inlet', 55.748, 12.098, 'park by the barn'), (5, 'Other Bæk (Præstø)', 55.12, 12.04, '');
INSERT INTO trips(id, spot_id, start, "end", anglers, notes) VALUES (1, 4, '2026-10-01T07:00', '2026-10-01T10:00', 'Sune', 'under the bridge'),
    (2, 5, '2026-10-02T07:00', '2026-10-02T09:00', 'Sune', '');
INSERT INTO trip_counts VALUES (1, 'Perch', 12), (2, 'Perch', 3);
INSERT INTO catches(trip_id, time, species, length_cm) VALUES (1, '2026-10-01T07:00', 'Perch', 38);
INSERT INTO weather(lat, lon, time, pressure) VALUES (55.72, 12.1, '2026-10-01T07:00', 1010), (55.71, 12.1, '2026-10-01T07:00', 1011);
INSERT INTO settings VALUES ('prior_hours', '12');
"""
old = sqlite3.connect(DB)
old.executescript(V03)
old.close()

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, logbooks, main, weather  # noqa: E402  (importing main runs the migration)

weather.ensure_range = lambda *a, **k: True  # no network: trip saves try to fetch weather in the background

# ---- migration ----
assert os.path.exists(DB + ".pre-0.4.bak"), "backup written before migrating"
with db.db() as conn:
    assert "name" not in {r[1] for r in conn.execute("PRAGMA table_info(spots)")}, "plaintext spot columns dropped"
    mine = logbooks.personal(conn, 1)
    team = conn.execute("SELECT * FROM logbooks WHERE name='P4L'").fetchone()
    assert team and team["owner_id"] == 1 and team["invite_code"] and team["prior_hours"] == 12
    assert [s["name"] for s in db.spots(conn, mine)] == ["Other Bæk (Præstø)", "Secret Inlet"]
    assert [s["name"] for s in db.spots(conn, team["id"])] == ["Other Bæk (Præstø)", "Secret Inlet"], "copied into P4L"
    assert conn.execute("SELECT COUNT(*) FROM trips WHERE logbook_id=?", (team["id"],)).fetchone()[0] == 2
    assert conn.execute("SELECT SUM(c.count) FROM trip_counts c JOIN trips t ON t.id=c.trip_id WHERE t.logbook_id=?",
                        (team["id"],)).fetchone()[0] == 15, "fish counts copied"
    assert db.open_note(conn.execute("SELECT notes FROM trips WHERE id=1").fetchone()[0]) == "under the bridge"
    assert sorted(tuple(r) for r in conn.execute("SELECT lat, lon FROM weather")) == [(55.7, 12.1)], "weather on the coarse grid"
    assert conn.execute("SELECT COUNT(*) FROM logbook_members WHERE user_id=2").fetchone()[0] == 0, "Test1234 sees nothing yet"
    sune, test = auth.new_session(conn, 1), auth.new_session(conn, 2)
conn = sqlite3.connect(DB)
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
raw = open(DB, "rb").read()
assert b"Secret Inlet" not in raw and b"barn" not in raw and b"bridge" not in raw, "no spot or note in the database file"

# ---- access ----
S, T = TestClient(main.app), TestClient(main.app)
S.cookies.set(auth.COOKIE, sune)
T.cookies.set(auth.COOKIE, test)
TEAM, SPOT = team["id"], 4
trip = {"spot_id": SPOT, "date": "2026-10-05", "start_time": "07:00", "end_time": "09:00", "counts": [{"species": "Perch", "count": 2}]}

assert S.get("/api/auth/me").json()["logbook"]["personal"]
assert len(S.get("/api/spots").json()) == 2
assert T.get("/api/spots").json() == [] and T.get("/api/trips").json() == []
assert T.get(f"/api/trips/1").status_code == 404
assert T.get(f"/api/forecast?spot_id={SPOT}").status_code == 404
assert T.put(f"/api/spots/{SPOT}", json={"name": "mine now", "lat": 1, "lon": 1}).status_code == 404
assert T.delete(f"/api/spots/{SPOT}").status_code == 404
assert T.post("/api/trips", json=trip).status_code == 400, "can't log a trip at someone else's spot"
T.delete("/api/trips/1")
assert len(S.get("/api/trips").json()) == 2, "delete of a foreign trip did nothing"
assert T.put("/api/logbooks/active", json={"id": TEAM}).status_code == 404, "can't switch into a team without the code"
assert T.post("/api/logbooks/join", json={"code": "AAAA-AAAA"}).status_code == 400

code = next(lb for lb in S.get("/api/logbooks").json() if lb["id"] == TEAM)["invite_code"]
assert T.post("/api/logbooks/join", json={"code": code.lower().replace("-", " ")}).status_code == 200
assert next(lb for lb in T.get("/api/logbooks").json() if lb["id"] == TEAM)["invite_code"] is None, "only the owner sees the code"
team_spot = T.get("/api/spots").json()[0]["id"]
assert T.post("/api/trips", json={**trip, "spot_id": team_spot}).status_code == 200
assert S.put("/api/logbooks/active", json={"id": TEAM}).status_code == 200
assert len(S.get("/api/trips").json()) == 3 and len(S.get("/api/logbooks/{}/members".format(TEAM)).json()) == 2
assert T.post(f"/api/logbooks/{TEAM}/code").status_code == 403, "members can't reset the code"
assert S.post(f"/api/logbooks/{TEAM}/leave", json={"mode": "keep"}).status_code == 400, "the owner can't just leave"

# leave and take my trip with me: it ends up in my personal logbook and is gone from the team
assert T.post(f"/api/logbooks/{TEAM}/leave", json={"mode": "take"}).status_code == 200
assert len(S.get("/api/trips").json()) == 2 and len(S.get("/api/spots").json()) == 2, "team spot used by others stays"
assert T.get("/api/auth/me").json()["logbook"]["personal"], "fell back to the personal logbook"
mine_t = T.get("/api/trips").json()
assert len(mine_t) == 1 and mine_t[0]["n_fish"] == 2 and len(T.get("/api/spots").json()) == 1

# rejoin, log, get removed by the owner: a copy comes along, the team keeps its own
T.post("/api/logbooks/join", json={"code": code})
T.post("/api/trips", json={**trip, "spot_id": team_spot})
assert S.delete(f"/api/logbooks/{TEAM}/members/2").status_code == 200
assert len(T.get("/api/trips").json()) == 2 and len(S.get("/api/trips").json()) == 3

# a new team, then deleting it: every member keeps their own trips
lid = T.post("/api/logbooks", json={"name": "Fjord gang"}).json()["id"]
T.post("/api/spots", json={"name": "Gang spot", "lat": 55.5, "lon": 11.9})
gang_spot = T.get("/api/spots").json()[0]["id"]
T.post("/api/trips", json={**trip, "spot_id": gang_spot})
assert S.delete(f"/api/logbooks/{lid}").status_code == 404, "outsiders can't delete it"
assert T.delete(f"/api/logbooks/{lid}").status_code == 200
T.put("/api/logbooks/active", json={"id": T.get("/api/logbooks").json()[0]["id"]})
assert len(T.get("/api/trips").json()) == 3 and "Gang spot" in [s["name"] for s in T.get("/api/spots").json()]

# settings and the model are per logbook
S.put("/api/settings", json={"prior_hours": 4})
assert S.get("/api/settings").json()["prior_hours"] == 4 and T.get("/api/settings").json()["prior_hours"] == 8
assert S.get("/api/status").json()["db"]["trips"] == 3 and T.get("/api/status").json()["db"]["trips"] == 3

# deleting an account: their teams pass to the next member, their personal logbook goes
T.post("/api/logbooks/join", json={"code": code})
S.put("/api/logbooks/active", json={"id": TEAM})
assert S.delete("/api/users/2").status_code == 200
with db.db() as conn:
    assert conn.execute("SELECT COUNT(*) FROM logbooks WHERE owner_id=2").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM trips WHERE logbook_id=?", (TEAM,)).fetchone()[0] == 3, "team trips stay"
print("logbooks: all checks passed")
