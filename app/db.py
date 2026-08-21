"""SQLite storage for Sea You Soon pairing.

Three tables:
  crew          — a seafarer's account: chosen username + PIN, ship, contract
  pairing_codes — short-lived single-use codes a crew member generates
  watch_links   — a family member's active link to a crew member (revocable)

Deliberately NO crew ID: it can't be verified (no roster access) and is
semi-public on board, so treating it as an identifier only created an
impersonation/squatting target. Identity lives in the sharing channel —
a code is trusted because the person you know sent it to you themselves.

Plain sqlite3 (stdlib) — no ORM. The whole DB is a single file, trivial to
back up. Postgres is only worth it if this outgrows one small VPS.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3

import bcrypt
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("SEAYOUSOON_DB", os.path.join(os.path.dirname(__file__), "..", "data.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS crew (
    username       TEXT PRIMARY KEY,   -- chosen by the seafarer, lowercase
    name           TEXT NOT NULL,
    ship           TEXT NOT NULL,
    embark_date    TEXT NOT NULL,   -- ISO date (YYYY-MM-DD)
    disembark_date TEXT NOT NULL,
    pin_hash       TEXT NOT NULL,
    pin_salt       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    code       TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES crew(username),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    max_uses   INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS watch_links (
    watch_id     TEXT PRIMARY KEY,
    username     TEXT NOT NULL REFERENCES crew(username),
    watcher_name TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   TEXT NOT NULL REFERENCES watch_links(watch_id),
    username   TEXT NOT NULL REFERENCES crew(username),
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Additive migration: the follower's IANA timezone (optional), so crew
        # composing a message can see the reader's local time.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(watch_links)")]
        if "timezone" not in cols:
            conn.execute("ALTER TABLE watch_links ADD COLUMN timezone TEXT")


# ---------------------------------------------------------------------------
# PIN hashing — bcrypt (salt lives inside the hash; pin_salt stays "" for new
# rows). Legacy salted-SHA-256 rows still verify and are upgraded on the next
# successful login via set_pin() (see needs_rehash).
# ---------------------------------------------------------------------------
def hash_pin(pin: str) -> tuple[str, str]:
    digest = bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()
    return digest, ""


def verify_pin(pin: str, pin_hash: str, salt: str) -> bool:
    if pin_hash.startswith("$2"):
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    legacy = hashlib.sha256((salt + pin).encode()).hexdigest()
    return secrets.compare_digest(legacy, pin_hash)


def needs_rehash(pin_hash: str) -> bool:
    return not pin_hash.startswith("$2")


def set_pin(username: str, pin: str):
    pin_hash, pin_salt = hash_pin(pin)
    with connect() as conn:
        conn.execute(
            "UPDATE crew SET pin_hash = ?, pin_salt = ? WHERE username = ?",
            (pin_hash, pin_salt, username),
        )


# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------
def create_crew(username, name, ship, embark_date, disembark_date, pin):
    pin_hash, pin_salt = hash_pin(pin)
    with connect() as conn:
        conn.execute(
            """INSERT INTO crew (username, name, ship, embark_date, disembark_date,
                                 pin_hash, pin_salt, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (username, name, ship, embark_date, disembark_date, pin_hash, pin_salt, now_iso()),
        )


def get_crew(username):
    with connect() as conn:
        return conn.execute("SELECT * FROM crew WHERE username = ?", (username,)).fetchone()


def update_crew_contract(username, ship, embark_date, disembark_date):
    with connect() as conn:
        conn.execute(
            "UPDATE crew SET ship=?, embark_date=?, disembark_date=? WHERE username=?",
            (ship, embark_date, disembark_date, username),
        )


# ---------------------------------------------------------------------------
# Pairing codes
# ---------------------------------------------------------------------------
def save_code(code, username, valid_days=7):
    expires = datetime.now(timezone.utc) + timedelta(days=valid_days)
    with connect() as conn:
        conn.execute(
            """INSERT INTO pairing_codes (code, username, created_at, expires_at, max_uses, uses, active)
               VALUES (?,?,?,?,1,0,1)""",
            (code, username, now_iso(), expires.isoformat()),
        )


def get_code(code):
    with connect() as conn:
        return conn.execute("SELECT * FROM pairing_codes WHERE code = ?", (code,)).fetchone()


def mark_code_used(code):
    with connect() as conn:
        conn.execute(
            "UPDATE pairing_codes SET uses = uses + 1, active = 0 WHERE code = ?", (code,)
        )


def active_codes_for(username):
    with connect() as conn:
        return conn.execute(
            """SELECT * FROM pairing_codes
               WHERE username = ? AND active = 1 AND expires_at > ?
               ORDER BY created_at DESC""",
            (username, now_iso()),
        ).fetchall()


# ---------------------------------------------------------------------------
# Watch links
# ---------------------------------------------------------------------------
def create_watch_link(watch_id, username, watcher_name):
    with connect() as conn:
        conn.execute(
            """INSERT INTO watch_links (watch_id, username, watcher_name, created_at, active)
               VALUES (?,?,?,?,1)""",
            (watch_id, username, watcher_name, now_iso()),
        )


def active_links_for(username):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM watch_links WHERE username = ? AND active = 1 ORDER BY created_at DESC",
            (username,),
        ).fetchall()


def revoke_link(watch_id, username):
    """Revoke only if the link belongs to this crew member."""
    with connect() as conn:
        conn.execute(
            "UPDATE watch_links SET active = 0 WHERE watch_id = ? AND username = ?",
            (watch_id, username),
        )


def set_link_timezone(watch_id, timezone):
    with connect() as conn:
        conn.execute(
            "UPDATE watch_links SET timezone = ? WHERE watch_id = ? AND active = 1",
            (timezone, watch_id),
        )


def get_link(watch_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM watch_links WHERE watch_id = ?", (watch_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Messages (one-way: crew -> follower; a message in a bottle, not a chat)
# ---------------------------------------------------------------------------
def create_message(username, watch_id, body):
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (watch_id, username, body, created_at) VALUES (?,?,?,?)",
            (watch_id, username, body, now_iso()),
        )


def messages_for_watch(watch_id, limit=20):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE watch_id = ? ORDER BY id DESC LIMIT ?",
            (watch_id, limit),
        ).fetchall()
