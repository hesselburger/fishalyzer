"""SQLite storage. One file, no ORM - plenty for a handful of anglers."""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("FISHALYZER_DB", os.path.join(os.path.dirname(__file__), "..", "data", "fishalyzer.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS spots (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY,
    spot_id INTEGER REFERENCES spots(id) ON DELETE SET NULL,
    start TEXT NOT NULL,          -- local time Europe/Copenhagen, 'YYYY-MM-DDTHH:MM'
    "end" TEXT,                   -- NULL while the trip is ongoing
    anglers TEXT DEFAULT '',      -- comma separated names; effort = hours * count
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
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
    lat REAL NOT NULL,            -- rounded to 2 decimals (~1 km), shared by nearby spots
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
CREATE INDEX IF NOT EXISTS idx_catches_time ON catches(time);
CREATE INDEX IF NOT EXISTS idx_trips_start ON trips(start);
"""

SEED_SPOTS = [
    # Rough example locations - edit them to your real inlets in the Spots view.
    ("Roskilde Fjord - Jyllinge (example)", 55.748, 12.098),
    ("Isefjord - Holbæk Fjord (example)", 55.718, 11.735),
]


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
    with db() as conn:
        conn.executescript(SCHEMA)
        # v0.1 logged single fish live and allowed open-ended trips; fold those into the per-day format.
        conn.execute("""INSERT OR IGNORE INTO trip_counts(trip_id, species, count)
                        SELECT trip_id, species, COUNT(*) FROM catches
                        WHERE trip_id NOT IN (SELECT trip_id FROM trip_counts) GROUP BY trip_id, species""")
        conn.execute("""UPDATE trips SET "end" = strftime('%Y-%m-%dT%H:%M', start, '+2 hours') WHERE "end" IS NULL""")
        if conn.execute("SELECT COUNT(*) FROM spots").fetchone()[0] == 0:
            conn.executemany("INSERT INTO spots(name, lat, lon) VALUES (?,?,?)", SEED_SPOTS)


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def get_setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))
