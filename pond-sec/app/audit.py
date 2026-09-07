"""Audit trail.

Answers "what happened, to which account, from where" after the fact. Written on
every authentication decision and every VM session change.

Nothing written here is rendered back into a page, so it can hold the raw source
address without becoming a stored-XSS route.
"""

from flask import has_request_context, request

from db.orm import db
from db.audit_models import AuditLog

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
    source = "-"
    if has_request_context():
        source = request.remote_addr or "unknown"
    try:
        db.session.add(AuditLog(
           actor_user_id=user_id,
           action=event,
           target_type="user",
           target_id=user_id,
           details_json={"username": username, "source_ip": source, "detail": detail},))
        db.session.commit()
    except Exception as exc:  # logging must never break the request
        db.session.rollback()
        print(f"[audit] could not write {event}: {exc}")

def to_row(log: AuditLog) -> dict:
    details = log.details_json or {}
    return {
        "audit_id": log.audit_id, "occurred_at": log.created_at,
        "event": log.action, "user_id": log.actor_user_id,
        "username": details.get("username"), "source_ip": details.get("source_ip"),
        "detail": details.get("detail"),
    }

def query_recent(limit: int = 12, event: str = None, user_id: int = None):
    q = db.session.query(AuditLog).order_by(AuditLog.audit_id.desc())
    if event:
        q = q.filter(AuditLog.action == event)
    if user_id:
        q = q.filter(AuditLog.actor_user_id == user_id)
    return [to_row(row) for row in q.limit(limit).all()]

def distinct_events():
    return [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]