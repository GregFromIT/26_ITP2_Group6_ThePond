"""CSRF protection.

A per-session token, required on every state-changing request. Implemented here
rather than pulled in from Flask-WTF so the platform keeps a single dependency,
and so the check is visible in the codebase for the report.

The token is compared with secrets.compare_digest — a plain == leaks position of
the first differing byte through timing.
"""

import secrets

from flask import abort, request, session

FIELD = "_csrf"
HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token() -> str:
    """The token for this session, minted on first use.

    Exposed to Jinja as csrf_token() by init_app(), which is why templates can
    call it directly.

    ADDING A FORM: every <form method="post"> needs

        <input type="hidden" name="_csrf" value="{{ csrf_token() }}">

    or protect() will reject the submission with a 400. If you add a fetch()
    that POSTs, send the token in the X-CSRF-Token header instead.
    """
    """Token for this session, minted on first use."""
    if FIELD not in session:
        session[FIELD] = secrets.token_urlsafe(32)
    return session[FIELD]


def rotate():
    """Discard the current token. Called whenever the session identity changes."""
    session.pop(FIELD, None)


def protect():
    """before_request hook: reject unsafe requests without a matching token.

    Runs on EVERY request before any view. There is deliberately no exemption
    mechanism. If you ever need one (a machine-to-machine API endpoint, say),
    add an explicit allow-list of endpoint names here rather than a decorator
    that is easy to copy onto the wrong view — and authenticate that endpoint
    some other way.
    """
    if request.method not in UNSAFE_METHODS:
        return None

    expected = session.get(FIELD)
    supplied = request.form.get(FIELD) or request.headers.get(HEADER, "")

    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        abort(400, description="Your form expired or came from somewhere else. Reload and try again.")
    return None


def init_app(app):
    app.before_request(protect)
    app.jinja_env.globals["csrf_token"] = csrf_token
