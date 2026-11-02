"""SQLite storage. One file, no ORM - plenty for a handful of anglers.

Everything an angler logs lives in exactly one logbook (see logbooks.py). Spot details (name, position, notes)
and trip notes are encrypted with FISHALYZER_KEY, which lives in the server's .env and never in the database,
so a copied database file or backup gives away no spots. The server itself can still read them.
"""
import json
import os
import sqlite3
from contextlib import contextmanager

from cryptography.fernet import Fernet

DB_PATH = os.environ.get("FISHALYZER_DB", os.path.join(os.path.dirname(__file__), "..", "data", "fishalyzer.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pin_hash TEXT NOT NULL,        -- scrypt, see auth.py
    admin INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,      -- wrong PINs since the last correct one
    locked_until TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M', 'now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS logbooks (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    personal INTEGER NOT NULL DEFAULT 0,   -- 1 = the owner's own logbook; nobody can join it
    invite_code TEXT UNIQUE,               -- teams only
    prior_hours REAL,                      -- the Insights "trust in folklore" slider; NULL = default
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M', 'now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS logbook_members (
    logbook_id INTEGER NOT NULL REFERENCES logbooks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M', 'now', 'localtime')),
    PRIMARY KEY (logbook_id, user_id)
);
CREATE TABLE IF NOT EXISTS spots (
    id INTEGER PRIMARY KEY,
    logbook_id INTEGER REFERENCES logbooks(id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    secret TEXT NOT NULL           -- encrypted {"name", "lat", "lon", "notes"}
);
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY,
    spot_id INTEGER REFERENCES spots(id) ON DELETE SET NULL,
    start TEXT NOT NULL,          -- local time Europe/Copenhagen, 'YYYY-MM-DDTHH:MM'
    "end" TEXT,                   -- NULL while the trip is ongoing
    anglers TEXT DEFAULT '',      -- comma separated names; effort = hours * count
    notes TEXT DEFAULT '',        -- encrypted when not empty
    created_at TEXT DEFAULT (datetime('now')),
    logbook_id INTEGER REFERENCES logbooks(id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);
-- How many fish of each species a trip produced. This is what the model learns from.
CREATE TABLE IF NOT EXISTS trip_counts (
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    species TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trip_id, species)
);
-- The day's "top fish": the few worth measuring. Not every fish caught.
CREATE TABLE IF NOT EXISTS catches (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
    time TEXT NOT NULL,
    species TEXT NOT NULL DEFAULT 'Perch',
    length_cm REAL,
    weight_g REAL,
    lure TEXT DEFAULT '',
    angler TEXT DEFAULT '',
    released INTEGER DEFAULT 1,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS weather (
    lat REAL NOT NULL,            -- rounded to a coarse grid (see weather.grid), shared by every spot in that cell
    lon REAL NOT NULL,
    time TEXT NOT NULL,           -- local hour 'YYYY-MM-DDTHH:00'
    temp REAL, pressure REAL, wind_speed REAL, wind_dir REAL, gusts REAL,
    cloud REAL, precip REAL, sst REAL, sea_level REAL,
    PRIMARY KEY (lat, lon, time)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M', 'now', 'localtime')),
    author TEXT DEFAULT '',
    page TEXT DEFAULT '',          -- where in the app it was sent from
    text TEXT NOT NULL,
    done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,   -- sha256 of the cookie value
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    logbook_id INTEGER             -- the logbook this device is looking at
);
CREATE INDEX IF NOT EXISTS idx_catches_time ON catches(time);
CREATE INDEX IF NOT EXISTS idx_trips_start ON trips(start);
"""
# Created after the v0.4 migration, since older databases don't have these columns before it.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_spots_logbook ON spots(logbook_id);
CREATE INDEX IF NOT EXISTS idx_trips_logbook ON trips(logbook_id);
"""

_fernet = None


def _cipher():
    global _fernet
    if _fernet is None:
        key = os.environ.get("FISHALYZER_KEY")
        if not key:
            raise RuntimeError("FISHALYZER_KEY is not set. deploy.sh creates one in the server's .env; "
                               "locally: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        _fernet = Fernet(key)
    return _fernet


def seal(value):
    return _cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(token):
    return json.loads(_cipher().decrypt(token.encode()))


def seal_note(text):
    return seal(text) if text else ""


def open_note(stored):
    return unseal(stored) if stored else ""


def spot_secret(name, lat, lon, notes):
    return seal({"name": name, "lat": lat, "lon": lon, "notes": notes or ""})


def _spot(r):
    return {"id": r["id"], "logbook_id": r["logbook_id"], **unseal(r["secret"])}


def spots(conn, logbook_id):
    return sorted((_spot(r) for r in conn.execute("SELECT * FROM spots WHERE logbook_id=?", (logbook_id,))),
                  key=lambda s: s["name"].lower())


def spot(conn, sid, logbook_id=None):
    """One spot, decrypted. With logbook_id: None unless the spot is in that logbook."""
    r = conn.execute("SELECT * FROM spots WHERE id=?", (sid,)).fetchone()
    return _spot(r) if r and (logbook_id is None or r["logbook_id"] == logbook_id) else None


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    _cipher()  # fail at startup, not on the first request, when the key is missing
    with db() as conn:
        conn.executescript(SCHEMA)
        # v0.1 logged single fish live and allowed open-ended trips; fold those into the per-day format.
        conn.execute("""INSERT OR IGNORE INTO trip_counts(trip_id, species, count)
                        SELECT trip_id, species, COUNT(*) FROM catches
                        WHERE trip_id NOT IN (SELECT trip_id FROM trip_counts) GROUP BY trip_id, species""")
        conn.execute("""UPDATE trips SET "end" = strftime('%Y-%m-%dT%H:%M', start, '+2 hours') WHERE "end" IS NULL""")
    with db() as conn:
        if "secret" not in {r[1] for r in conn.execute("PRAGMA table_info(spots)")}:
            from . import logbooks
            backup = sqlite3.connect(DB_PATH + ".pre-0.4.bak")  # unencrypted: delete it once v0.4 looks right
            conn.backup(backup)
            backup.close()
            conn.execute("BEGIN")  # all or nothing: a failed migration leaves the v0.3 database untouched
            logbooks.migrate_04(conn)
    with db() as conn:
        conn.executescript(INDEXES)


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
