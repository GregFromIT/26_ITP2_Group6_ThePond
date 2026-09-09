"""Staff console: accounts, roles, sessions and the audit log.

Routes are under /admin and every one is guarded by @require(...) from
roles.py. Moderators see the console, the account list and the session list;
only administrators see or reach the role controls.

BOOTSTRAPPING THE FIRST ADMIN
-----------------------------
Registration always creates a student — there is no "make me an admin" checkbox,
because that is exactly the box an attacker ticks. The first administrator is
promoted from the command line by whoever controls the server:

    flask --app wsgi set-role gthomas admin

After that, admins promote each other through the web console.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No score editing, no flag award deletion, no account deletion. See the note at
the bottom of roles.py for the reasoning; if the client wants any of them, they
should be added consciously with their own permissions and audit events, not
folded into an existing page.
"""

import click
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from datetime import datetime, timedelta

from db.orm import db
from db.challenge_models import Challenge
from db.runtime_models import ChallengeInstance, VMInstance
from db.user_models import Role, User, UserCredential

from . import audit, roles
from .roles import require
from .security import clear_lockout, fmt_ts, issue_temporary_password
from . import identity
from .identity import ROLE_NAME_TO_DB, get_user_row

bp = Blueprint("admin", __name__, url_prefix="/admin")

AUDIT_PAGE_SIZE = 100


def _target(user_id: int):
    """Load the account being acted on, or 404."""
    row = get_user_row(user_id)
    if row is None:
        abort(404)
    return row


def _may_act_on(target):
    """Refuse any action against an equal or senior account.

    Called by every state-changing view here. Without it, a moderator could
    unlock or lock another moderator — or an admin — which turns a class-support
    role into a way to take over the platform.
    """
    if not roles.outranks(g.user, target):
        abort(403)


# ---------------------------------------------------------------- console

@bp.route("/")
@require("view_admin_console")
def console():
        stats = {
            "accounts": db.session.query(User).count(),
            "moderators": db.session.query(User).join(Role).filter(Role.role_name == "manager").count(),
            "admins": db.session.query(User).join(Role).filter(Role.role_name == "sysadmin").count(),
            "locked": db.session.query(UserCredential).filter(UserCredential.locked_until.isnot(None)).count(),
            "live_sessions": db.session.query(ChallengeInstance).filter_by(status="running").count(),
            "live_vms": db.session.query(VMInstance).filter(
                VMInstance.status == "running", VMInstance.deleted_at.is_(None)
            ).count(),
            "stuck_vms": db.session.query(VMInstance).filter(
                VMInstance.status == "error", VMInstance.deleted_at.is_(None)
            ).count(),
        }

        locked_users = (
            db.session.query(User).join(UserCredential)
            .filter(UserCredential.locked_until.isnot(None))
            .order_by(UserCredential.locked_until.desc())
            .limit(10).all()
        )

        locked = [
            {
            "user_id": u.user_id, "username": u.username, "name": u.display_name,
            "locked_until": fmt_ts(u.credentials.locked_until), "failed_attempts": u.credentials.failed_login_count,
            }
            for u in locked_users
        ]

        live_rows = (
            db.session.query(ChallengeInstance, User, Challenge)
            .join(User, ChallengeInstance.user_id == User.user_id)
            .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
            .filter(ChallengeInstance.status == "running")
            .order_by(ChallengeInstance.started_at)
            .all()
        )

        live = []
        for ci, u, challenge in live_rows:
            vm = (
                db.session.query(VMInstance)
                .filter(VMInstance.instance_id == ci.instance_id, VMInstance.deleted_at.is_(None))
                .order_by(VMInstance.created_at.desc()).first()
            )
            live.append({
                "instance_id": ci.instance_id, "started_at": ci.started_at, "username": u.username,
                "challenge_name": challenge.title, "theme_name": (challenge.category or "general").title(),
                "proxmox_vmid": vm.proxmox_vmid if vm else None,
            })
        recent = audit.query_recent(limit=12)
        return render_template("admin/console.html", stats=stats, locked=locked, live=live, recent=recent)


# ------------------------------------------------------------------ users

@bp.route("/users")
@require("view_users")
def users():
    search = request.args.get("q", "").strip()[:60]
    q = db.session.query(User).join(Role)
    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(User.username.ilike(like), User.display_name.ilike(like)))
    rows_orm = q.order_by(Role.role_level.desc(), User.username).limit(200).all()
    rows = [get_user_row(u.user_id) for u in rows_orm]
    return render_template("admin/users.html", users=rows, search=search)


