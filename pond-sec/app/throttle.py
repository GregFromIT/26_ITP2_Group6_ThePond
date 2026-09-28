"""Rate limiting.

Sliding window counters in SQLite. Every throttled action names a bucket; a
bucket is usually an action plus a source address, a username, or both.

Why the database and not a dict: with more than one gunicorn worker an in-memory
counter gives an attacker N times the allowance, and it resets on every deploy.
"""

from datetime import datetime, timedelta

from flask import request

from db.orm import db
from db.throttle_models import ThrottleEvent


# Every throttled action in one place, so the limits can be reviewed together
# rather than hunted for across the views.
#
# ADDING A LIMIT:
#   1. add an entry here: "action_name": (attempts_allowed, window_seconds)
#   2. call throttle.hit("action_name", key) in the view before doing the work
#   3. if it returns False, tell the user how long to wait and return early
#      (a 429 status is the honest one to send)
#
# Choosing the key decides WHAT is limited: client_ip() limits a source,
# a username limits an account, f"{user_id}:{challenge_id}" limits one user on
# one challenge. Limiting only by account lets an attacker deny service to others;
# limiting only by IP lets a botnet through. Sensitive actions get both, as
# auth.login does.
#
# (limit, window seconds)
LIMITS = {
    "login_ip": (15, 900),          # 15 sign-in attempts per IP per 15 min
    "login_user": (10, 900),        # slows credential stuffing against one name
    "register_ip": (5, 3600),
    "flag_challenge": (20, 300),    # 20 flag guesses per challenge per 5 min
    "change_password": (10, 900),
}


def client_ip() -> str:
    """Source address. ProxyFix has already resolved X-Forwarded-For if trusted."""
    return request.remote_addr or "unknown"


def _window_start(seconds: int) -> datetime:
    return datetime.utcnow() - timedelta(seconds=seconds)


def check(action: str, key: str):
    """Is this action allowed right now? Returns (allowed, seconds_to_wait).

    Read-only — use hit() unless you specifically need to test without counting.
    """
    limit, window = LIMITS[action]
    bucket = f"{action}:{key}"
    since = _window_start(window)

    hits = db.session.query(ThrottleEvent).filter(
       ThrottleEvent.bucket == bucket, ThrottleEvent.occurred_at >= since
   ).count()
    
    if hits < limit:
        return True, 0

    oldest = db.session.query(db.func.min(ThrottleEvent.occurred_at)).filter(
       ThrottleEvent.bucket == bucket, ThrottleEvent.occurred_at >= since
   ).scalar()
    
    wait = int((oldest + timedelta(seconds=window) - datetime.utcnow()).total_seconds())
    return False, max(1, wait)


def record(action: str, key: str):
    db.session.add(ThrottleEvent(bucket=f"{action}:{key}"))
    db.session.commit()


def hit(action: str, key: str):
    """Check and count in one call. Denied attempts are not counted, so a
    throttled client can always get back in once its window rolls off."""
    allowed, wait = check(action, key)
    if allowed:
        record(action, key)
    return allowed, wait


def clear(action: str, key: str):
    db.session.query(ThrottleEvent).filter(ThrottleEvent.bucket == f"{action}:{key}").delete()
    db.session.commit()


def prune(older_than_seconds: int = 86400):
   db.session.query(ThrottleEvent).filter(
        ThrottleEvent.occurred_at < (datetime.utcnow() - timedelta(seconds=older_than_seconds))
   ).delete()
   db.session.commit()
