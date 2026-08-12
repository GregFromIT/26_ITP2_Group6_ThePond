"""Roles and permissions.

Three roles, defined once here and enforced everywhere through can():

    student    the default for anyone who registers. No staff powers at all.
    moderator  runs classes. Can see accounts and sessions, unlock a student
               who has locked themselves out, issue a temporary password, and
               kill a session whose VM is stuck. Cannot change anyone's role.
    admin      the system administrator. Everything a moderator can do, plus
               granting and removing moderator (and admin) access.

WHY A MATRIX AND NOT `if user.role == "admin"` CHECKS
-----------------------------------------------------
Scattered role comparisons drift: someone adds a view, copies the wrong check,
and a moderator quietly gets a power nobody granted. Here, adding a capability
means adding one line to PERMISSIONS, and the whole policy is readable in one
screen — including by a marker or an auditor who does not want to read views.

ADDING A PERMISSION
-------------------
    1. add a name to PERMISSIONS with the set of roles that hold it
    2. guard the view with @require("your_permission")
    3. hide the UI for it with {% if can('your_permission') %} in the template

Do both 2 and 3. Hiding a button is not access control — the guard on the view
is what actually stops the request. Hiding it is just courtesy.

ESCALATION RULES (enforced in admin.py, not here)
-------------------------------------------------
    * you can never act on an account that outranks you
    * moderators cannot act on other moderators or on admins
    * an admin cannot demote themselves, and the last admin cannot be demoted
      at all — that is what stops the platform locking out its own operators
"""

import functools

from flask import abort, g

STUDENT = "student"
MODERATOR = "moderator"
ADMIN = "admin"

ALL_ROLES = (STUDENT, MODERATOR, ADMIN)

# Higher outranks lower. Used by the escalation checks in admin.py.
RANK = {STUDENT: 0, MODERATOR: 1, ADMIN: 2}

LABELS = {
    STUDENT: "Student",
    MODERATOR: "Moderator",
    ADMIN: "System administrator",
}

DESCRIPTIONS = {
    STUDENT: "Takes challenges. No access to other accounts.",
    MODERATOR: "Class staff: can view accounts, unlock students and close stuck sessions.",
    ADMIN: "Full access, including granting and removing moderator access.",
}

# The whole access policy. Everything else in the codebase asks this.
PERMISSIONS = {
    # --- moderator and above -------------------------------------------
    "view_admin_console": {MODERATOR, ADMIN},
    "view_users": {MODERATOR, ADMIN},
    "unlock_account": {MODERATOR, ADMIN},
    "lock_account": {MODERATOR, ADMIN},
    "reset_password": {MODERATOR, ADMIN},
    "view_sessions": {MODERATOR, ADMIN},
    "close_any_session": {MODERATOR, ADMIN},
    "view_audit_log": {MODERATOR, ADMIN},
    # --- admin only ------------------------------------------------------
    "change_roles": {ADMIN},
}

# Deliberately absent: any power to edit scores or delete flag awards. Scores
# are the evidence of what a student did, and the award ledger is what makes
# double-claiming impossible. If a cheating case needs a score changed, that
# should be a documented, deliberate database action by an administrator with a
# reason recorded — not a button in a web UI that a tired moderator can misfire.
# Raise it with the client before adding one.


def role_of(user) -> str:
    """The role on a user row, defaulting safely to student."""
    if user is None:
        return STUDENT
    try:
        return user["role"] or STUDENT
    except (KeyError, IndexError, TypeError):
        return STUDENT


def can(permission: str, user=None) -> bool:
    """Does this user hold this permission? Defaults to the signed-in user.

    An unknown permission name returns False rather than raising: a typo in a
    template should fail closed and hide the control, not 500 the page. Typos in
    a @require() guard are caught by the tests, which assert every permission
    name in use exists in PERMISSIONS.
    """
    if user is None:
        user = g.get("user")
    if user is None:
        return False
    return role_of(user) in PERMISSIONS.get(permission, set())


def outranks(actor, target) -> bool:
    """True if actor is strictly senior to target. Equal ranks do NOT outrank.

    That is what stops one moderator unlocking, locking or resetting another,
    and stops an admin quietly acting on a peer without it being an admin-level
    decision.
    """
    return RANK[role_of(actor)] > RANK[role_of(target)]


def require(permission: str):
    """Guard a view. Put it BELOW @bp.route and ABOVE the function.

        @bp.route("/thing")
        @require("view_users")
        def thing(): ...

    Sends signed-out users to sign in; returns 403 for a signed-in user who
    simply lacks the permission, because pretending the page does not exist
    would be confusing for staff who genuinely should not have it.
    """

    def decorator(view):
        @functools.wraps(view)
        def wrapped(**kwargs):
            if g.get("user") is None:
                from flask import flash, redirect, request, url_for

                flash("Sign in to reach that page.", "info")
                return redirect(url_for("auth.login", next=request.path))
            if not can(permission):
                abort(403)
            return view(**kwargs)

        return wrapped

    return decorator


def init_app(app):
    """Expose can() and the role labels to every template."""
    app.jinja_env.globals["can"] = can
    app.jinja_env.globals["role_labels"] = LABELS
