"""Registration, login, lockout and password changes.

Routes: /register, /login, /logout, /change-password

WHAT IS NOT HERE ANY MORE
-------------------------
No email address is collected or stored, so there is no self-service password
recovery: no /forgot-password, no emailed link, no reset token table, no mailer.
A student who forgets their password asks a moderator or administrator, who
issues a temporary password from the staff console and hands it over in person.

That trade is worth understanding before anyone reverses it. It removes an
entire class of attack — reset-link interception, mail spoofing, address
enumeration, mail-bombing — and it removes the platform's only way to contact a
user. Recovery now depends on staff being reachable.

Shape of every POST view here, in order — follow it when adding one:

    1. throttle.hit(...)          refuse early if the source is hammering us
    2. read fields with field()   trimmed and length-capped
    3. validate, collecting ALL errors before responding
    4. act
    5. audit.record(...)          every authentication decision is logged

ADDING A REGISTRATION FIELD takes four edits: schema.sql (column) ->
register() form dict and validation -> the INSERT -> register.html. Miss the
template and the field silently arrives empty.
"""

import functools

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
)

from . import audit, csrf, throttle
from . import audit, csrf, identity, throttle
from .security import (
    USERNAME_RE, clear_lockout, lockout_remaining, password_matches, password_problems,
    register_failure, set_password,
)

bp = Blueprint("auth", __name__)

UNI_YEARS = ["Year 1", "Year 2", "Year 3", "Year 4", "Postgraduate", "Staff"]


@bp.app_context_processor
def inject_user():
    return {"current_user": g.get("user")}


def field(name: str) -> str:
    """Read a form field, trimmed and capped.

    Use this instead of request.form.get() for anything that reaches the
    database or the audit log.
    """
    limit = current_app.config["MAX_FIELD_LENGTH"]
    return request.form.get(name, "").strip()[:limit]


def load_logged_in_user():
    """Populate g.user for this request. Registered as a before_request hook."""
    from . import session_is_idle   # imported here to avoid a circular import

    user_id = session.get("user_id")
    if user_id is not None and session_is_idle():
        session.clear()
        g.user = None
        flash("You were signed out after a period of inactivity.", "info")
        return

    g.user = (
        None
        if user_id is None
        else identity.get_user_row(user_id)
    )
    if user_id is not None and g.user is None:
        session.clear()
        return

    # A lockout applied while someone is signed in takes effect on their very
    # next request, not at their next sign-in.
    if g.user is not None and lockout_remaining(g.user):
        session.clear()
        g.user = None
        flash("This account is locked. Ask your course staff to unlock it.", "error")


def force_password_change():
    """Pin an account with a temporary password to the change-password page.

    Registered as a before_request hook AFTER load_logged_in_user. Without it a
    student handed a temporary password could simply navigate elsewhere and keep
    using a credential a staff member knows.
    """
    if g.get("user") is None or not g.user["must_change_password"]:
        return None
    allowed = {"auth.change_password", "auth.logout", "static"}
    if request.endpoint in allowed:
        return None
    return redirect(url_for("auth.change_password"))


def login_required(view):
    """Decorate any view that must not be reachable while signed out.

    Put it BELOW the route decorator. Above it and Flask registers the
    undecorated function, so the check never runs. This only checks that someone
    is signed in — not that the thing requested belongs to them. For that,
    follow the pattern in themes._owned_instance().
    """

    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.get("user") is None:
            flash("Sign in to reach that page.", "info")
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)

    return wrapped


# ---------------------------------------------------------------- register

@bp.route("/register", methods=("GET", "POST"))
def register():
    form = {"name": "", "uni_year": "", "username": ""}

    if request.method == "POST":
        allowed, wait = throttle.hit("register_ip", throttle.client_ip())
        if not allowed:
            flash(f"Too many sign-ups from this connection. Try again in {wait // 60 + 1} minutes.",
                  "error")
            return render_template("register.html", form=form, uni_years=UNI_YEARS), 429

        form = {
            "name": field("name"),
            "uni_year": field("uni_year"),
            "username": field("username"),
        }
        password = request.form.get("password", "")[:200]
        confirm = request.form.get("confirm", "")[:200]
        errors = []

        if not form["name"]:
            errors.append("Enter your name.")
        if form["uni_year"] not in UNI_YEARS:
            errors.append("Choose your year of study.")
        if not USERNAME_RE.match(form["username"]):
            errors.append("Usernames are 3-32 characters: letters, numbers, . _ or -")
        if password != confirm:
            errors.append("The two passwords do not match.")
        errors += password_problems(password, form["username"])

        # A taken username is stated plainly: usernames are printed on the
        # leaderboards, so this reveals nothing that is not already public.
        if query("SELECT 1 FROM user WHERE username = ?", (form["username"],), one=True):
            errors.append("That username is taken.")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("register.html", form=form, uni_years=UNI_YEARS)

        user_id = identity.create_user(form["name"], form["uni_year"], form["username"])
        set_password(user_id, password)
        audit.record(audit.REGISTER, user_id=user_id, username=form["username"])
        flash("Account created. Sign in to start.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html", form=form, uni_years=UNI_YEARS)


