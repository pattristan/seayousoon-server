"""Rate limiting for the endpoints an attacker would hammer.

A fixed-window, in-memory limiter: we run a single uvicorn worker on one
small VPS, so a dict beats dragging in Redis. Limits reset on restart —
acceptable, because the attacks this blocks (PIN guessing, pairing-code
guessing, register spam) need thousands of tries, not a lucky handful.
"""

from __future__ import annotations

import time
from threading import Lock

from fastapi import Request

_hits: dict[str, tuple[float, int]] = {}
_lock = Lock()


def client_ip(request: Request) -> str:
    """Real client address: we sit behind Caddy on localhost, so the first
    hop in X-Forwarded-For is the caller (Caddy overwrites the header)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def allow(key: str, max_hits: int, window_seconds: int) -> bool:
    """True if this attempt is within the window's budget."""
    now = time.monotonic()
    with _lock:
        # Opportunistic pruning keeps the dict from growing unbounded.
        if len(_hits) > 4096:
            for k, (start, _) in list(_hits.items()):
                if now - start > window_seconds:
                    del _hits[k]
        start, count = _hits.get(key, (now, 0))
        if now - start > window_seconds:
            start, count = now, 0
        if count >= max_hits:
            return False
        _hits[key] = (start, count + 1)
        return True


# Budgets: generous for humans, hopeless for brute force.
def allow_login(ip: str, username: str) -> bool:
    return allow(f"login:ip:{ip}", 20, 15 * 60) and allow(f"login:user:{username}", 10, 15 * 60)


def allow_register(ip: str) -> bool:
    return allow(f"register:{ip}", 5, 60 * 60)


def allow_redeem(ip: str) -> bool:
    return allow(f"redeem:{ip}", 12, 60 * 60)


def allow_generate(username: str) -> bool:
    return allow(f"generate:{username}", 15, 60 * 60)


LOGIN_ERROR = "Too many attempts — please wait a few minutes and try again."
