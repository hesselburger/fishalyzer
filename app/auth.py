"""Username + 4-digit PIN login with long-lived sessions.

A 4-digit PIN only has 10,000 combinations, so guessing is throttled hard:
- per user: 5 wrong PINs -> locked 15 min; every further 5 doubles the lock (max 24 h). A correct PIN resets it.
- per IP: 10 failed logins per 15 min across all usernames.
Session tokens are random; only their SHA-256 is stored.

Manage users from the server shell:
  docker exec fishalyzer python -m app.auth add <name> <pin> [--admin]
  docker exec fishalyzer python -m app.auth pin <name> <pin>      (also unlocks)
  docker exec fishalyzer python -m app.auth list
"""
import hashlib
import hmac
import re
import secrets
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from . import db

COOKIE = "fz_session"
SESSION_DAYS = 400          # browsers cap cookies at ~400 days; refreshed on use, so effectively "stay logged in"
USER_TRIES, LOCK_MIN, LOCK_MAX_H = 5, 15, 24
IP_TRIES, IP_WINDOW_S = 10, 15 * 60
PIN_RE = re.compile(r"^\d{4}$")
NAME_RE = re.compile(r"^[\w .'-]{1,30}$")
FMT = "%Y-%m-%dT%H:%M:%S"

_ip_fails = defaultdict(deque)


def hash_pin(pin, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pin.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def check_pin(pin, stored):
    salt, _ = stored.split("$")
    return hmac.compare_digest(hash_pin(pin, bytes.fromhex(salt)), stored)


_DUMMY = hash_pin("0000")  # unknown usernames cost the same time as known ones


def validate(name, pin):
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("Username: 1-30 letters, digits, spaces or . ' - _")
    if not PIN_RE.match(pin or ""):
        raise ValueError("PIN must be exactly 4 digits.")
    return name


def _ip_blocked(ip, now):
    q = _ip_fails[ip]
    while q and q[0] < now - IP_WINDOW_S:
        q.popleft()
    return len(q) >= IP_TRIES


def login(conn, name, pin, ip):
    """Returns (user_row, None) on success, (None, message) otherwise."""
    now = time.time()
    if _ip_blocked(ip, now):
        return None, "Too many failed attempts from this device. Try again in 15 minutes."
    u = conn.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", ((name or "").strip(),)).fetchone()
    if u and u["locked_until"] and u["locked_until"] > datetime.now().strftime(FMT):
        return None, f"Too many wrong PINs. {u['name']} is locked until {u['locked_until'][11:16]}."
    ok = check_pin(pin or "", u["pin_hash"] if u else _DUMMY) and u is not None
    if ok:
        conn.execute("UPDATE users SET failed=0, locked_until=NULL WHERE id=?", (u["id"],))
        return u, None
    _ip_fails[ip].append(now)
    if u:
        failed = u["failed"] + 1
        lock = None
        if failed % USER_TRIES == 0:
            minutes = min(LOCK_MIN * 2 ** (failed // USER_TRIES - 1), LOCK_MAX_H * 60)
            lock = (datetime.now() + timedelta(minutes=minutes)).strftime(FMT)
        conn.execute("UPDATE users SET failed=?, locked_until=COALESCE(?, locked_until) WHERE id=?", (failed, lock, u["id"]))
        if lock:
            return None, f"Too many wrong PINs. {u['name']} is locked until {lock[11:16]}."
    return None, "Wrong username or PIN."


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    now = datetime.now().strftime(FMT)
    conn.execute("INSERT INTO sessions(token_hash, user_id, created_at, last_seen) VALUES (?,?,?,?)",
                 (token_hash(token), user_id, now, now))
    return token


def session_user(conn, token):
    """The logged-in user for a cookie value, or None. Also tells whether the cookie is due for a refresh."""
    if not token:
        return None, False
    r = conn.execute("""SELECT u.id, u.name, u.admin, s.last_seen FROM sessions s JOIN users u ON u.id=s.user_id
                        WHERE s.token_hash=?""", (token_hash(token),)).fetchone()
    if not r:
        return None, False
    now = datetime.now()
    if r["last_seen"] < (now - timedelta(days=SESSION_DAYS)).strftime(FMT):
        return None, False
    stale = r["last_seen"] < (now - timedelta(days=1)).strftime(FMT)
    if stale:
        conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now.strftime(FMT), token_hash(token)))
    return {"id": r["id"], "name": r["name"], "admin": bool(r["admin"])}, stale


def end_session(conn, token):
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token or ""),))


def add_user(conn, name, pin, admin=False):
    name = validate(name, pin)
    if conn.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone():
        raise ValueError(f"{name} already exists.")
    return conn.execute("INSERT INTO users(name, pin_hash, admin) VALUES (?,?,?)",
                        (name, hash_pin(pin), int(admin))).lastrowid


def set_pin(conn, user_id, pin, keeptoken_hash=None):
    """New PIN, unlocked, and every other session logged out."""
    if not PIN_RE.match(pin or ""):
        raise ValueError("PIN must be exactly 4 digits.")
    conn.execute("UPDATE users SET pin_hash=?, failed=0, locked_until=NULL WHERE id=?", (hash_pin(pin), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash IS NOT ?", (user_id, keeptoken_hash))


def _cli(argv):
    db.init()
    with db.db() as conn:
        if argv[:1] == ["add"] and len(argv) >= 3:
            add_user(conn, argv[1], argv[2], "--admin" in argv)
            print(f"Added {argv[1]}{' (admin)' if '--admin' in argv else ''}.")
        elif argv[:1] == ["pin"] and len(argv) == 3:
            u = conn.execute("SELECT id FROM users WHERE name=? COLLATE NOCASE", (argv[1],)).fetchone()
            if not u:
                sys.exit(f"No user called {argv[1]}.")
            set_pin(conn, u["id"], argv[2])
            print(f"New PIN set for {argv[1]}; unlocked and logged out everywhere.")
        elif argv[:1] == ["list"]:
            for u in conn.execute("SELECT name, admin, failed, locked_until FROM users ORDER BY name"):
                print(f"{u['name']:<20} {'admin' if u['admin'] else '':<6} failed={u['failed']} locked_until={u['locked_until'] or '-'}")
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    try:
        _cli(sys.argv[1:])
    except ValueError as e:
        sys.exit(str(e))
