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
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from . import audit, roles
from .db import execute, query, utcnow
from .roles import require
from .security import clear_lockout, fmt_ts, issue_temporary_password
from datetime import datetime, timedelta

bp = Blueprint("admin", __name__, url_prefix="/admin")

AUDIT_PAGE_SIZE = 100


def _target(user_id: int):
    """Load the account being acted on, or 404."""
    user = query("SELECT * FROM user WHERE user_id = ?", (user_id,), one=True)
    if user is None:
        abort(404)
    return user


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
    stats = query(
        """
        SELECT (SELECT COUNT(*) FROM user)                                          AS accounts,
               (SELECT COUNT(*) FROM user WHERE role = 'moderator')                 AS moderators,
               (SELECT COUNT(*) FROM user WHERE role = 'admin')                     AS admins,
               (SELECT COUNT(*) FROM user WHERE locked_until IS NOT NULL)           AS locked,
               (SELECT COUNT(*) FROM running_instance WHERE status = 'in_progress') AS live_sessions,
               (SELECT COUNT(*) FROM active_vm WHERE status = 'running')            AS live_vms,
               (SELECT COUNT(*) FROM active_vm WHERE status = 'error')              AS stuck_vms
        """,
        one=True,
    )
    locked = query(
        "SELECT user_id, username, name, locked_until, failed_attempts FROM user "
        "WHERE locked_until IS NOT NULL ORDER BY locked_until DESC LIMIT 10"
    )
    live = query(
        "SELECT ri.instance_id, ri.started_at, u.username, c.name AS challenge_name, "
        "       t.name AS theme_name, av.proxmox_vmid "
        "FROM running_instance ri "
        "JOIN user u ON u.user_id = ri.user_id "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "LEFT JOIN active_vm av ON av.active_vm_id = ri.active_vm_id "
        "WHERE ri.status = 'in_progress' ORDER BY ri.started_at"
    )
    recent = query(
        "SELECT occurred_at, event, username, source_ip, detail FROM audit_log "
        "ORDER BY audit_id DESC LIMIT 12"
    )
    return render_template("admin/console.html", stats=stats, locked=locked, live=live,
                           recent=recent)


# ------------------------------------------------------------------ users

@bp.route("/users")
@require("view_users")
def users():
    search = request.args.get("q", "").strip()[:60]
    if search:
        rows = query(
            "SELECT * FROM user WHERE username LIKE ? OR name LIKE ? "
            "ORDER BY role DESC, username LIMIT 200",
            (f"%{search}%", f"%{search}%"),
        )
    else:
        rows = query("SELECT * FROM user ORDER BY role DESC, username LIMIT 200")
    return render_template("admin/users.html", users=rows, search=search)


@bp.route("/users/<int:user_id>")
@require("view_users")
def user_detail(user_id):
    target = _target(user_id)
    sessions = query(
        "SELECT ri.instance_id, ri.status, ri.started_at, ri.duration_seconds, "
        "       c.name AS challenge_name, t.name AS theme_name "
        "FROM running_instance ri "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "WHERE ri.user_id = ? ORDER BY ri.started_at DESC LIMIT 15",
        (user_id,),
    )
    events = query(
        "SELECT occurred_at, event, source_ip, detail FROM audit_log "
        "WHERE user_id = ? ORDER BY audit_id DESC LIMIT 20",
        (user_id,),
    )
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
    execute("UPDATE user SET locked_until = ? WHERE user_id = ?", (fmt_ts(until), user_id))
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
        remaining = query(
            "SELECT COUNT(*) AS n FROM user WHERE role = 'admin' AND user_id != ?",
            (user_id,),
            one=True,
        )["n"]
        if remaining == 0:
            flash(
                "That is the only administrator account. Promote someone else first — "
                "otherwise nobody can grant access and recovery needs shell access to "
                "the server.",
                "error",
            )
            return redirect(url_for("admin.user_detail", user_id=user_id))

    previous = target["role"]
    execute(
        "UPDATE user SET role = ?, role_set_at = ?, role_set_by = ? WHERE user_id = ?",
        (new_role, utcnow(), g.user["user_id"], user_id),
    )
    audit.record("account.role_changed", user_id=user_id, username=target["username"],
                 detail=f"{previous} -> {new_role} by {g.user['username']}")
    flash(f"{target['username']} is now {roles.LABELS[new_role].lower()}.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# --------------------------------------------------------------- sessions

@bp.route("/sessions")
@require("view_sessions")
def sessions():
    live = query(
        "SELECT ri.instance_id, ri.started_at, u.username, c.name AS challenge_name, "
        "       t.name AS theme_name, av.proxmox_vmid, av.node, av.status AS vm_status "
        "FROM running_instance ri "
        "JOIN user u ON u.user_id = ri.user_id "
        "JOIN challenge c ON c.challenge_id = ri.challenge_id "
        "JOIN theme t ON t.theme_id = ri.theme_id "
        "LEFT JOIN active_vm av ON av.active_vm_id = ri.active_vm_id "
        "WHERE ri.status = 'in_progress' ORDER BY ri.started_at"
    )
    orphans = query(
        "SELECT av.active_vm_id, av.proxmox_vmid, av.node, av.status, av.started_at, v.name "
        "FROM active_vm av JOIN vm v ON v.vm_id = av.vm_id "
        "WHERE av.status IN ('running', 'error') "
        "AND NOT EXISTS (SELECT 1 FROM running_instance ri "
        "                WHERE ri.active_vm_id = av.active_vm_id AND ri.status = 'in_progress')"
    )
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
    from .challenges import _close   # imported here to avoid a circular import

    instance = query(
        "SELECT ri.*, u.username FROM running_instance ri "
        "JOIN user u ON u.user_id = ri.user_id WHERE ri.instance_id = ?",
        (instance_id,),
        one=True,
    )
    if instance is None:
        abort(404)
    if instance["status"] != "in_progress":
        flash("That session is already closed.", "info")
        return redirect(url_for("admin.sessions"))

    _close(instance_id, "abandoned")
    audit.record("session.closed_by_staff", user_id=instance["user_id"],
                 username=instance["username"],
                 detail=f"instance {instance_id} by {g.user['username']}")
    flash(f"Closed {instance['username']}'s session and released the machine.", "success")
    return redirect(url_for("admin.sessions"))


# ------------------------------------------------------------- audit log

@bp.route("/audit")
@require("view_audit_log")
def audit_log():
    event = request.args.get("event", "").strip()[:60]
    if event:
        rows = query(
            "SELECT * FROM audit_log WHERE event = ? ORDER BY audit_id DESC LIMIT ?",
            (event, AUDIT_PAGE_SIZE),
        )
    else:
        rows = query("SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (AUDIT_PAGE_SIZE,))
    events = query("SELECT DISTINCT event FROM audit_log ORDER BY event")
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
    user = query("SELECT user_id, username, role FROM user WHERE username = ?",
                 (username,), one=True)
    if user is None:
        raise click.ClickException(f"No account called {username}.")

    execute("UPDATE user SET role = ?, role_set_at = ?, role_set_by = NULL WHERE user_id = ?",
            (role, utcnow(), user["user_id"]))
    audit.record("account.role_changed", user_id=user["user_id"], username=username,
                 detail=f"{user['role']} -> {role} via CLI")
    click.echo(f"{username}: {user['role']} -> {role}")


def init_app(app):
    app.cli.add_command(set_role_command)
