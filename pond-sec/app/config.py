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


def verify_flag(value):
    """PROXMOX_VERIFY_SSL: only an explicit no/off/false/0 turns verification
    off. _bool() would treat "true", "yes" or an empty string as OFF, and a
    security control should fail toward on."""
    if value is None:
        return True
    return value.strip().lower() not in ("0", "false", "no", "off")


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

    # Ordinary forms are tiny, so anything large is a mistake or an attempt to
    # tie up a worker. VM image uploads are the exception and are exempted per
    # request in app/uploads.py, with their own much larger limit.
    MAX_CONTENT_LENGTH = _int("MAX_CONTENT_BYTES", 64 * 1024)

    # Where uploaded VM images are written. Outside the app package and never
    # served over HTTP. Defaults to instance/uploads.
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR")

    # Pasting a URL into the uploads page makes THIS SERVER fetch it, which is
    # server-side request forgery if left unguarded — and this server sits on
    # the same network as Proxmox holding an API token. app/fetcher.py refuses
    # private, loopback, link-local and reserved addresses, and re-checks every
    # redirect hop.
    #
    # Turn this on only if VM images genuinely live on an internal server, and
    # understand what it allows: anyone who can reach the uploads page can then
    # make the platform request any internal address and store the reply.
    # Loopback and link-local stay refused either way.
    UPLOAD_FETCH_ALLOW_PRIVATE = _bool("UPLOAD_FETCH_ALLOW_PRIVATE", False)
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
    MIN_PASSWORD_LENGTH = _int("MIN_PASSWORD_LENGTH", 12)

    # --- Proxmox ---------------------------------------------------------
    # Defaults are the project's own cluster. Everything is still overridable,
    # so a second cluster needs no code change.
    PROXMOX_BACKEND = os.environ.get("PROXMOX_BACKEND", "simulate")
    PROXMOX_HOST = os.environ.get("PROXMOX_HOST", "10.1.21.151")
    PROXMOX_NODE = os.environ.get("PROXMOX_NODE", "pve")

    # Format is user@realm!tokenname, e.g. pond@pve!launcher. There is
    # deliberately NO default: a default that works is a default nobody
    # replaces, and the last one was a root@pam token that could do anything to
    # every VM on the cluster (H3). @pam tokens are refused outright by
    # app/proxmox.py. Create The Pond's own token with
    # playbooks/pond_least_privilege.yml.
    PROXMOX_TOKEN_ID = os.environ.get("PROXMOX_TOKEN_ID")
    # No default, ever. The secret comes from the environment or nowhere.
    PROXMOX_TOKEN_SECRET = os.environ.get("PROXMOX_TOKEN_SECRET")

    # Where clone disks land. Only consulted for full clones — a linked clone
    # shares the template's disk and inherits its storage.
    PROXMOX_STORAGE = os.environ.get("PROXMOX_STORAGE", "local-lvm")
    PROXMOX_FULL_CLONE = _bool("PROXMOX_FULL_CLONE", False)

    # Certificate verification is ON everywhere. Without it anyone on the path to
    # the hypervisor can impersonate it and capture the API token. 0 is refused
    # in production; in development an explicit 0 works but logs a warning on
    # every connect. A self-signed cluster should use PROXMOX_CA_BUNDLE instead.
    PROXMOX_VERIFY_SSL = verify_flag(os.environ.get("PROXMOX_VERIFY_SSL"))
    # Path to a copy of the cluster CA (/etc/pve/pve-root-ca.pem) on this host.
    # When set it is what the connection is verified against, and it wins over
    # PROXMOX_VERIFY_SSL=0.
    PROXMOX_CA_BUNDLE = os.environ.get("PROXMOX_CA_BUNDLE") or None
    # The pool every clone is created in, and the one the API token is scoped
    # to. Not the same thing as PROXMOX_CLONE_POOL_START below, which is only a
    # vmid number for the simulate backend.
    PROXMOX_POOL = os.environ.get("PROXMOX_POOL", "pond-clones")
    PROXMOX_TEMPLATE_POOL = os.environ.get("PROXMOX_TEMPLATE_POOL", "pond-templates")
    # VMs that belong to other projects on the same cluster. The app never
    # clones, starts or deletes these, whatever the token could technically do.
    # What the token may be granted beyond the pools and the session zone. These
    # must match pond_template_bridges / pond_storages in
    # playbooks/pond_least_privilege.yml: the privilege self-check refuses a
    # token holding SDN.Use on any other local bridge (that would let a clone be
    # put back on the lab LAN) or space on any other storage.
    # Comma lists. Bridges default to none (template NICs on a VNet in the zone).
    PROXMOX_TEMPLATE_BRIDGES = os.environ.get("PROXMOX_TEMPLATE_BRIDGES", "")
    PROXMOX_TEMPLATE_STORAGES = os.environ.get("PROXMOX_TEMPLATE_STORAGES", "local-lvm")
    PROXMOX_PROTECTED_VMIDS = os.environ.get("PROXMOX_PROTECTED_VMIDS", "300-303")
    # Seconds a passed privilege self-check is trusted, per worker. 0 = check on
    # every launch.
    PROXMOX_PRIVILEGE_CHECK_TTL = _int("PROXMOX_PRIVILEGE_CHECK_TTL", 300)
    PROXMOX_CLONE_POOL_START = _int("PROXMOX_CLONE_POOL_START", 9000)

    # Every clone is moved onto its own VNet in this SDN zone before it boots
    # (see app/proxmox.py). It must be a Simple zone, created once by hand; the
    # app refuses to launch if it is missing or of another type.
    PROXMOX_SDN_ZONE = os.environ.get("PROXMOX_SDN_ZONE", "pondz")
    # Seconds to wait for a clone, stop or delete task before giving up.
    PROXMOX_TASK_TIMEOUT = _int("PROXMOX_TASK_TIMEOUT", 300)