@bp.route("/users/<int:user_id>")
@require("view_users")
def user_detail(user_id):
    target = _target(user_id)
    session_rows = (
        db.session.query(ChallengeInstance, Challenge)
        .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
        .filter(ChallengeInstance.user_id == user_id)
        .order_by(ChallengeInstance.started_at.desc())
        .limit(15).all()
    )
    sessions = [
        {
            "instance_id": ci.instance_id, "status": ci.status, "started_at": ci.started_at,
            "duration_seconds": max(0, int(((ci.completed_at or ci.stopped_at) - ci.started_at).total_seconds()))
                                 if ci.started_at and (ci.completed_at or ci.stopped_at) else None,
            "challenge_name": challenge.title, "theme_name": (challenge.category or "general").title(),
        }
        for ci, challenge in session_rows
    ]
    events = audit.query_recent(limit=20, user_id=user_id)
    return render_template(
        "admin/user_detail.html",
        target=target,
        sessions=sessions,
        events=events,
        may_act=roles.outranks(g.user, target),
        assignable=roles.ALL_ROLES,
    )


@bp.route("/users/<int:user_id>/unlock", methods=("POST",))
@require("unlock_account")
def unlock(user_id):
    target = _target(user_id)
    _may_act_on(target)
    clear_lockout(user_id)
    audit.record("account.unlocked", user_id=user_id, username=target["username"],
                 detail=f"by {g.user['username']}")
    flash(f"{target['username']} can sign in again.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<int:user_id>/lock", methods=("POST",))
@require("lock_account")
def lock(user_id):
    target = _target(user_id)
    _may_act_on(target)
    hours = min(max(int(request.form.get("hours", 24) or 24), 1), 8760)
    until = datetime.utcnow() + timedelta(hours=hours)
    user = db.session.get(User, user_id)
    if user.credentials is None:
        user.credentials = UserCredential(user_id=user_id, password_hash="")
    user.credentials.locked_until = until
    db.session.commit()
    audit.record("account.locked_by_staff", user_id=user_id, username=target["username"],
                 detail=f"{hours}h by {g.user['username']}")
    flash(f"{target['username']} is locked out for {hours} hours.", "info")
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<int:user_id>/reset-password", methods=("POST",))
@require("reset_password")
def reset_password(user_id):
    """Issue a temporary password for a student who cannot get in.

    With no email address on file, this is the ONLY recovery path, and it is
    deliberately an in-person one: the temporary password appears once on this
    staff member's screen and is never stored in readable form, never logged,
    and never shown again. Hand it over face to face, or over a channel you
    already trust, and the student is forced to change it before they can reach
    anything else.

    Two things follow from that and are worth stating in the report:
      * staff can effectively take over any account below their own rank. That
        is unavoidable when staff hold recovery, and it is why the action is
        audited by name and why _may_act_on() stops it being used sideways or
        upwards.
      * if the student is not physically present, staff must not send this over
        anything they would not send a password over.
    """
    target = _target(user_id)
    _may_act_on(target)
    temporary = issue_temporary_password(user_id)
    audit.record(audit.TEMP_PASSWORD, user_id=user_id, username=target["username"],
                 detail=f"issued by {g.user['username']}")
    # The password itself goes in the flash and nowhere else — not the audit
    # detail, not the log, not the database.
    flash(
        f"Temporary password for {target['username']}: {temporary} — give this to them "
        f"directly. It will not be shown again, and they must change it at sign-in.",
        "success",
    )
    return redirect(url_for("admin.user_detail", user_id=user_id))


