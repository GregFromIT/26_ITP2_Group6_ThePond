"""Configuration.

Everything here can be overridden with an environment variable so the same code
runs on a laptop and on the Proxmox host without edits.

RANGE_ENV decides how strict the defaults are. In production the app refuses to
start without a real secret key, cookies are secure-only, and HTTPS is enforced.
"""

import os


def _int(name, default):
    return int(os.environ.get(name, default))


def _bool(name, default):
    return os.environ.get(name, "1" if default else "0") == "1"


# ADDING A SETTING:
#   1. add a class attribute below, reading os.environ with a sensible default
#      (use _int/_bool for typed values)
#   2. document it in .env.example
#   3. read it in code with current_app.config["YOUR_SETTING"] — never call
#      os.environ directly from a view, or it cannot be overridden in tests
#
# Anything that is a secret (key, token, password) must have NO usable default.
# Defaults that work are defaults nobody replaces.

ENV = os.environ.get("RANGE_ENV", "development").lower()
IS_PRODUCTION = ENV == "production"


class Config:
    RANGE_ENV = ENV
    IS_PRODUCTION = IS_PRODUCTION

    # --- Flask -----------------------------------------------------------
    # No default key. create_app() loads it from FLASK_SECRET_KEY, falls back to
    # a generated per-instance file in development, and refuses to start in
    # production without one.
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = _bool("COOKIE_SECURE", IS_PRODUCTION)
    SESSION_COOKIE_NAME = "range_session"
    PERMANENT_SESSION_LIFETIME = _int("SESSION_LIFETIME_MINUTES", 720) * 60
    IDLE_TIMEOUT_MINUTES = _int("IDLE_TIMEOUT_MINUTES", 60)

    # Nothing here accepts an upload. Anything large is either a mistake or an
    # attempt to tie up a worker.
    MAX_CONTENT_LENGTH = _int("MAX_CONTENT_BYTES", 64 * 1024)
    MAX_FIELD_LENGTH = _int("MAX_FIELD_LENGTH", 200)

    FORCE_HTTPS = _bool("FORCE_HTTPS", IS_PRODUCTION)
    # Number of reverse proxies in front of the app. 0 means the app is exposed
    # directly and X-Forwarded-For must NOT be trusted — otherwise every client
    # can forge its own source address and walk straight through the throttles.
    TRUSTED_PROXIES = _int("TRUSTED_PROXIES", 0)

    # --- Database --------------------------------------------------------
    DATABASE = os.environ.get("DATABASE_PATH", "instance/cyber_range.sqlite")

    # --- Account policy --------------------------------------------------
    MAX_LOGIN_ATTEMPTS = _int("MAX_LOGIN_ATTEMPTS", 3)
    LOCKOUT_MINUTES = _int("LOCKOUT_MINUTES", 15)      # 0 = admin unlock only
    RESET_TOKEN_HOURS = _int("RESET_TOKEN_HOURS", 24)
    MIN_PASSWORD_LENGTH = _int("MIN_PASSWORD_LENGTH", 12)

    # --- Proxmox ---------------------------------------------------------
    # Defaults are the project's own cluster. Everything is still overridable,
    # so a second cluster needs no code change.
    PROXMOX_BACKEND = os.environ.get("PROXMOX_BACKEND", "simulate")
    PROXMOX_HOST = os.environ.get("PROXMOX_HOST", "10.1.21.151")
    PROXMOX_NODE = os.environ.get("PROXMOX_NODE", "pve")

    # Format is user@realm!tokenname. root@pam!root is what the cluster has
    # today; see the note in the README about moving to a dedicated,
    # least-privilege token before this runs for a cohort.
    PROXMOX_TOKEN_ID = os.environ.get("PROXMOX_TOKEN_ID", "root@pam!root")
    # No default, ever. The secret comes from the environment or nowhere.
    PROXMOX_TOKEN_SECRET = os.environ.get("PROXMOX_TOKEN_SECRET")

    # Where clone disks land. Only consulted for full clones — a linked clone
    # shares the template's disk and inherits its storage.
    PROXMOX_STORAGE = os.environ.get("PROXMOX_STORAGE", "local-lvm")
    PROXMOX_FULL_CLONE = _bool("PROXMOX_FULL_CLONE", False)

    # A cluster on an IP address almost certainly has a self-signed certificate,
    # which fails verification. Either install the cluster CA on this host and
    # leave this on, or set PROXMOX_VERIFY_SSL=0 and understand that anyone on
    # the path between here and the hypervisor can then impersonate it.
    PROXMOX_VERIFY_SSL = _bool("PROXMOX_VERIFY_SSL", IS_PRODUCTION)
    PROXMOX_CLONE_POOL_START = _int("PROXMOX_CLONE_POOL_START", 9000)
