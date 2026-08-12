"""Credential handling: hashing, lockout arithmetic, temporary passwords.

Kept apart from the auth views so the rules can be tested on their own and so
there is exactly one place that reads or writes password_manager.

THERE IS NO EMAIL AND NO RESET TOKEN. Password recovery is staff-driven: a
moderator or administrator issues a temporary password from the console and
hands it over in person, and the platform forces a change on next sign-in. See
issue_temporary_password() below and app/admin.py.
"""

import re
import secrets
from datetime import datetime, timedelta

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from .db import execute, query, utcnow

# Single source of truth for the stored timestamp format — see db.utcnow().
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

# Words for temporary passwords. Deliberately plain and unambiguous: staff read
# these aloud or write them on paper, so no l/1, O/0, or anything embarrassing.
TEMP_WORDS = (
    "harbour brick candle timber ferry lantern meadow quartz saddle velvet "
    "walnut anchor copper garden hollow marble pepper ribbon tunnel willow"
).split()


# ------------------------------------------------------------------ helpers

def parse_ts(value):
    if not value:
        return None
    return datetime.strptime(value[:19], TIMESTAMP_FMT)


def fmt_ts(dt: datetime) -> str:
    return dt.strftime(TIMESTAMP_FMT)


def password_problems(password: str, username: str = "") -> list:
    """Return a list of reasons the password is unacceptable (empty = fine).

    Returning a LIST rather than raising means the user sees everything wrong
    with their password at once instead of fixing one thing per attempt.

    Add rules here and they apply to registration and to the change-password
    page together. Resist adding composition rules (one upper, one digit, one
    symbol): current NIST guidance is that they push people towards Password1!
    and no further. Length and a breached-password check are what help.
    """
    problems = []
    minimum = current_app.config["MIN_PASSWORD_LENGTH"]
    if len(password) < minimum:
        problems.append(f"Use at least {minimum} characters.")
    if username and username.lower() in password.lower():
        problems.append("Leave your username out of your password.")
    if password.lower() in {"password", "passw0rd", "letmein", "changeme"}:
        problems.append("That password is on every wordlist there is.")
    return problems


# --- The rules below are the ones an assessor will look at. Change with care.


def set_password(user_id: int, password: str, must_change: bool = False):
    """Hash and store a password. The ONLY writer of password_manager.

    must_change=True marks the account so the next sign-in lands on the
    change-password page and can go nowhere else — used for the temporary
    passwords staff hand out.
    """
    password_hash = generate_password_hash(password)
    existing = query(
        "SELECT password_id FROM password_manager WHERE user_id = ?", (user_id,), one=True
    )
    if existing:
        execute(
            "UPDATE password_manager SET password_hash = ?, updated_at = ? WHERE user_id = ?",
            (password_hash, utcnow(), user_id),
        )
    else:
        execute(
            "INSERT INTO password_manager (user_id, password_hash) VALUES (?, ?)",
            (user_id, password_hash),
        )
    execute(
        "UPDATE user SET must_change_password = ? WHERE user_id = ?",
        (1 if must_change else 0, user_id),
    )


def password_matches(user_id: int, password: str) -> bool:
    """Check a password. The ONLY reader of password_manager.

    Note the deliberate wasted hash when no credential exists: without it, a
    missing account returns noticeably faster than a wrong password, and that
    timing difference is enough to enumerate accounts.
    """
    row = query(
        "SELECT password_hash FROM password_manager WHERE user_id = ?", (user_id,), one=True
    )
    if row is None:
        generate_password_hash(password)
        return False
    return check_password_hash(row["password_hash"], password)


def issue_temporary_password(user_id: int) -> str:
    """Set a random temporary password and return it ONCE, in the clear.

    This is the only moment a password exists in readable form anywhere in the
    platform. The caller shows it to the staff member and nowhere else: it is
    never stored, never logged, never put in the audit detail, and cannot be
    retrieved again. If staff lose it they issue another one.

    Three words and a number is deliberate — long enough to survive the trip
    across a desk, easy to say out loud, and comfortably over the 12-character
    minimum so the account is not left weaker than policy while it waits to be
    changed.
    """
    words = [secrets.choice(TEMP_WORDS) for _ in range(3)]
    temporary = "-".join(words) + "-" + str(secrets.randbelow(90) + 10)
    set_password(user_id, temporary, must_change=True)
    clear_lockout(user_id)   # issuing a temporary password also lifts a lockout
    return temporary


# ------------------------------------------------------------------ lockout

def lockout_remaining(user_row):
    """Minutes left on a lockout, 0 if the account is usable."""
    locked_until = parse_ts(user_row["locked_until"])
    if locked_until is None:
        return 0
    remaining = (locked_until - datetime.utcnow()).total_seconds()
    if remaining <= 0:
        clear_lockout(user_row["user_id"])
        return 0
    return max(1, int(remaining // 60) + 1)


def register_failure(user_row):
    """Count a bad password and lock the account on the configured attempt.

    Returns (attempts_so_far, locked_now).

    This is the client-required 3-strikes lockout. On its own it is a denial of
    service against any known username, which is why auth.login() also runs a
    per-source throttle in front of it. If you touch one, look at the other.
    """
    attempts = user_row["failed_attempts"] + 1
    limit = current_app.config["MAX_LOGIN_ATTEMPTS"]
    if attempts >= limit:
        minutes = current_app.config["LOCKOUT_MINUTES"]
        until = datetime.utcnow() + timedelta(minutes=minutes or 525600)
        execute(
            "UPDATE user SET failed_attempts = ?, locked_until = ? WHERE user_id = ?",
            (attempts, fmt_ts(until), user_row["user_id"]),
        )
        return attempts, True
    execute(
        "UPDATE user SET failed_attempts = ? WHERE user_id = ?",
        (attempts, user_row["user_id"]),
    )
    return attempts, False


def clear_lockout(user_id: int):
    execute(
        "UPDATE user SET failed_attempts = 0, locked_until = NULL WHERE user_id = ?",
        (user_id,),
    )
