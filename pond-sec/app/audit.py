"""Audit trail.

Answers "what happened, to which account, from where" after the fact. Written on
every authentication decision and every VM session change.

Nothing written here is rendered back into a page, so it can hold the raw source
address without becoming a stored-XSS route.
"""

from flask import has_request_context, request

from .db import execute

# Event names, defined once so they stay greppable and consistent.
#
# ADDING AN EVENT: add a constant here in the same "noun.verb_past" style, then
# call audit.record(audit.YOUR_EVENT, ...) from the view. Do not pass a bare
# string at the call site — the whole point of these constants is that someone
# reading the log can find every place an event is raised.
LOGIN_OK = "login.success"
LOGIN_FAIL = "login.failure"
LOGIN_LOCKED = "login.locked_out"
LOGIN_BLOCKED = "login.throttled"
REGISTER = "account.registered"
PASSWORD_CHANGED = "password.changed"
TEMP_PASSWORD = "password.temporary_issued"
INSTANCE_LAUNCH = "instance.launched"
INSTANCE_CLOSE = "instance.closed"
FLAG_THROTTLED = "flag.throttled"
CSRF_REJECT = "request.csrf_rejected"


def record(event: str, user_id=None, username=None, detail=None):
    """Write one audit row.

    event:    one of the constants above.
    user_id:  the account acted on, where known. Left None for events about an
              account that does not exist (a sign-in attempt on a bad username).
    username: stored alongside user_id so the log still reads sensibly if the
              account is later deleted.
    detail:   short free text for context. Keep it short and NEVER put a
              password, flag, token or session cookie in here.

    Failures are swallowed: an audit write must never be the reason a student
    cannot sign in. If you make auditing load-bearing for something, revisit
    that decision explicitly.
    """
    source = "-"
    if has_request_context():
        source = request.remote_addr or "unknown"
    try:
        execute(
            "INSERT INTO audit_log (event, user_id, username, source_ip, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (event, user_id, username, source, detail),
        )
    except Exception as exc:  # logging must never break the request
        print(f"[audit] could not write {event}: {exc}")
