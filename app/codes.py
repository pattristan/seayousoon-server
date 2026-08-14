"""Pairing-code generation and username validation."""

from __future__ import annotations

import re
import secrets

# Unambiguous alphabet — no 0/O/1/I/L to avoid read-aloud mistakes.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Account usernames: lowercase letters/digits/._- , 3-20 chars.
# Deliberately NOT the crew ID — see db.py for why.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,19}$")

SHIPS = [
    "AIDAbella", "AIDAblu", "AIDAcosma", "AIDAdiva", "AIDAluna", "AIDAmar",
    "AIDAnova", "AIDAperla", "AIDAprima", "AIDAsol", "AIDAstella",
]


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match(username.strip().lower()))


def ship_prefix(ship: str) -> str:
    """AIDAsol -> SOL, AIDAnova -> NOVA. Falls back to first letters."""
    name = ship.strip()
    if name.upper().startswith("AIDA"):
        return name[4:].upper()
    return name[:4].upper()


def generate_code(ship: str, length: int = 5) -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(length))
    return f"{ship_prefix(ship)}-{body}"
