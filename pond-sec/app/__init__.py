"""Pond Sec — application factory.

Start here. create_app() is the only place the pieces are wired together, so
this file is the map of the codebase:

    config.py      settings, all environment-overridable
    db.py          SQLite connections, init-db / seed-db commands
    csrf.py        per-session CSRF tokens (enforced on every unsafe request)
    auth.py        /register /login /logout /forgot-password /reset-password
    roles.py       who may do what — the whole access policy, in one matrix
    admin.py       /admin staff console: accounts, roles, sessions, audit log
    dashboard.py   / and /dashboard
    themes.py      /themes/... themes, challenges and the VM session lifecycle
    scoring.py     flag grading and every leaderboard
    security.py    hashing, lockout rules, reset tokens
    throttle.py    rate limits
    audit.py       security event log
    proxmox.py     hypervisor adapter (simulate | api)

ADDING A BLUEPRINT:
    1. write app/yourthing.py with `bp = Blueprint("yourthing", __name__)`
    2. import it at the top of this file
    3. app.register_blueprint(yourthing.bp) alongside the others below
    4. templates go in app/templates/, and every form needs the CSRF field —
       see the note in csrf.py

The order of the before_request hooks matters and is not accidental:
force_https, then csrf.protect (registered by csrf.init_app), then
auth.load_logged_in_user. A request is redirected to HTTPS before its token is
read, and its token is checked before any user is loaded. Insert new hooks with
that ordering in mind.
"""

import os
import secrets
import stat
from datetime import datetime, timedelta, timezone

from flask import Flask, current_app, redirect, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, audit, auth, csrf, dashboard, db, roles, themes
from .config import Config


def create_app(test_config=None):
    """Build and configure the app.

    test_config: dict of overrides, used by tests/test_flow.py to point at a
    temporary database. Anything passed here beats the environment, which is
    why tests never need environment variables set.
    """
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    database = app.config["DATABASE"]
    if not os.path.isabs(database):
        app.config["DATABASE"] = os.path.join(app.root_path, "..", database)

    app.config["SECRET_KEY"] = resolve_secret_key(app)

    if app.config["TRUSTED_PROXIES"]:
        # Only trust forwarded headers when something in front is known to set
        # them. Otherwise remote_addr stays whatever actually connected.
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=app.config["TRUSTED_PROXIES"],
            x_proto=app.config["TRUSTED_PROXIES"],
            x_host=app.config["TRUSTED_PROXIES"],
        )

    db.init_app(app)
    csrf.init_app(app)
    roles.init_app(app)      # exposes can() to templates
    admin.init_app(app)      # registers the set-role CLI command
    themes.sock.init_app(app)   # WebSocket console relay - see themes.console_relay

    app.before_request(force_https)
    app.before_request(auth.load_logged_in_user)
    app.before_request(auth.force_password_change)   # must run AFTER the loader
    app.after_request(security_headers)

    app.register_blueprint(dashboard.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(themes.bp)
    app.register_blueprint(admin.bp)

    register_filters(app)
    register_error_handlers(app)
    return app


# ------------------------------------------------------------- secret key

def resolve_secret_key(app) -> str:
    """FLASK_SECRET_KEY, else a generated per-instance file, else refuse.

    A hard-coded default key means anyone with the source can forge a session
    cookie for any account, so there isn't one.
    """
    from_env = app.config.get("SECRET_KEY")
    if from_env:
        return from_env

    if app.config["IS_PRODUCTION"]:
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
            "and set it in the environment before starting in production."
        )

    key_path = os.path.join(app.instance_path, "secret_key")
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as handle:
            return handle.read().strip()

    key = secrets.token_hex(32)
    with open(key_path, "w", encoding="utf-8") as handle:
        handle.write(key)
    try:
        # Owner read/write only. On Windows this is close to a no-op — NTFS
        # permissions are ACL-based and chmod cannot express them — which is
        # part of why production requires FLASK_SECRET_KEY from the environment
        # rather than relying on this file at all.
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)   # 0600
    except OSError:
        pass
    app.logger.warning("Generated a development secret key at %s", key_path)
    return key


# ----------------------------------------------------------- request hooks

def force_https(_=None):
    if not current_app.config["FORCE_HTTPS"]:
        return None
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        return None
    if request.endpoint == "static":
        return None
    return redirect(request.url.replace("http://", "https://", 1), code=308)


def security_headers(response):
    """Headers that close off framing, sniffing and injected script.

    style-src allows inline because a handful of progress meters set a width
    attribute; script-src does not, which is the one that matters — no injected
    <script> or event handler will run even if something slips past escaping.
    """
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "object-src 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), interest-cohort=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if current_app.config["FORCE_HTTPS"]:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


def session_is_idle() -> bool:
    """True when the session has sat untouched past the idle timeout."""
    limit = current_app.config["IDLE_TIMEOUT_MINUTES"]
    if not limit:
        return False
    last_seen = session.get("_seen")
    now = datetime.now(timezone.utc)
    if last_seen:
        try:
            seen_at = datetime.fromisoformat(last_seen)
        except ValueError:
            return True
        if now - seen_at > timedelta(minutes=limit):
            return True
    session["_seen"] = now.isoformat()
    return False


# ----------------------------------------------------------------- errors

def register_error_handlers(app):
    """Friendly error pages instead of Flask's defaults.

    Every handler renders error.html and returns the matching status code.
    Nothing here echoes anything from the request into the page: an error page
    that repeats what the user sent is a reflected-XSS route, and a 500 that
    shows a traceback hands over file paths and library versions.

    Add a handler for a new status code here rather than try/except in views.
    """
    def render_error(code, heading, message):
        return render_template("error.html", code=code, heading=heading, message=message), code

    @app.errorhandler(400)
    def bad_request(error):
        detail = getattr(error, "description", "")
        if "form expired" in str(detail):
            audit.record(audit.CSRF_REJECT, detail=request.path)
            return render_error(400, "That form expired",
                                "Reload the page and try again. If this keeps happening, "
                                "check that cookies are enabled.")
        return render_error(400, "Bad request", "The server could not read that request.")

    @app.errorhandler(403)
    def forbidden(error):
        return render_error(403, "Not yours", "That belongs to another account.")

    @app.errorhandler(404)
    def not_found(error):
        return render_error(404, "Nothing here",
                            "That page, challenge or session does not exist.")

    @app.errorhandler(413)
    def too_large(error):
        return render_error(413, "Too much data", "That submission was larger than the limit.")

    @app.errorhandler(429)
    def too_many(error):
        return render_error(429, "Slow down",
                            "Too many attempts from this connection. Wait a moment and retry.")

    @app.errorhandler(500)
    def server_error(error):
        # Never leak a traceback to the browser; it goes to the log instead.
        app.logger.exception("Unhandled error on %s", request.path)
        return render_error(500, "Something broke",
                            "The error has been logged. Try again in a moment.")


def register_filters(app):
    """Jinja filters shared by every template.

    Used as {{ row['started_at']|stamp }} and {{ seconds|duration }}. Add
    presentation helpers here rather than formatting in a view — the same value
    then looks the same on every page.
    """
    @app.template_filter("stamp")
    def stamp(value, fallback="—"):
        """UTC string from SQLite to something readable."""
        if not value:
            return fallback
        try:
            return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %H:%M")
        except ValueError:
            return value

    @app.template_filter("duration")
    def duration(seconds, fallback="—"):
        if seconds is None:
            return fallback
        seconds = int(seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"