@bp.route("/users/<int:user_id>/role", methods=("POST",))
@require("change_roles")
def set_role(user_id):
    """Grant or remove moderator/admin access. Administrators only.

    Three guards, in order — each exists because of a specific way this could go
    wrong:
      1. the role must be a known one       (a typo would create a powerless account)
      2. demoting an admin must leave at least one behind, INCLUDING when an
         admin steps down themselves (otherwise nobody can administer anything
         and recovery needs shell access)
      3. every change is audited with who did it

    Stepping down is deliberately allowed: handing over to a colleague and
    dropping back to moderator is a normal thing for staff to do, and forbidding
    it just means the old admin account stays privileged forever. What is not
    allowed is being the last one out the door.
    """
    target = _target(user_id)
    new_role = request.form.get("role", "")

    if new_role not in roles.ALL_ROLES:
        flash("That is not a role.", "error")
        return redirect(url_for("admin.user_detail", user_id=user_id))

    if target["role"] == roles.ADMIN and new_role != roles.ADMIN:
        remaining = (
            db.session.query(User).join(Role)
            .filter(Role.role_name == "sysadmin", User.user_id != user_id)
            .count()
        )
        if remaining == 0:
            flash(
                "That is the only administrator account. Promote someone else first — "
                "otherwise nobody can grant access and recovery needs shell access to "
                "the server.",
                "error",
            )
            return redirect(url_for("admin.user_detail", user_id=user_id))

    db_role = db.session.execute(
        db.select(Role).filter_by(role_name=ROLE_NAME_TO_DB[new_role])
    ).scalar_one()
    user = db.session.get(User, user_id)
    previous = target["role"]
    user.role_id = db_role.role_id
    db.session.commit()
    audit.record("account.role_changed", user_id=user_id, username=target["username"],
                 detail=f"{previous} -> {new_role} by {g.user['username']}")
    flash(f"{target['username']} is now {roles.LABELS[new_role].lower()}.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# --------------------------------------------------------------- sessions

@bp.route("/sessions")
@require("view_sessions")
def sessions():
    live_rows = (
        db.session.query(ChallengeInstance, User, Challenge)
        .join(User, ChallengeInstance.user_id == User.user_id)
        .join(Challenge, ChallengeInstance.challenge_id == Challenge.challenge_id)
        .filter(ChallengeInstance.status == "running")
        .order_by(ChallengeInstance.started_at)
        .all()
    )
    live = []
    for ci, u, challenge in live_rows:
        vm = (
            db.session.query(VMInstance)
            .filter(VMInstance.instance_id == ci.instance_id, VMInstance.deleted_at.is_(None))
            .order_by(VMInstance.created_at.desc()).first()
        )
        live.append({
            "instance_id": ci.instance_id, "started_at": ci.started_at, "username": u.username,
            "challenge_name": challenge.title, "theme_name": (challenge.category or "general").title(),
            "proxmox_vmid": vm.proxmox_vmid if vm else None, "node": vm.proxmox_node if vm else None,
            "vm_status": vm.status if vm else None,
        })
    orphan_rows = (
        db.session.query(VMInstance)
        .filter(VMInstance.status.in_(("running", "error")), VMInstance.deleted_at.is_(None))
        .all()
    )
    orphans = []
    for vm in orphan_rows:
        ci = db.session.get(ChallengeInstance, vm.instance_id)
        if ci is not None and ci.status == "running":
            continue
        orphans.append({
            "vm_instance_id": vm.vm_instance_id, "proxmox_vmid": vm.proxmox_vmid,
            "node": vm.proxmox_node, "status": vm.status, "started_at": vm.started_at or vm.created_at,
            "name": vm.hostname,
        })
    return render_template("admin/sessions.html", live=live, orphans=orphans)


@bp.route("/sessions/<int:instance_id>/close", methods=("POST",))
@require("close_any_session")
def close_session(instance_id):
    """Force a session shut and release its VM.

    For the stuck cases: a student who closed the tab, a challenge left running over
    a weekend, a VM that needs freeing for the next class. Recorded as abandoned
    — staff closing it is not the student completing it — and the student
    keeps every flag they had already captured, because those are already in the
    award ledger.
    """
    from .themes import _close   # imported here to avoid a circular import

    instance = db.session.get(ChallengeInstance, instance_id)
    if instance is None:
        abort(404)
    if instance.status != "running":
        flash("That session is already closed.", "info")
        return redirect(url_for("admin.sessions"))

    username = db.session.get(User, instance.user_id).username
    _close(instance_id, "abandoned")
    audit.record("session.closed_by_staff", user_id=instance.user_id,
                 username=username,
                 detail=f"instance {instance_id} by {g.user['username']}")
    flash(f"Closed {username}'s session and released the machine.", "success")
    return redirect(url_for("admin.sessions"))


# ------------------------------------------------------------- audit log

@bp.route("/audit")
@require("view_audit_log")
def audit_log():
    event = request.args.get("event", "").strip()[:60]
    rows = audit.query_recent(limit=AUDIT_PAGE_SIZE, event=event or None)
    events = audit.distinct_events()
    return render_template("admin/audit.html", rows=rows, events=events, selected=event)


# ------------------------------------------------------------ CLI command

@click.command("set-role")
@click.argument("username")
@click.argument("role", type=click.Choice(roles.ALL_ROLES))
def set_role_command(username, role):
    """Set a user's role from the command line.

    The bootstrap path for the first administrator, and the way back in if every
    admin account is ever lost. Requires shell access to the server, which is
    the point — it is the one privilege escalation that cannot be performed over
    the web.
    """
    user = db.session.execute(db.select(User).filter_by(username=username)).scalar_one_or_none()
    if user is None:
        raise click.ClickException(f"No account called {username}.")

    previous_role = user.role.role_name
    db_role = db.session.execute(
        db.select(Role).filter_by(role_name=ROLE_NAME_TO_DB[role])
    ).scalar_one()
    user.role_id = db_role.role_id
    db.session.commit()
    audit.record("account.role_changed", user_id=user.user_id, username=username,
                 detail=f"{previous_role} -> {db_role.role_name} via CLI")
    click.echo(f"{username}: {previous_role} -> {db_role.role_name}")

def init_app(app):
    app.cli.add_command(set_role_command)
