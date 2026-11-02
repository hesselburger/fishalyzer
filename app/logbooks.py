"""Logbooks: every account has a personal one; a team is a logbook shared through one invite code.

Spots, trips and the model live in exactly one logbook. Nothing moves between logbooks unless someone
chooses to: leaving a team, being removed from one, or a team being deleted only ever copies an angler's
own trips (and the spots they need) into that angler's personal logbook.
"""
import secrets

from . import db, weather

CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
LEAVE_MODES = ("keep", "copy", "take")


def new_code():
    # 31^8 ≈ 10^12 codes: guessing one over HTTP would take years, so joins need no throttle.
    c = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
    return f"{c[:4]}-{c[4:]}"


def _norm_code(code):
    c = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else None


def personal(conn, uid):
    """The user's personal logbook id, created on first use."""
    r = conn.execute("SELECT id FROM logbooks WHERE owner_id=? AND personal=1", (uid,)).fetchone()
    if r:
        return r["id"]
    lid = conn.execute("INSERT INTO logbooks(name, owner_id, personal) VALUES ('My logbook', ?, 1)", (uid,)).lastrowid
    conn.execute("INSERT INTO logbook_members(logbook_id, user_id) VALUES (?,?)", (lid, uid))
    return lid


def _team_name(name):
    name = (name or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("Team name: 1-40 characters.")
    return name


def create_team(conn, uid, name):
    lid = conn.execute("INSERT INTO logbooks(name, owner_id, invite_code) VALUES (?,?,?)",
                       (_team_name(name), uid, new_code())).lastrowid
    conn.execute("INSERT INTO logbook_members(logbook_id, user_id) VALUES (?,?)", (lid, uid))
    return lid


def rename(conn, lb, name):
    conn.execute("UPDATE logbooks SET name=? WHERE id=?", (_team_name(name), lb["id"]))


def reset_code(conn, lb):
    code = new_code()
    conn.execute("UPDATE logbooks SET invite_code=? WHERE id=?", (code, lb["id"]))
    return code


def join(conn, uid, code):
    c = _norm_code(code)
    r = c and conn.execute("SELECT id FROM logbooks WHERE invite_code=? AND personal=0", (c,)).fetchone()
    if not r:
        raise ValueError("No team has that code. Check it with whoever invited you.")
    conn.execute("INSERT OR IGNORE INTO logbook_members(logbook_id, user_id) VALUES (?,?)", (r["id"], uid))
    return r["id"]


def membership(conn, lid, uid):
    """The logbook as seen by uid, or None if uid isn't a member."""
    r = conn.execute("""SELECT l.*, l.owner_id = ? AS is_owner FROM logbooks l
                        JOIN logbook_members m ON m.logbook_id = l.id WHERE l.id = ? AND m.user_id = ?""",
                     (uid, lid, uid)).fetchone()
    return dict(r) if r else None


def active(conn, uid, lid):
    """The logbook a session works in: the one it picked while still a member, else the personal one."""
    return (lid and membership(conn, lid, uid)) or membership(conn, personal(conn, uid), uid)


def mine(conn, uid):
    personal(conn, uid)
    return db.rows(conn.execute("""
        SELECT l.id, l.name, l.personal, l.owner_id = ? AS is_owner,
               CASE WHEN l.owner_id = ? THEN l.invite_code END AS invite_code,
               (SELECT name FROM users WHERE id = l.owner_id) AS owner,
               (SELECT COUNT(*) FROM logbook_members x WHERE x.logbook_id = l.id) AS members
        FROM logbooks l JOIN logbook_members m ON m.logbook_id = l.id
        WHERE m.user_id = ? ORDER BY l.personal DESC, l.name COLLATE NOCASE""", (uid, uid, uid)))


def members(conn, lid):
    return db.rows(conn.execute("""
        SELECT u.id, u.name, m.joined_at, u.id = l.owner_id AS is_owner,
               (SELECT COUNT(*) FROM trips t WHERE t.logbook_id = l.id AND t.created_by = u.id) AS trips
        FROM logbook_members m JOIN users u ON u.id = m.user_id JOIN logbooks l ON l.id = m.logbook_id
        WHERE m.logbook_id = ? ORDER BY is_owner DESC, m.joined_at""", (lid,)))


def copy(conn, spot_ids, trip_ids, dst, uid):
    """Copy spots and trips (with their fish) into logbook dst; the copies belong to uid."""
    new_spot = {sid: conn.execute("INSERT INTO spots(logbook_id, created_by, secret) SELECT ?, ?, secret FROM spots WHERE id=?",
                                  (dst, uid, sid)).lastrowid for sid in spot_ids}
    for tid in trip_ids:
        t = conn.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone()
        nid = conn.execute('INSERT INTO trips(logbook_id, created_by, spot_id, start, "end", anglers, notes, created_at) '
                           "VALUES (?,?,?,?,?,?,?,?)", (dst, uid, new_spot.get(t["spot_id"]), t["start"], t["end"],
                                                         t["anglers"], t["notes"], t["created_at"])).lastrowid
        conn.execute("INSERT INTO trip_counts(trip_id, species, count) SELECT ?, species, count FROM trip_counts WHERE trip_id=?",
                     (nid, tid))
        conn.execute("""INSERT INTO catches(trip_id, time, species, length_cm, weight_g, lure, angler, released, notes)
                        SELECT ?, time, species, length_cm, weight_g, lure, angler, released, notes FROM catches WHERE trip_id=?""",
                     (nid, tid))


def copy_own(conn, lid, uid):
    """Copy uid's trips in lid, the spots they used and the spots uid added there, to uid's personal logbook."""
    trips = conn.execute("SELECT id, spot_id FROM trips WHERE logbook_id=? AND created_by=?", (lid, uid)).fetchall()
    spot_ids = {r["id"] for r in conn.execute("SELECT id FROM spots WHERE logbook_id=? AND created_by=?", (lid, uid))}
    spot_ids |= {t["spot_id"] for t in trips if t["spot_id"]}
    copy(conn, sorted(spot_ids), [t["id"] for t in trips], personal(conn, uid), uid)


def leave(conn, lb, uid, mode):
    """keep: everything stays with the team. copy: also copy it to my logbook. take: copy it and remove it from the team."""
    if lb["personal"]:
        raise ValueError("You can't leave your personal logbook.")
    if lb["owner_id"] == uid:
        raise ValueError("You own this team. Hand it to another member, or delete it.")
    if mode not in LEAVE_MODES:
        raise ValueError("Choose what happens to your trips.")
    if mode != "keep":
        copy_own(conn, lb["id"], uid)
    if mode == "take":
        conn.execute("DELETE FROM trips WHERE logbook_id=? AND created_by=?", (lb["id"], uid))
        # Their spots go too, unless another member's trips still use them.
        conn.execute("""DELETE FROM spots WHERE logbook_id=? AND created_by=?
                        AND id NOT IN (SELECT spot_id FROM trips WHERE spot_id IS NOT NULL)""", (lb["id"], uid))
    conn.execute("DELETE FROM logbook_members WHERE logbook_id=? AND user_id=?", (lb["id"], uid))


def remove_member(conn, lb, uid):
    """Owner removes someone: they keep a copy of their own trips; the team keeps its copy too."""
    if uid == lb["owner_id"]:
        raise ValueError("The owner can't be removed.")
    if not membership(conn, lb["id"], uid):
        raise ValueError("Not a member of this team.")
    copy_own(conn, lb["id"], uid)
    conn.execute("DELETE FROM logbook_members WHERE logbook_id=? AND user_id=?", (lb["id"], uid))


def transfer(conn, lb, uid):
    if not membership(conn, lb["id"], uid):
        raise ValueError("The new owner must be a member of the team.")
    conn.execute("UPDATE logbooks SET owner_id=? WHERE id=?", (uid, lb["id"]))


def delete_team(conn, lb):
    """Every member first gets a copy of their own trips, so deleting a team never deletes someone else's log."""
    if lb["personal"]:
        raise ValueError("A personal logbook can't be deleted.")
    for m in members(conn, lb["id"]):
        copy_own(conn, lb["id"], m["id"])
    conn.execute("DELETE FROM logbooks WHERE id=?", (lb["id"],))


def before_user_delete(conn, uid):
    """Teams the user owns pass to their longest-standing member, or go if nobody else is in them.
    The personal logbook goes with the account (ON DELETE CASCADE)."""
    for lb in conn.execute("SELECT id FROM logbooks WHERE owner_id=? AND personal=0", (uid,)).fetchall():
        heir = conn.execute("SELECT user_id FROM logbook_members WHERE logbook_id=? AND user_id!=? ORDER BY joined_at LIMIT 1",
                            (lb["id"], uid)).fetchone()
        if heir:
            conn.execute("UPDATE logbooks SET owner_id=? WHERE id=?", (heir["user_id"], lb["id"]))
        else:
            conn.execute("DELETE FROM logbooks WHERE id=?", (lb["id"],))


def migrate_04(conn):
    """v0.3 -> v0.4. Until now everything was shared by everyone. Encrypt the spots and trip notes, coarsen the
    weather cache, then give all data to the first admin's personal logbook plus a copy in a new team "P4L"
    (the friends who used v0.3) that they can share. Runs inside one transaction (see db.init)."""
    for table, col in [("spots", "logbook_id INTEGER REFERENCES logbooks(id) ON DELETE CASCADE"),
                       ("spots", "created_by INTEGER REFERENCES users(id) ON DELETE SET NULL"),
                       ("spots", "secret TEXT"),
                       ("trips", "logbook_id INTEGER REFERENCES logbooks(id) ON DELETE CASCADE"),
                       ("trips", "created_by INTEGER REFERENCES users(id) ON DELETE SET NULL"),
                       ("sessions", "logbook_id INTEGER")]:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    for r in conn.execute("SELECT id, name, lat, lon, notes FROM spots").fetchall():
        conn.execute("UPDATE spots SET secret=? WHERE id=?", (db.spot_secret(r["name"], r["lat"], r["lon"], r["notes"]), r["id"]))
    for col in ("name", "lat", "lon", "notes"):
        conn.execute(f"ALTER TABLE spots DROP COLUMN {col}")
    for r in conn.execute("SELECT id, notes FROM trips WHERE notes != ''").fetchall():
        conn.execute("UPDATE trips SET notes=? WHERE id=?", (db.seal_note(r["notes"]), r["id"]))
    weather.regrid(conn)

    owner = conn.execute("SELECT id FROM users ORDER BY admin DESC, id LIMIT 1").fetchone()
    if not owner:
        return  # shortcut: data from before any account existed stays unassigned (invisible); fine for this one install
    uid = owner["id"]
    prior = db.get_setting(conn, "prior_hours")
    mine_id = personal(conn, uid)
    conn.execute("UPDATE spots SET logbook_id=?, created_by=?", (mine_id, uid))
    conn.execute("UPDATE trips SET logbook_id=?, created_by=?", (mine_id, uid))
    team = create_team(conn, uid, "P4L")
    conn.execute("UPDATE logbooks SET prior_hours=? WHERE id IN (?,?)", (prior, mine_id, team))
    copy(conn, [r[0] for r in conn.execute("SELECT id FROM spots WHERE logbook_id=?", (mine_id,)).fetchall()],
         [r[0] for r in conn.execute("SELECT id FROM trips WHERE logbook_id=?", (mine_id,)).fetchall()], team, uid)