# ------------------------------------------------------------------- login

@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = field("username")
        password = request.form.get("password", "")[:200]
        limit = current_app.config["MAX_LOGIN_ATTEMPTS"]
        source = throttle.client_ip()

        # Per-source throttle sits in front of the per-account lockout. Without
        # it, one client can walk the leaderboard and lock every student out
        # with three guesses each.
        allowed, wait = throttle.hit("login_ip", source)
        if allowed:
            allowed, wait = throttle.hit("login_user", username.lower())
        if not allowed:
            audit.record(audit.LOGIN_BLOCKED, username=username)
            flash(f"Too many sign-in attempts. Try again in {wait // 60 + 1} minutes.", "error")
            return render_template("login.html", username=username), 429

        user = identity.get_user_row_by_username(username)

        if user is None:
            audit.record(audit.LOGIN_FAIL, username=username, detail="no such account")
            flash("Username or password is not right.", "error")
            return render_template("login.html", username=username)

        minutes_left = lockout_remaining(user)
        if minutes_left:
            audit.record(audit.LOGIN_LOCKED, user_id=user["user_id"], username=username)
            flash(
                f"This account is locked after {limit} failed attempts. "
                f"Try again in {minutes_left} minutes, or ask course staff to unlock it.",
                "error",
            )
            return render_template("login.html", username=username)

        if not password_matches(user["user_id"], password):
            attempts, locked = register_failure(user)
            audit.record(audit.LOGIN_FAIL, user_id=user["user_id"], username=username,
                         detail=f"attempt {attempts}")
            if locked:
                flash(
                    f"Account locked after {limit} failed attempts. "
                    f"Ask a moderator or administrator to unlock it.",
                    "error",
                )
            else:
                remaining = limit - attempts
                flash(
                    f"Username or password is not right. "
                    f"{remaining} attempt{'s' if remaining != 1 else ''} left before lockout.",
                    "error",
                )
            return render_template("login.html", username=username)

        clear_lockout(user["user_id"])
        throttle.clear("login_user", username.lower())
        throttle.clear("login_ip", source)
        identity.touch_last_login(user["user_id"])
        audit.record(audit.LOGIN_OK, user_id=user["user_id"], username=username)

        # New session identity, new CSRF token: nothing survives the boundary.
        session.clear()
        csrf.rotate()
        session["user_id"] = user["user_id"]
        session.permanent = False

        if user["must_change_password"]:
            return redirect(url_for("auth.change_password"))

        target = request.args.get("next", "")
        if target.startswith("/") and not target.startswith("//"):
            return redirect(target)
        return redirect(url_for("dashboard.index"))

    return render_template("login.html", username="")


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    csrf.rotate()
    flash("Signed out.", "info")
    return redirect(url_for("dashboard.landing"))


# --------------------------------------------------------- change password

@bp.route("/change-password", methods=("GET", "POST"))
@login_required
def change_password():
    """Self-service password change, and the landing point after a staff reset.

    The current password is required even when the account is flagged for a
    forced change: that proves whoever is at the keyboard is the person staff
    handed the temporary password to, not someone who walked up to an unlocked
    machine afterwards.
    """
    forced = bool(g.user["must_change_password"])

    if request.method == "POST":
        current = request.form.get("current", "")[:200]
        password = request.form.get("password", "")[:200]
        confirm = request.form.get("confirm", "")[:200]
        errors = []

        if not password_matches(g.user["user_id"], current):
            errors.append("Your current password is not right.")
        if password != confirm:
            errors.append("The two new passwords do not match.")
        if password == current:
            errors.append("The new password must be different from the current one.")
        errors += password_problems(password, g.user["username"])

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("change_password.html", forced=forced)

        set_password(g.user["user_id"], password)
        audit.record("password.changed", user_id=g.user["user_id"],
                     username=g.user["username"])
        flash("Password updated.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("change_password.html", forced=forced)
